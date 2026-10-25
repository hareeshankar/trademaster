"""TradeMaster API (FastAPI). Run:  uvicorn app.main:app --host 127.0.0.1 --port 8000"""
import csv
import io
import logging
import os
import threading

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from . import auth
from . import markettime as mt
from .angel import instruments as ins
from .angel.feed import FEED
from .engine.orders import TRACKER, norm_order
from .engine.registry import BROKER
from .indicators import CLOUD_OPTIONS
from .models import EXCHANGES, TIMEFRAMES, Config
from .patterns import PATTERN_DEFS, PATTERNS, classify_candles, excel_check_rows
from .settings import ALLOW_LIVE, CORS_ORIGINS
from . import storage
from . import seed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = FastAPI(title="TradeMaster", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup():
    auth.ensure_owner()
    threading.Thread(target=BROKER.auto_login, daemon=True).start()
    seed.seed_in_background()


def need_owner(authorization: str = Header(default="")):
    tok = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not auth.verify_token(tok):
        raise HTTPException(401, "Please log in")
    return True


def need_session():
    if BROKER.session is None:
        raise HTTPException(409, "Connect your Angel One account first")
    return BROKER.session


def bad(msg, code=400):
    raise HTTPException(code, str(msg))


# ====================================================================== auth
class LoginIn(BaseModel):
    password: str


@app.get("/api/health")
def health():
    return {"ok": True, "time": str(mt.now_ist()), "needs_setup": auth.needs_setup()}


@app.post("/api/auth/setup")
def first_setup(body: LoginIn):
    """First visit only: the owner chooses the password (works once, while no password exists)."""
    if not auth.needs_setup():
        bad("Password already set - log in instead", 409)
    if len(body.password) < 10:
        bad("Use at least 10 characters")
    auth.set_password(body.password)
    return {"token": auth.make_token()}


@app.post("/api/auth/login")
def login(body: LoginIn):
    try:
        if not auth.check_password(body.password):
            bad("Wrong password", 401)
    except PermissionError as ex:
        bad(ex, 429)
    return {"token": auth.make_token()}


class PwIn(BaseModel):
    current: str
    new: str


@app.post("/api/auth/password", dependencies=[Depends(need_owner)])
def change_password(body: PwIn):
    if len(body.new) < 10:
        bad("Use at least 10 characters")
    if not auth.check_password(body.current):
        bad("Current password is wrong", 401)
    auth.set_password(body.new)
    return {"ok": True}


# ====================================================================== account
class ConnectIn(BaseModel):
    api_key: str
    client_id: str
    mpin: str
    totp_secret: str
    save: bool = True


@app.get("/api/account", dependencies=[Depends(need_owner)])
def account():
    s = BROKER.session
    saved = BROKER.saved()
    ids = list(saved["logins"])
    if saved["last"] in ids:
        ids.remove(saved["last"])
        ids.insert(0, saved["last"])
    return {
        "connected": s is not None, "client_id": s.client_id if s else None,
        "name": (s.profile or {}).get("name") if s else None,
        "server_ip": s.public_ip if s else None, "saved_ids": ids,
        "feed": FEED.status(), "allow_live": ALLOW_LIVE, "login_error": BROKER.login_error,
        "now": str(mt.now_ist()),
        "markets": {ex: mt.market_open(mt.now_ist(), ex) for ex in EXCHANGES},
    }


@app.post("/api/account/connect", dependencies=[Depends(need_owner)])
def connect(body: ConnectIn):
    try:
        BROKER.connect(body.api_key, body.client_id, body.mpin, body.totp_secret, save=body.save)
    except Exception as ex:
        bad(ex)
    threading.Thread(target=ins.load_master, daemon=True).start()
    return account()


class CidIn(BaseModel):
    client_id: str = ""


@app.post("/api/account/connect-saved", dependencies=[Depends(need_owner)])
def connect_saved(body: CidIn):
    try:
        BROKER.connect_saved(body.client_id or None)
    except Exception as ex:
        bad(ex)
    threading.Thread(target=ins.load_master, daemon=True).start()
    return account()


@app.post("/api/account/forget", dependencies=[Depends(need_owner)])
def forget(body: CidIn):
    BROKER.forget(body.client_id)
    return account()


@app.post("/api/account/disconnect", dependencies=[Depends(need_owner)])
def disconnect():
    if any(e.running for e in BROKER.engines.values()):
        bad("Stop the running strategies first")
    BROKER.disconnect()
    return account()


@app.get("/api/account/broker", dependencies=[Depends(need_owner)])
def broker_view():
    s = need_session()
    out = {"funds": {}, "positions": [], "orders": [], "holdings": [], "errors": []}
    for k, fn in (("funds", s.funds), ("positions", s.positions), ("holdings", s.holdings)):
        try:
            out[k] = fn() or out[k]
        except Exception as ex:
            out["errors"].append(f"{k}: {ex}")
    try:
        out["orders"] = [norm_order(r) for r in (s.order_book() or [])][::-1]
    except Exception as ex:
        out["errors"].append(f"orders: {ex}")
    return out


@app.post("/api/kill", dependencies=[Depends(need_owner)])
def kill():
    return {"closed": BROKER.kill_switch()}


# ====================================================================== meta + instruments
@app.get("/api/meta", dependencies=[Depends(need_owner)])
def meta():
    return {"timeframes": list(TIMEFRAMES), "patterns": list(PATTERNS),
            "pattern_candles": {d[0]: d[1] for d in PATTERN_DEFS}, "exchanges": list(EXCHANGES),
            "cloud_options": list(CLOUD_OPTIONS), "defaults": Config().to_dict(), "allow_live": ALLOW_LIVE,
            "indices": {ex: ins.index_options(ex) for ex in EXCHANGES}}


@app.get("/api/instruments", dependencies=[Depends(need_owner)])
def instruments(exchange: str = "NSE", index: list[str] = Query(default=[])):
    if exchange not in EXCHANGES:
        bad("Unknown exchange")
    try:
        return {"items": ins.instrument_list(exchange, index or None)}
    except Exception as ex:
        bad(f"Instrument list failed: {ex}")


class FindIn(BaseModel):
    exchange: str
    names: list[str]


@app.post("/api/instruments/find", dependencies=[Depends(need_owner)])
def find(body: FindIn):
    items, missing = ins.find(body.exchange, body.names)
    return {"items": items, "missing": missing}


# ====================================================================== strategies
def _summary(e, cfg_dict):
    if e is None:
        st = storage.load_state(os.path.join(cfg_dict["_path"], "live_state.json"))
        running = False
    else:
        st, running = e.state, e.running
    closed, opn = st["closed"], st["open"]
    realized = sum(float(t.get("pnl") or 0) for t in closed)
    unreal = sum((float(p.get("last_price") or p["entry"]) - p["entry"]) * p["qty"] for p in opn
                 if p.get("status", "OPEN") != "PENDING_ENTRY")
    wins = sum(1 for t in closed if float(t.get("pnl") or 0) > 0)
    return {"running": running, "open": len(opn), "closed": len(closed), "realized": round(realized, 2),
            "unrealized": round(unreal, 2), "win_rate": round(wins / len(closed) * 100, 1) if closed else None,
            "paused": bool(e and e.entries_paused)}


@app.get("/api/strategies", dependencies=[Depends(need_owner)])
def strategies():
    out = []
    for folder, files in storage.list_strategies().items():
        rows = []
        for f in files:
            c = f["config"]
            e = BROKER.peek(f["path"])
            rows.append({"file": f["file"], "folder": folder, "exchange": c.get("exchange"),
                         "tf_label": c.get("tf_label"), "mode": c.get("mode", "paper"),
                         "capital": c.get("capital"), "n_items": len(c.get("items") or []),
                         "was_running": storage.read_json(os.path.join(f["path"], "run_config.json")).get("running", False),
                         **_summary(e, {**c, "_path": f["path"]})})
        out.append({"folder": folder, "files": rows})
    return {"folders": out}


class NameIn(BaseModel):
    name: str


@app.post("/api/folders", dependencies=[Depends(need_owner)])
def new_folder(body: NameIn):
    if not body.name.strip():
        bad("Give the folder a name")
    return {"folder": storage.create_folder(body.name)}


@app.delete("/api/folders/{folder}", dependencies=[Depends(need_owner)])
def del_folder(folder: str):
    for f in storage.list_strategies().get(storage.safe_name(folder), []):
        e = BROKER.peek(f["path"])
        if (e and e.running) or (e and e.state["open"]):
            bad("A strategy in this folder is running or has open positions")
    storage.delete_folder(folder)
    return {"ok": True}


class StrategyIn(BaseModel):
    folder: str
    file: str
    config: dict


def _path(folder, file):
    p = storage.strategy_path(folder, file)
    if not os.path.exists(os.path.join(p, "config.json")):
        bad("Strategy not found", 404)
    return p


@app.post("/api/strategies", dependencies=[Depends(need_owner)])
def save_strategy(body: StrategyIn):
    if not body.folder.strip() or not body.file.strip():
        bad("Folder and file name are needed")
    cfg = Config.from_dict(body.config)
    errs = cfg.validate()
    if errs:
        bad("; ".join(errs))
    if cfg.mode == "live" and not ALLOW_LIVE:
        bad("Live mode is switched off on the server")
    storage.create_folder(body.folder)
    path = storage.save_config(body.folder, body.file, cfg.to_dict())
    e = BROKER.peek(path)
    if e and e.running:
        try:
            e.update_config(cfg)
        except ValueError as ex:
            bad(ex)
    return {"folder": storage.safe_name(body.folder), "file": storage.safe_name(body.file)}


def _view(folder, file):
    path = _path(folder, file)
    e = BROKER.engine(path)
    snap = e.snapshot()
    cfg = snap["cfg"] or Config.from_dict(storage.load_config(path)).to_dict()
    st = snap["state"]
    for t in st["closed"]:
        p = t.get("pattern")
        names = [x.strip() for x in str(p).split("+")] if isinstance(p, str) and p and p != "Manual BUY" else None
        t["pattern"] = (classify_candles(t, only=names) or p) if names else p
        try:
            t["pnl_pct"] = round((t["exit"] / t["entry"] - 1) * 100, 2)
        except (KeyError, TypeError, ZeroDivisionError):
            t["pnl_pct"] = None
    for p in st["open"]:
        lp = p.get("last_price") or p["entry"]
        pend = p.get("status") == "PENDING_ENTRY"
        p["pnl"] = 0.0 if pend else round((lp - p["entry"]) * p["qty"], 2)
        p["pnl_pct"] = 0.0 if pend else round((lp / p["entry"] - 1) * 100, 2)
        p["key"] = f"{p['exchange']}:{p['symbol']}"
    storage.fill_slots(st)
    st["open"].sort(key=lambda p: p.get("slot") or 0)
    for s_ in st["signals"]:
        s_.pop("hist", None)
    return {"folder": folder, "file": file, "config": cfg, "running": snap["running"], "paused": snap["paused"],
            "status": snap["status"], "errors": snap["errors"], "last_update": snap["last_update"],
            "rows": snap["rows"], "open": st["open"], "closed": st["closed"][::-1],
            "signals": st["signals"][::-1], "log": st["log"][::-1][:300], "orders": st["orders"][::-1],
            "summary": _summary(e, {}), "has_run": os.path.exists(e.run_file),
            "market_open": mt.market_open(mt.now_ist(), cfg.get("exchange", "NSE")), "feed": FEED.status(),
            "now": str(mt.now_ist())}


@app.get("/api/strategies/{folder}/{file}", dependencies=[Depends(need_owner)])
def view(folder: str, file: str):
    return _view(folder, file)


@app.delete("/api/strategies/{folder}/{file}", dependencies=[Depends(need_owner)])
def delete(folder: str, file: str):
    path = _path(folder, file)
    e = BROKER.peek(path)
    if e and (e.running or any(p.get("mode") == "live" for p in e.state["open"])):
        bad("Stop it and close live positions first")
    if e:
        e.stop(save=False)
        BROKER.engines.pop(os.path.abspath(path), None)
    storage.delete_strategy(path)
    return {"ok": True}


class ActionIn(BaseModel):
    keys: list[str] = []
    symbol: str = ""
    on: bool = True
    force: bool = False
    index: int = -1
    trade: dict = {}
    confirm: str = ""


@app.post("/api/strategies/{folder}/{file}/{action}", dependencies=[Depends(need_owner)])
def act(folder: str, file: str, action: str, body: ActionIn):
    path = _path(folder, file)
    e = BROKER.engine(path)
    try:
        if action == "start":
            need_session()
            cfg = Config.from_dict(storage.load_config(path))
            if cfg.mode == "live" and body.confirm != "LIVE":
                bad("Type LIVE to confirm real-money trading")
            e.start(cfg)
        elif action == "stop":
            if any(p.get("mode") == "live" for p in e.state["open"]) and not body.force:
                bad("Live positions are open. Stop anyway? Their target / stop-loss will NOT be watched.", 409)
            e.stop()
        elif action == "pause":
            e.pause(body.on)
        elif action == "exit-all":
            e.exit_all()
        elif action == "exit":
            e.exit_selected(body.keys)
        elif action == "buy":
            cfg = e.cfg
            it = next((x for x in (cfg.items if cfg else []) if x["symbol"] == body.symbol), None)
            if it is None:
                bad("Pick a stock from this strategy's list")
            ok, msg = e.manual_buy(it)
            if not ok:
                bad(msg)
        elif action == "buy-all":
            if not e.running:
                bad("Start the strategy first")
            threading.Thread(target=e.manual_buy_all, daemon=True).start()
        elif action == "reset":
            e.reset_all()
        elif action == "delete-position":
            if not e.delete_position(body.symbol):
                bad("Position not found")
        elif action == "delete-closed":
            if not e.delete_closed(body.index, body.trade or None):
                bad("Trade not found")
        else:
            bad("Unknown action", 404)
    except HTTPException:
        raise
    except ValueError as ex:
        bad(ex)
    return _view(folder, file)


@app.get("/api/strategies/{folder}/{file}/csv/{kind}", dependencies=[Depends(need_owner)])
def csv_export(folder: str, file: str, kind: str):
    v = _view(folder, file)
    buf = io.StringIO()
    w = csv.writer(buf)
    if kind == "excel-check":
        e = BROKER.engine(_path(folder, file))
        cols, rows = excel_check_rows(e.state["open"] + e.state["closed"])
        w.writerow(cols)
        w.writerows(rows)
    else:
        data = {"monitoring": v["rows"], "signals": v["signals"], "positions": v["open"], "closed": v["closed"],
                "orders": v["orders"], "logs": [{"line": x} for x in v["log"]]}.get(kind)
        if data is None:
            bad("Unknown export", 404)
        df = pd.DataFrame(data).drop(columns=["hist"], errors="ignore")
        df.to_csv(buf, index=False)
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{file}_{kind}_{mt.now_ist():%Y%m%d}.csv"'})


# ====================================================================== settings
class HolidaysIn(BaseModel):
    days: list[str]


@app.get("/api/settings/holidays", dependencies=[Depends(need_owner)])
def get_holidays():
    return {"days": sorted(mt.holidays())}


@app.put("/api/settings/holidays", dependencies=[Depends(need_owner)])
def put_holidays(body: HolidaysIn):
    return {"days": mt.save_holidays(body.days)}


# ====================================================================== built front end (optional, same server)
_DIST = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
if os.path.isdir(_DIST):
    @app.get("/{full:path}", include_in_schema=False)
    def spa(full: str):
        root = os.path.realpath(_DIST)
        f = os.path.realpath(os.path.join(root, full))
        if full and f.startswith(root + os.sep) and os.path.isfile(f):
            return FileResponse(f)
        return FileResponse(os.path.join(_DIST, "index.html"))
