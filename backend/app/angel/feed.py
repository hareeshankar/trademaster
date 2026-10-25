"""Angel One live price feed (SmartAPI WebSocket V2) - one connection shared by every strategy.

Replaces the old app's REST polling every 1.5 s: every trade on the exchange arrives as a tick, so
forming candles and target / stop-loss checks see the real high and low, and REST calls are saved for candles + orders.
"""
import json
import logging
import struct
import threading
import time

import websocket

log = logging.getLogger("trademaster.feed")

URI = "wss://smartapisocket.angelone.in/smart-stream"
EXCHANGE_TYPE = {"NSE": 1, "NFO": 2, "BSE": 3, "BFO": 4, "MCX": 5, "NCX": 7, "CDS": 13}
MODE_QUOTE = 2
MAX_TOKENS = 1000


def parse_tick(b):
    """Binary packet -> dict (LTP / QUOTE / SNAP_QUOTE). Prices arrive in paise -> rupees."""
    if len(b) < 51:
        return None
    mode, ex_type = b[0], b[1]
    token = b[2:27].split(b"\x00", 1)[0].decode(errors="ignore")
    exch_ts, ltp = struct.unpack("<qq", b[35:51])
    div = 10_000_000.0 if ex_type == 13 else 100.0
    t = {"mode": mode, "exchange_type": ex_type, "token": token, "ts": exch_ts / 1000.0, "ltp": ltp / div,
         "recv": time.time()}
    if mode in (2, 3) and len(b) >= 123:
        ltq, atp, vol = struct.unpack("<qqq", b[51:75])
        o, h, l, c = struct.unpack("<qqqq", b[91:123])
        t.update({"volume": vol, "atp": atp / div, "open": o / div, "high": h / div, "low": l / div,
                  "close": c / div})
    return t


class LiveFeed:
    def __init__(self):
        self.session = None
        self.ws = None
        self.thread = None
        self.lock = threading.RLock()
        self.subs = {}              # (ex_type, token) -> set(owner ids)
        self.ticks = {}             # token -> last tick
        self.listeners = {}         # owner -> fn(tick)
        self.connected = False
        self.last_msg = 0.0
        self.error = ""
        self._stop = threading.Event()
        self._sent = set()

    # ------------------------------------------------------------------ lifecycle
    def attach(self, session):
        """Use this logged-in session (called on login and every re-login)."""
        with self.lock:
            self.session = session
        self.restart()

    def restart(self):
        with self.lock:
            if self.ws is not None:
                try:
                    self.ws.close()
                except Exception:
                    pass
            if self.thread is None or not self.thread.is_alive():
                self._stop.clear()
                self.thread = threading.Thread(target=self._run, name="angel-feed", daemon=True)
                self.thread.start()

    def stop(self):
        self._stop.set()
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass

    def _run(self):
        delay = 2
        while not self._stop.is_set():
            s = self.session
            if s is None or not s.jwt:
                time.sleep(1)
                continue
            hdr = {"Authorization": f"Bearer {s.jwt}", "x-api-key": s.api_key,
                   "x-client-code": s.client_id, "x-feed-token": s.feed_token}
            self._sent = set()
            ws = websocket.WebSocketApp(URI, header=hdr, on_open=self._on_open, on_data=self._on_data,
                                        on_error=self._on_error, on_close=self._on_close)
            self.ws = ws
            pinger = threading.Thread(target=self._heartbeat, args=(ws,), daemon=True)
            pinger.start()
            started = time.time()
            try:
                ws.run_forever(ping_interval=0)
            except Exception as ex:
                self.error = f"{type(ex).__name__}: {ex}"
            self.connected = False
            if self._stop.is_set():
                break
            delay = 2 if time.time() - started > 60 else min(delay * 2, 30)
            time.sleep(delay)

    def _heartbeat(self, ws):
        while self.ws is ws and not self._stop.is_set():
            time.sleep(10)
            try:
                if ws.sock and ws.sock.connected:
                    ws.send("ping")
                    if self.connected and self.subs and time.time() - self.last_msg > 45:
                        self.error = "no data for 45 s - reconnecting"
                        ws.close()          # silent socket -> reconnect
                        return
            except Exception:
                return

    def _on_open(self, ws):
        self.connected, self.error, self.last_msg = True, "", time.time()
        self._send_subs()

    def _on_data(self, ws, data, opcode, cont):
        self.last_msg = time.time()
        if opcode != websocket.ABNF.OPCODE_BINARY:
            if isinstance(data, (bytes, bytearray)):
                data = data.decode(errors="ignore")
            if data and data != "pong":
                try:
                    msg = json.loads(data)
                    if msg.get("errorCode") or msg.get("errorMessage"):
                        self.error = f"{msg.get('errorCode')}: {msg.get('errorMessage')}"
                except ValueError:
                    pass
            return
        try:
            t = parse_tick(data)
        except Exception:
            return
        if not t:
            return
        self.ticks[t["token"]] = t
        for fn in list(self.listeners.values()):
            try:
                fn(t)
            except Exception:
                log.exception("tick listener failed")

    def _on_error(self, ws, err):
        self.error = str(err)[:200]

    def _on_close(self, ws, *a):
        self.connected = False

    # ------------------------------------------------------------------ subscriptions
    def _send_subs(self):
        ws = self.ws
        if ws is None or not self.connected:
            return
        with self.lock:
            want = set(self.subs)
            new = want - self._sent
            gone = self._sent - want
        for action, keys in ((1, new), (0, gone)):
            by_ex = {}
            for ex_type, tok in keys:
                by_ex.setdefault(ex_type, []).append(tok)
            if not by_ex:
                continue
            req = {"correlationID": "trademastr", "action": action,
                   "params": {"mode": MODE_QUOTE,
                              "tokenList": [{"exchangeType": e, "tokens": t} for e, t in by_ex.items()]}}
            try:
                ws.send(json.dumps(req))
                with self.lock:
                    self._sent = (self._sent | keys) if action == 1 else (self._sent - keys)
            except Exception as ex:
                self.error = f"subscribe failed: {ex}"

    def subscribe(self, owner, items):
        with self.lock:
            for it in items:
                key = (EXCHANGE_TYPE.get(it["exchange"], 1), str(it["token"]))
                self.subs.setdefault(key, set()).add(owner)
            if len(self.subs) > MAX_TOKENS:
                self.error = f"{len(self.subs)} instruments - Angel allows {MAX_TOKENS} per connection"
        self._send_subs()

    def unsubscribe(self, owner):
        with self.lock:
            for key in list(self.subs):
                self.subs[key].discard(owner)
                if not self.subs[key]:
                    del self.subs[key]
            self.listeners.pop(owner, None)
        self._send_subs()

    def listen(self, owner, fn):
        with self.lock:
            self.listeners[owner] = fn

    def last(self, token):
        return self.ticks.get(str(token))

    def status(self):
        return {"connected": self.connected, "instruments": len(self.subs),
                "last_tick_age": round(time.time() - self.last_msg, 1) if self.last_msg else None,
                "error": self.error}


FEED = LiveFeed()
