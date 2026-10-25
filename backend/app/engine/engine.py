"""One running strategy file.

Flow (same rules as the Streamlit app, with fixes):
  * at every candle close (+4 s) the broker's candles are loaded and the 10 patterns checked on the closed candle;
  * a signal enters at the live price (paper: instantly; live: a BUY LIMIT order a little above LTP);
  * live ticks from the WebSocket move the forming candle and trigger target / stop-loss exits the moment they happen;
  * time exit at square-off once the position has been held `max_hold` trading days.
Live orders are LIMIT only (exchange rule: no market / IOC orders from algos).
"""
import copy
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from .. import markettime as mt
from ..angel.feed import EXCHANGE_TYPE, FEED
from ..indicators import CLOUD_MIN_BARS, cloud_list, cloud_position
from ..models import FUTURES_MARGIN_PCT, Config
from ..patterns import PATTERNS, STORE_KEYS, detect_patterns, pattern_outlook
from ..storage import add_log, fill_slots, load_state, pick_slot, read_json, save_state, write_json
from .candles import fetch_update
from .orders import FINAL_DEAD, FINAL_OK, TRACKER, round_down, round_up

log = logging.getLogger("trademaster.engine")

GRACE = 4                  # seconds after candle close before asking Angel for it
FRESH_SECONDS = 120        # a signal older than this (after its candle closed) is not traded
TICK_STALE = 20            # seconds without a tick -> use REST price as fallback
MAX_EXIT_TRIES = 6


def key_of(p):
    return f"{p['exchange']}:{p['symbol']}"


class Engine:
    def __init__(self, path, broker):
        self.path = path
        self.broker = broker                    # registry Broker: .session (AngelSession or None)
        os.makedirs(path, exist_ok=True)
        self.state_file = os.path.join(path, "live_state.json")
        self.run_file = os.path.join(path, "run_config.json")
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stop_evt = threading.Event()
        self.thread = None
        self.state = load_state(self.state_file)
        self.cfg = None
        self.entries_paused = False
        self._reset_runtime()

    # ================================================================== lifecycle
    def _reset_runtime(self):
        self.cache = {}                 # key -> {"done": df, "b": boundary loaded, "retry_at", "fails", "tries", "cloud"}
        self.forming = {}               # key -> {"b", "open", "high", "low", "close"}
        self.prices = {}                # key -> (ltp, epoch)
        self.rows = []
        self.errors = {}
        self.status = ""
        self.last_update = None
        self.by_token = {}
        self.last_rest_poll = 0.0
        self.last_reconcile = 0.0
        self.loss_block_day = ""
        self.stop_when_flat = False

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive() and not self.stop_evt.is_set()

    @property
    def session(self):
        return self.broker.session

    def owner(self):
        return f"eng:{self.path}"

    def start(self, cfg: Config):
        errs = cfg.validate()
        if errs:
            raise ValueError("; ".join(errs))
        if cfg.mode == "live" and not self.broker.allow_live:
            raise ValueError("Live orders are switched off on the server (TM_ALLOW_LIVE=1 turns them on)")
        if self.session is None:
            raise ValueError("Connect your Angel One account first")
        self.stop(save=False)
        with self.lock:
            self.cfg = cfg
            self.entries_paused = False
            self._reset_runtime()
            for it in cfg.items:
                self.by_token[(EXCHANGE_TYPE.get(it["exchange"], 1), str(it["token"]))] = it
            # open positions from before a restart must also get prices
            for p in self.state["open"]:
                self.by_token.setdefault((EXCHANGE_TYPE.get(p["exchange"], 1), str(p["token"])), p)
            self.status = "Starting..."
            self.stop_evt = threading.Event()
            add_log(self.state, f"STARTED ({cfg.mode.upper()}) - {len(cfg.items)} instruments, {cfg.tf_label}, "
                                f"target {cfg.target_pct}% SL {cfg.sl_pct or 'off'}%, {cfg.size_text}")
            save_state(self.state)
        FEED.listen(self.owner(), self._on_tick)
        FEED.subscribe(self.owner(), list(self.by_token.values()))
        self.thread = threading.Thread(target=self._loop, args=(self.stop_evt,), name=f"eng-{os.path.basename(self.path)}",
                                       daemon=True)
        self.thread.start()
        self._save_run(True)

    def stop(self, save=True):
        self.stop_evt.set()
        self.wake.set()
        t = self.thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=5)
        self.thread = None
        FEED.unsubscribe(self.owner())
        if save:
            with self.lock:
                self.status = "Stopped"
                add_log(self.state, "STOPPED")
                save_state(self.state)
            self._save_run(False)

    def _save_run(self, running):
        if self.cfg is not None:
            write_json(self.run_file, {"running": running, "cfg": self.cfg.to_dict(),
                                       "paused": self.entries_paused})

    def run_info(self):
        return read_json(self.run_file)

    # ================================================================== prices
    def _on_tick(self, t):
        it = self.by_token.get((t["exchange_type"], t["token"]))
        if it is None:
            return
        key = key_of(it)
        ltp = t["ltp"]
        if ltp <= 0:
            return
        now = mt.now_ist()
        with self.lock:
            self.prices[key] = (ltp, time.time())
            cfg = self.cfg
            if cfg is not None and (cfg.ignore_hours or mt.market_open(now, it["exchange"])):
                B = mt.candle_boundary(now, it["exchange"], cfg.tf_min)
                f = self.forming.get(key)
                if f is None or f["b"] != B:
                    f = self.forming[key] = {"b": B, "open": ltp, "high": ltp, "low": ltp, "close": ltp}
                f["high"], f["low"], f["close"] = max(f["high"], ltp), min(f["low"], ltp), ltp
            trigger = False
            for p in self.state["open"]:
                if key_of(p) == key:
                    p["last_price"] = ltp
                    if p.get("status", "OPEN") == "OPEN" and self._exit_reason(p, ltp):
                        trigger = True
        if trigger:
            self.wake.set()

    def price(self, it):
        """(ltp, age seconds) from the feed, else None."""
        v = self.prices.get(key_of(it))
        if v is None:
            t = FEED.last(it["token"])
            if t and t["ltp"] > 0:
                return t["ltp"], time.time() - t["recv"]
            return None
        return v[0], time.time() - v[1]

    def fresh_price(self, it):
        v = self.price(it)
        if v and v[1] <= TICK_STALE:
            return v[0]
        try:
            ltp = self.session.ltp(it)
            with self.lock:
                self.prices[key_of(it)] = (ltp, time.time())
            return ltp
        except Exception as ex:
            self._err("price", f"{it['symbol']}: no live price ({ex})")
            return v[0] if v else None

    def _rest_fallback(self, live_ok):
        """Feed down / silent: refresh prices of open positions + watched items by REST every 5 s."""
        if not live_ok or time.time() - self.last_rest_poll < 5:
            return
        items = [it for it in list(self.by_token.values()) if (self.price(it) or (0, 1e9))[1] > TICK_STALE]
        if not items:
            return
        self.last_rest_poll = time.time()
        try:
            got = self.session.quotes(items[:250], "LTP")
        except Exception as ex:
            self._err("price", f"REST price fallback failed: {ex}")
            return
        for it in items:
            q = got.get(str(it["token"]))
            if q and q.get("ltp"):
                self._on_tick({"exchange_type": EXCHANGE_TYPE.get(it["exchange"], 1), "token": str(it["token"]),
                               "ltp": float(q["ltp"])})

    # ================================================================== loop
    def _err(self, kind, msg):
        with self.lock:
            self.errors[kind] = msg

    def _loop(self, evt):
        pool = ThreadPoolExecutor(max_workers=3)
        try:
            while not evt.is_set():
                t0 = time.time()
                try:
                    self._cycle(evt, pool)
                except Exception as ex:
                    log.exception("cycle failed")
                    self._err("engine", f"engine error: {type(ex).__name__}: {ex}")
                self.wake.wait(max(0.2, 1.0 - (time.time() - t0)))
                self.wake.clear()
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def _cycle(self, evt, pool):
        cfg = self.cfg
        if cfg is None or self.session is None:
            self.status = "Waiting for Angel One login"
            return
        if self.stop_when_flat and not self.state["open"]:
            self.stop(save=True)            # Exit all finished: every sell order filled
            return
        now = mt.now_ist()
        live_ok = cfg.ignore_hours or mt.market_open(now, cfg.exchange)
        tf = pd.Timedelta(minutes=cfg.tf_min)
        B = mt.candle_boundary(now, cfg.exchange, cfg.tf_min)

        # 1) orders waiting at the broker (live) - fills, rejections, timeouts
        self._manage_orders(now)
        # 2) exits: target / stop-loss / time
        self._check_exits(now, live_ok)
        # 3) candles + new entries at candle close
        due = [it for it in cfg.items if self._candle_due(key_of(it), B, now, live_ok)]
        if due:
            done_n = [0]

            def load_one(it):
                if evt.is_set():
                    return
                try:
                    self._load_candles(it, B, tf, live_ok)
                except Exception as ex:
                    self._err(f"c:{it['symbol']}", f"{it['symbol']}: {ex}")
                done_n[0] += 1
                self.status = f"Loading candles {done_n[0]}/{len(due)}"

            list(pool.map(load_one, due))
        self._rest_fallback(live_ok)
        # 4) broker check (live) every 5 minutes
        if cfg.mode == "live" and time.time() - self.last_reconcile > 300:
            self.last_reconcile = time.time()
            self._reconcile()
        self._build_rows(B, tf, live_ok)

    # ================================================================== candles + entries
    def _candle_due(self, key, B, now, live_ok):
        c = self.cache.get(key)
        if c is None:
            return True
        if c.get("retry_at") is not None:
            return time.time() >= c["retry_at"]
        if c.get("b") == B or not live_ok:
            return False
        return now >= B + pd.Timedelta(seconds=GRACE)

    def _load_candles(self, it, B, tf, live_ok):
        cfg, key = self.cfg, key_of(it)
        prev = self.cache.get(key, {})
        try:
            df = fetch_update(self.session, it, cfg.interval, cfg.tf_min, prev.get("done"), mt.now_ist(),
                              bool(cfg.cloud_filter))
        except Exception:
            df = None
        if df is None or df.empty:
            fails = prev.get("fails", 0) + 1
            with self.lock:
                self.cache[key] = {**prev, "b": None if fails < 3 else B, "fails": fails if fails < 3 else 0,
                                   "retry_at": time.time() + 20 if fails < 3 else None}
            raise RuntimeError(f"no candle data (try {fails})")
        nowf = mt.now_ist()
        ends = df["date"].map(lambda t: mt.candle_end(t, tf, it["exchange"]))
        done = df[ends <= nowf].reset_index(drop=True)
        tries = prev.get("tries", 0) + 1
        # Angel sometimes publishes the just-closed candle a few seconds late -> retry (max 3 times)
        lag = (tf < pd.Timedelta(minutes=240) and live_ok and not done.empty
               and done["date"].iat[-1] < B - tf and tries < 3)
        with self.lock:
            self.cache[key] = {"b": None if lag else B, "done": done, "tries": tries if lag else 0,
                               "retry_at": time.time() + 3 if lag else None,
                               "cloud": cloud_position(done) if cfg.cloud_filter else None, "fails": 0}
            self.errors.pop(f"c:{it['symbol']}", None)
        if not lag:
            self._try_entry(it, done)

    def _today(self, now):
        return self.state["day"].setdefault(f"{now:%Y-%m-%d}", {"entries": 0})

    def _today_pnl(self, now):
        d = f"{now:%Y-%m-%d}"
        realized = sum(float(t.get("pnl") or 0) for t in self.state["closed"] if str(t.get("exit_time", ""))[:10] == d)
        unreal = sum((float(p.get("last_price") or p["entry"]) - p["entry"]) * p["qty"]
                     for p in self.state["open"] if p.get("status", "OPEN") != "PENDING_ENTRY")
        return realized + unreal

    def _blocked(self, it, now, manual=False):
        cfg, st = self.cfg, self.state
        ex, sym = it["exchange"], it["symbol"]
        if not cfg.ignore_hours and now.time() >= mt.square_off_time(ex) and cfg.product == "intraday":
            return f"no new entries after {mt.square_off_time(ex):%H:%M} (square-off time)"
        if any(p["exchange"] == ex and p["symbol"] == sym for p in st["open"]):
            return "already in position"
        if not cfg.no_limit and len(st["open"]) >= cfg.max_open:
            return f"positions full {len(st['open'])}/{cfg.max_open}"
        if self.entries_paused and not manual:
            return "entries paused"
        if self._today(now)["entries"] >= cfg.max_orders_day:
            return f"daily entry cap {cfg.max_orders_day} reached"
        if cfg.daily_loss_limit > 0 and self._today_pnl(now) <= -cfg.daily_loss_limit:
            if self.loss_block_day != f"{now:%Y-%m-%d}":
                self.loss_block_day = f"{now:%Y-%m-%d}"
                add_log(st, f"DAILY LOSS LIMIT hit ({cfg.daily_loss_limit:,.0f}) - no new entries today")
            return "daily loss limit hit"
        return None

    def _skip(self, rec, key, why):
        st = self.state
        st["handled"] = (st["handled"] + [key])[-3000:]
        rec["status"] = f"SKIPPED ({why})"
        st["signals"] = (st["signals"] + [rec])[-300:]
        add_log(st, f"SKIPPED {rec['symbol']}: {why}  ({rec['pattern']})")
        save_state(st)

    def _try_entry(self, it, done):
        cfg = self.cfg
        now = mt.now_ist()
        ex, sym = it["exchange"], it["symbol"]
        if not (cfg.ignore_hours or mt.market_open(now, ex)):
            return
        if len(done) < cfg.trend_lookback + 3:
            return
        sig = detect_patterns(done, cfg.trend_lookback)
        names = [p for p in cfg.patterns if p in sig.columns and bool(sig[p].iat[-1])]
        if not names:
            return
        tf = pd.Timedelta(minutes=cfg.tf_min)
        ctime = done["date"].iat[-1]
        closed_at = mt.candle_end(ctime, tf, ex)
        if tf >= pd.Timedelta(minutes=240):
            if not cfg.ignore_hours and now - closed_at > pd.Timedelta(days=4):
                return
        elif not cfg.ignore_hours and now - closed_at > min(2 * tf, pd.Timedelta(seconds=FRESH_SECONDS)):
            return
        key = f"{ex}:{sym}|{ctime}"
        with self.lock:
            if key in self.state["handled"]:
                return
        pv, sg = done.iloc[-2], done.iloc[-1]
        candles = {f"{p}_{k}": round(float(c[n]), 2) for p, c in (("prev", pv), ("sig", sg))
                   for k, n in (("O", "open"), ("H", "high"), ("L", "low"), ("C", "close"))}
        hist = [[round(float(x[n]), 4) for n in ("open", "high", "low", "close")] for _, x in done.tail(5).iterrows()]
        rec = {"time": str(now.floor("s")), "symbol": sym, "exchange": ex, "pattern": "+".join(names),
               "candle": str(ctime), **candles, "hist": hist, "status": "ENTERED"}
        if cfg.cloud_filter:
            cpos = cloud_position(done)
            rec["cloud"] = cpos or "n/a"
            if cpos not in cloud_list(cfg.cloud_filter):
                with self.lock:
                    self._skip(rec, key, f"cloud {cpos or 'not ready, need ' + str(CLOUD_MIN_BARS) + ' candles'}"
                                         f" - filter needs {' / '.join(cloud_list(cfg.cloud_filter))}")
                return
        with self.lock:
            b = self._blocked(it, now)
            if b:
                self._skip(rec, key, b)
                return
        price = self.fresh_price(it) or float(done["close"].iat[-1])
        with self.lock:
            b = self._blocked(it, now)
            if b:
                self._skip(rec, key, b)
                return
            self.state["handled"] = (self.state["handled"] + [key])[-3000:]
            self._enter(it, rec, ctime, price, now)

    def _cash(self):
        st, cfg = self.state, self.cfg
        used = sum(p["entry"] * p["qty"] * p.get("mf", 1.0) for p in st["open"])
        return cfg.capital + sum(float(t.get("pnl") or 0) for t in st["closed"]) - used

    def _enter(self, it, rec, ctime, price, now):
        """Create the position. Paper: filled now. Live: BUY LIMIT order sent, position waits for the fill."""
        cfg, st = self.cfg, self.state
        ex, sym = it["exchange"], it["symbol"]
        tf = pd.Timedelta(minutes=cfg.tf_min)
        lot = int(it.get("lot", 1))
        mf = FUTURES_MARGIN_PCT / 100 if ex in ("NFO", "MCX") else 1.0
        live = cfg.mode == "live"
        tick = float(it.get("tick") or 0.05)
        entry = round_up(price * (1 + cfg.entry_buffer_pct / 100), tick) if live else price * (1 + cfg.slippage_pct / 100)
        cash = self._cash()
        if live:
            avail = self.broker.available_cash()
            if avail is not None:
                cash = min(cash, avail)
        one_lot = entry * lot * mf
        qty = cfg.size_qty(one_lot, lot, cash)
        if qty < 1:
            unit = "share" if ex in ("NSE", "BSE") else "lot"
            rec["status"] = f"SKIPPED (1 {unit} needs {one_lot:,.0f}; {cfg.size_text}, free cash {cash:,.0f})"
            add_log(st, f"SKIPPED {sym}: 1 {unit} needs {one_lot:,.0f} but {cfg.size_text}, free cash {cash:,.0f}")
            st["signals"] = (st["signals"] + [rec])[-300:]
            save_state(st)
            return
        slot, replaced = pick_slot(st, now)
        pos = {
            "slot": slot, "symbol": sym, "tradingsymbol": it["tradingsymbol"], "token": str(it["token"]),
            "exchange": ex, "lot": lot, "tick": tick, "mode": cfg.mode,
            "pattern": rec["pattern"], "signal_date": str(ctime), "signal_close": str(mt.candle_end(ctime, tf, ex)),
            "entry_time": str(now.floor("s")), "entry": round(entry, 2), "qty": int(qty), "mf": mf,
            "last_price": price, "status": "OPEN",
            **{k: rec[k] for k in STORE_KEYS if k in rec},
        }
        self._set_levels(pos)
        if live:
            product = cfg.broker_product(ex)
            try:
                oid = self.session.place_limit(it, "BUY", qty, entry, product)
            except Exception as ex_:
                rec["status"] = f"ORDER FAILED ({ex_})"
                add_log(st, f"BUY ORDER FAILED {sym}: {ex_}")
                st["signals"] = (st["signals"] + [rec])[-300:]
                save_state(st)
                return
            pos.update({"status": "PENDING_ENTRY", "order_id": oid, "order_time": time.time(), "product": product,
                        "order_price": entry, "order_status": "sent"})
            self._order_log(oid, "BUY", sym, qty, entry, "sent", rec["pattern"])
            rec["status"] = f"ORDER SENT #{oid}"
        st["open"].append(pos)
        self._today(now)["entries"] += 1
        where = f"position #{slot}" + ("" if cfg.no_limit else f"/{cfg.max_open}")
        if replaced:
            where += f", took the place of {replaced['symbol']} ({replaced['reason']} {str(replaced['exit_time'])[11:]})"
        verb = "BUY ORDER" if live else "ENTRY"
        sl_txt = f"  SL {pos['stop_loss']:.2f}" if pos.get("stop_loss") else ""
        add_log(st, f"{verb} {sym} @ {entry:.2f}  qty {qty}  TGT {pos['target']:.2f}{sl_txt}  ({rec['pattern']})  -> {where}")
        st["signals"] = (st["signals"] + [rec])[-300:]
        save_state(st)

    def _set_levels(self, pos):
        cfg = self.cfg
        e = pos["entry"]
        tick = pos.get("tick") or 0.05
        pos["target"] = round_up(e * (1 + cfg.target_pct / 100), tick)
        pos["stop_loss"] = round_down(e * (1 - cfg.sl_pct / 100), tick) if cfg.sl_pct > 0 else None

    def _order_log(self, oid, side, sym, qty, price, status, note=""):
        self.state["orders"] = (self.state["orders"] + [{
            "time": str(mt.now_ist().floor("s")), "order_id": oid, "side": side, "symbol": sym, "qty": qty,
            "price": round(price, 2), "status": status, "note": note}])[-500:]

    def _order_update(self, oid, **kw):
        for o in reversed(self.state["orders"]):
            if o["order_id"] == oid:
                o.update(kw)
                return

    # ================================================================== manual actions
    def manual_buy(self, it):
        cfg = self.cfg
        if cfg is None or not self.running:
            return False, "Start the strategy first"
        now = mt.now_ist()
        if not (cfg.ignore_hours or mt.market_open(now, it["exchange"])):
            return False, "Market is closed"
        with self.lock:
            b = self._blocked(it, now, manual=True)
        if b:
            return False, f"Not bought: {b}"
        price = self.fresh_price(it)
        if price is None:
            return False, "No live price right now - try again in a moment"
        rec = {"time": str(now.floor("s")), "symbol": it["symbol"], "exchange": it["exchange"],
               "pattern": "Manual BUY", "candle": str(now.floor("min")), "status": "ENTERED"}
        with self.lock:
            b = self._blocked(it, now, manual=True)
            if b:
                return False, f"Not bought: {b}"
            if not any(key_of(x) == key_of(it) for x in self.cfg.items):
                self.by_token[(EXCHANGE_TYPE.get(it["exchange"], 1), str(it["token"]))] = it
                FEED.subscribe(self.owner(), [it])
            self._enter(it, rec, now.floor("min"), price, now)
        return rec["status"].startswith(("ENTERED", "ORDER SENT")), rec["status"]

    def manual_buy_all(self):
        ok, why = 0, []
        for it in list(self.cfg.items if self.cfg else []):
            if not self.running:
                break
            done, msg = self.manual_buy(it)
            if done:
                ok += 1
                continue
            why.append(f"{it['symbol']}: {msg}")
            if any(x in msg for x in ("positions full", "Market is closed", "Start the strategy", "cap", "loss limit")):
                break
        with self.lock:
            add_log(self.state, f"BUY ALL: {ok} entered" + (f" - {'; '.join(why[:3])}" if why else ""))
            save_state(self.state)
        return ok

    def exit_selected(self, keys, reason="Manual exit"):
        keys = set(keys)
        n = 0
        with self.lock:
            for p in list(self.state["open"]):
                if key_of(p) in keys and p.get("status", "OPEN") in ("OPEN", "STUCK"):
                    if p.get("status") == "STUCK":
                        p["status"], p["exit_tries"] = "OPEN", 0
                    self._exit(p, reason, p.get("last_price") or p["entry"])
                    n += 1
                elif key_of(p) in keys and p.get("status") == "PENDING_ENTRY":
                    self._cancel_entry(p, "cancelled by you")
                    n += 1
            save_state(self.state)
        return n

    def _ensure_loop_for_exits(self):
        """Live SELL orders need the loop running to follow their fills - start it (entries paused) if needed."""
        if self.running or not any(p.get("mode") == "live" for p in self.state["open"]):
            return
        cfg = self.cfg or Config.from_dict((self.run_info().get("cfg")) or read_json(os.path.join(self.path, "config.json")))
        if not cfg.items:
            cfg.items = [{k: p[k] for k in ("symbol", "tradingsymbol", "token", "exchange", "lot", "tick") if k in p}
                         for p in self.state["open"]]
        self.start(cfg)
        self.entries_paused = True

    def exit_all(self, reason="Manual exit (Exit all)"):
        """Close every position. Paper closes instantly; live sends SELL orders and keeps running until filled."""
        with self.lock:
            keys = [key_of(p) for p in self.state["open"]]
        if keys:
            self._ensure_loop_for_exits()
        self.entries_paused = True
        n = self.exit_selected(keys, reason)
        if self.cfg is None or self.cfg.mode == "paper" or not self.state["open"]:
            self.stop()
        else:
            add_log(self.state, "EXIT ALL - entries stopped, waiting for sell orders to fill")
            self.stop_when_flat = True
            self._save_run(True)
        return n

    def pause(self, on=True):
        self.entries_paused = on
        with self.lock:
            add_log(self.state, "ENTRIES PAUSED (exits still managed)" if on else "ENTRIES RESUMED")
            save_state(self.state)
        self._save_run(self.running)

    def update_config(self, cfg: Config):
        errs = cfg.validate()
        if errs:
            raise ValueError("; ".join(errs))
        with self.lock:
            old = self.cfg
            if old is not None and old.mode != cfg.mode and self.state["open"]:
                raise ValueError("Close open positions before switching paper / live")
            self.cfg = cfg
            if old is not None and (cfg.cloud_filter != old.cloud_filter or cfg.interval != old.interval):
                self.cache = {}
            new = {(EXCHANGE_TYPE.get(it["exchange"], 1), str(it["token"])): it for it in cfg.items}
            for p in self.state["open"]:
                new.setdefault((EXCHANGE_TYPE.get(p["exchange"], 1), str(p["token"])), p)
            self.by_token = new
            for p in self.state["open"]:                     # new target / SL apply to open positions too
                if p.get("status", "OPEN") == "OPEN":
                    self._set_levels(p)
            add_log(self.state, "SETTINGS CHANGED while running")
            save_state(self.state)
        if self.running:
            FEED.unsubscribe(self.owner())
            FEED.listen(self.owner(), self._on_tick)
            FEED.subscribe(self.owner(), list(self.by_token.values()))
        self._save_run(self.running)

    def delete_position(self, key):
        """Remove an open PAPER position without a record."""
        with self.lock:
            p = next((x for x in self.state["open"] if key_of(x) == key), None)
            if p is None:
                return False
            if p.get("mode") == "live":
                raise ValueError("Live positions can't be deleted - exit them (the shares are real)")
            self.state["open"].remove(p)
            save_state(self.state)
            return True

    def delete_closed(self, idx, expect=None):
        keys = ("symbol", "entry_time", "exit_time")
        with self.lock:
            c = self.state["closed"]
            if expect is not None and not (0 <= idx < len(c) and all(c[idx].get(k) == expect.get(k) for k in keys)):
                idx = next((i for i, t in enumerate(c) if all(t.get(k) == expect.get(k) for k in keys)), -1)
            if 0 <= idx < len(c):
                c.pop(idx)
                save_state(self.state)
                return True
        return False

    def reset_all(self):
        if any(p.get("mode") == "live" for p in self.state["open"]):
            raise ValueError("Live positions are open - exit them first")
        self.stop(save=False)
        with self.lock:
            if os.path.exists(self.state_file):
                os.remove(self.state_file)
            self.state = load_state(self.state_file)
            self._reset_runtime()
        if os.path.exists(self.run_file):
            os.remove(self.run_file)

    # ================================================================== exits
    def _exit_reason(self, p, ltp):
        if ltp >= p["target"]:
            return "Target"
        if p.get("stop_loss") and ltp <= p["stop_loss"]:
            return "SL"
        return None

    def _check_exits(self, now, live_ok):
        cfg = self.cfg
        with self.lock:
            for p in list(self.state["open"]):
                if p.get("status", "OPEN") != "OPEN":
                    continue
                ltp = p.get("last_price")
                if not live_ok or ltp is None:
                    continue
                why = self._exit_reason(p, ltp)
                if why:
                    self._exit(p, why, ltp)
                    continue
                held = mt.trading_days_held(p["entry_time"][:10], now, p["exchange"])
                # test mode ("ignore market hours"): the old app time-exited every position at once; now it waits a day
                due = held > cfg.max_hold if cfg.ignore_hours else held >= cfg.max_hold
                if due and now.time() >= mt.square_off_time(p["exchange"]):
                    self._exit(p, "Time exit", ltp)

    def _exit(self, p, reason, ltp):
        """Paper: close now. Live: SELL LIMIT a little below LTP; position waits in PENDING_EXIT for the fill."""
        cfg = self.cfg or Config()
        if p.get("mode", "paper") != "live":
            if reason == "Target" and ltp >= p["target"]:
                fill = p["target"]                # limit order logic: you get at least the target price
            else:
                fill = ltp
            self._close(p, fill * (1 - cfg.slippage_pct / 100), reason, brokerage=True)
            return
        tick = p.get("tick") or 0.05
        px = round_down(ltp * (1 - cfg.exit_buffer_pct / 100), tick)
        qty = p["qty"] - int(p.get("exit_filled", 0))
        try:
            oid = self.session.place_limit(p, "SELL", qty, px, p.get("product") or cfg.broker_product(p["exchange"]))
        except Exception as ex:
            p["exit_fail"] = int(p.get("exit_fail", 0)) + 1
            add_log(self.state, f"SELL ORDER FAILED {p['symbol']} ({reason}): {ex}"
                                f"{' - CHECK THE BROKER APP' if p['exit_fail'] >= 3 else ' - will retry'}")
            self._err(f"x:{p['symbol']}", f"{p['symbol']}: sell order failed ({ex})")
            return
        p.update({"status": "PENDING_EXIT", "exit_order_id": oid, "exit_reason": reason, "exit_order_time": time.time(),
                  "exit_price_sent": px, "exit_tries": int(p.get("exit_tries", 0)) + 1, "order_status": "sell sent"})
        self._order_log(oid, "SELL", p["symbol"], qty, px, "sent", reason)
        add_log(self.state, f"SELL ORDER {p['symbol']} qty {qty} @ {px:.2f}  ({reason}, LTP {ltp:.2f})")

    def _close(self, p, exit_px, reason, brokerage=False, when=None):
        cfg = self.cfg or Config()
        st = self.state
        qty = p["qty"]
        pnl = (exit_px - p["entry"]) * qty
        if brokerage:
            pnl -= cfg.brokerage_pct / 100 * (p["entry"] + exit_px) * qty
        risk = (p["entry"] - p["stop_loss"]) * qty if p.get("stop_loss") else 0
        done = {k: p.get(k) for k in ("symbol", "exchange", "pattern", "signal_date", "signal_close", "entry_time",
                                      "entry", "stop_loss", "target", "qty", "mode", "order_id", "exit_order_id")}
        done.update({k: p[k] for k in STORE_KEYS if k in p})
        done.update({"exit_time": str(pd.Timestamp(when or mt.now_ist()).floor("s")), "exit": round(exit_px, 2),
                     "reason": reason, "pnl": round(pnl, 2), "r_multiple": round(pnl / risk, 2) if risk > 0 else None})
        fill_slots(st)
        slot = p.get("slot")
        done["slot"] = slot
        st["closed"].append(done)
        st["open"] = [x for x in st["open"] if x is not p]
        if slot:
            st.setdefault("slot_last", {})[str(slot)] = {"symbol": p["symbol"], "reason": reason,
                                                         "exit_time": done["exit_time"]}
        add_log(st, f"CLOSED {p['symbol']} @ {exit_px:.2f}  {reason}  P&L {pnl:,.2f}"
                    f"{f'  (position #{slot} is free now)' if slot else ''}")
        save_state(st)

    def _cancel_entry(self, p, why):
        try:
            self.session.cancel(p["order_id"])
            p["cancel_sent"] = True
            p["cancel_why"] = why
            add_log(self.state, f"CANCEL BUY {p['symbol']} #{p['order_id']} ({why})")
        except Exception as ex:
            add_log(self.state, f"CANCEL FAILED {p['symbol']} #{p['order_id']}: {ex}")

    # ================================================================== live order follow-up
    def _manage_orders(self, now):
        with self.lock:
            pend = [p for p in self.state["open"] if p.get("status") in ("PENDING_ENTRY", "PENDING_EXIT")]
        if not pend:
            return
        cfg = self.cfg
        for p in pend:
            oid = p["order_id"] if p["status"] == "PENDING_ENTRY" else p["exit_order_id"]
            o = TRACKER.get(self.session, oid)
            if o is None:
                continue
            with self.lock:
                if p not in self.state["open"]:
                    continue
                st = o["status"]
                p["order_status"] = st + (f" ({o['text']})" if o["text"] and st in FINAL_DEAD else "")
                self._order_update(oid, status=st, filled=o["filled"], avg=o["avg"], text=o["text"])
                if p["status"] == "PENDING_ENTRY":
                    self._follow_entry(p, o, cfg)
                else:
                    self._follow_exit(p, o, cfg)
                save_state(self.state)

    def _follow_entry(self, p, o, cfg):
        st = o["status"]
        if st in FINAL_OK or (st in FINAL_DEAD and o["filled"] > 0):
            p["qty"] = o["filled"] or p["qty"]
            p["entry"] = round(o["avg"] or p["order_price"], 2)
            p["status"] = "OPEN"
            p["entry_time"] = str(mt.now_ist().floor("s"))
            self._set_levels(p)
            add_log(self.state, f"BOUGHT {p['symbol']} qty {p['qty']} @ {p['entry']:.2f}  TGT {p['target']:.2f}"
                                f"{'  SL %.2f' % p['stop_loss'] if p.get('stop_loss') else ''}  (order #{p['order_id']})")
            return
        if st in FINAL_DEAD:
            self.state["open"].remove(p)
            add_log(self.state, f"BUY NOT FILLED {p['symbol']}: {st} {o['text']}".strip())
            for g in reversed(self.state["signals"]):
                if g.get("symbol") == p["symbol"] and "ORDER SENT" in str(g.get("status")):
                    g["status"] = f"NOT FILLED ({st}{': ' + o['text'] if o['text'] else ''})"
                    break
            return
        if time.time() - p.get("order_time", 0) > cfg.order_timeout_s and not p.get("cancel_sent"):
            self._cancel_entry(p, f"not filled in {cfg.order_timeout_s}s")

    def _follow_exit(self, p, o, cfg):
        st = o["status"]
        if st in FINAL_OK:
            filled_before = int(p.get("exit_filled", 0))
            avg_before = float(p.get("exit_avg", 0))
            total = filled_before + o["filled"]
            avg = ((avg_before * filled_before) + o["avg"] * o["filled"]) / total if total else o["avg"]
            self._close(p, avg, p.get("exit_reason", "Exit"))
            return
        if st in FINAL_DEAD:
            if o["filled"] > 0:          # partly sold, rest cancelled/rejected -> remember and sell the rest
                fb = int(p.get("exit_filled", 0))
                p["exit_avg"] = ((float(p.get("exit_avg", 0)) * fb) + o["avg"] * o["filled"]) / (fb + o["filled"])
                p["exit_filled"] = fb + o["filled"]
            p["status"] = "OPEN"
            if int(p.get("exit_tries", 0)) >= MAX_EXIT_TRIES:
                p["exit_stuck"] = True
                add_log(self.state, f"EXIT STUCK {p['symbol']} after {MAX_EXIT_TRIES} tries ({o['text']}) - "
                                    f"SELL IT IN THE ANGEL APP")
                self._err(f"x:{p['symbol']}", f"{p['symbol']}: exit stuck - sell manually in Angel One")
                p["status"] = "STUCK"
                return
            add_log(self.state, f"SELL {st} {p['symbol']} ({o['text']}) - sending again")
            self._exit(p, p.get("exit_reason", "Exit"), p.get("last_price") or p["entry"])
            return
        # still open at the exchange: re-price lower after the timeout (chase the price, LIMIT only)
        if time.time() - p.get("exit_order_time", 0) > cfg.order_timeout_s:
            ltp = p.get("last_price") or p["entry"]
            tick = p.get("tick") or 0.05
            tries = int(p.get("exit_tries", 1))
            px = round_down(ltp * (1 - cfg.exit_buffer_pct * (tries + 1) / 100), tick)
            try:
                self.session.modify_limit(p["exit_order_id"], p, o["unfilled"] or p["qty"], px,
                                          p.get("product") or cfg.broker_product(p["exchange"]))
                p.update({"exit_order_time": time.time(), "exit_tries": tries + 1, "exit_price_sent": px})
                add_log(self.state, f"SELL RE-PRICED {p['symbol']} -> {px:.2f} (try {tries + 1})")
            except Exception as ex:
                p["exit_order_time"] = time.time()
                add_log(self.state, f"SELL RE-PRICE FAILED {p['symbol']}: {ex}")

    def _reconcile(self):
        """Compare our live positions with Angel's position book; flag differences (never auto-trades)."""
        try:
            book = self.session.positions()
        except Exception as ex:
            self._err("reconcile", f"broker position check failed: {ex}")
            return
        net = {}
        for r in book:
            try:
                net[(r.get("exchange"), str(r.get("symboltoken")))] = net.get(
                    (r.get("exchange"), str(r.get("symboltoken"))), 0) + int(float(r.get("netqty") or 0))
            except (TypeError, ValueError):
                continue
        warn = []
        with self.lock:
            for p in self.state["open"]:
                if p.get("mode") != "live" or p.get("status") != "OPEN":
                    continue
                have = net.get((p["exchange"], str(p["token"])), None)
                if p.get("product") == "DELIVERY" and have in (None, 0):
                    continue                         # delivery shares move to holdings next day
                if have is None or have < p["qty"]:
                    warn.append(f"{p['symbol']}: app {p['qty']} vs broker {have or 0}")
        if warn:
            self._err("reconcile", "Broker mismatch - " + "; ".join(warn))
        else:
            with self.lock:
                self.errors.pop("reconcile", None)

    # ================================================================== monitoring table
    def _build_rows(self, B, tf, live_ok):
        cfg = self.cfg
        with self.lock:
            open_keys = {key_of(p): p.get("status", "OPEN") for p in self.state["open"]}
            rows = []
            for it in cfg.items:
                key = key_of(it)
                c, f = self.cache.get(key) or {}, self.forming.get(key)
                pv = self.price(it)
                ltp = pv[0] if pv else None
                done = c.get("done")
                row = {"symbol": it["symbol"], "ltp": ltp, "open": None, "high": None, "low": None,
                       "prev_close": None, "change_pct": None,
                       "position": {"OPEN": "IN POSITION", "PENDING_ENTRY": "BUYING", "PENDING_EXIT": "SELLING",
                                    "STUCK": "EXIT STUCK"}.get(open_keys.get(key), "-"),
                       "cloud": (c.get("cloud") or "-") if cfg.cloud_filter else None,
                       "outlook": ["Loading candles..."] if done is None else pattern_outlook(done, cfg.patterns,
                                                                                         cfg.trend_lookback)}
                if done is not None and len(done):
                    pc = float(done["close"].iat[-1])
                    if f and f["b"] == B and live_ok:
                        row.update({"open": f["open"], "high": f["high"], "low": f["low"]})
                    row["prev_close"] = round(pc, 2)
                    if ltp:
                        row["change_pct"] = round((ltp / pc - 1) * 100, 2)
                rows.append(row)
            self.rows, self.last_update = rows, mt.now_ist()
            self.status = (f"Waiting for the next candle ({(B + tf):%H:%M})" if live_ok
                           else "Market closed - prices only, no entries / exits")
            if self.entries_paused:
                self.status += " - ENTRIES PAUSED"

    def snapshot(self):
        with self.lock:
            st = copy.deepcopy({k: v for k, v in self.state.items() if not k.startswith("_")})
            return {"state": st, "rows": list(self.rows), "errors": list(self.errors.values()),
                    "status": self.status, "running": self.running, "paused": self.entries_paused,
                    "last_update": str(self.last_update) if self.last_update is not None else None,
                    "cfg": self.cfg.to_dict() if self.cfg else None}

    def resume_from_disk(self):
        rc = self.run_info()
        if not rc.get("running") or self.running:
            return False
        cfg = Config.from_dict(rc.get("cfg") or {})
        self.start(cfg)
        if rc.get("paused"):
            self.entries_paused = True
        with self.lock:
            add_log(self.state, f"AUTO-RESUMED after restart - {len(self.state['open'])} open position(s) kept")
            save_state(self.state)
        return True


def dumps(o):
    return json.dumps(o, default=str)
