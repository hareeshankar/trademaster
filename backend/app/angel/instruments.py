"""Angel One instrument master + index member lists (stock pickers)."""
import io
import os
import threading
import time

import pandas as pd
import requests

from ..settings import DATA_DIR

MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
MASTER_CACHE = os.path.join(DATA_DIR, "scrip_master.pkl")
_lock = threading.Lock()
_mem = {"df": None, "day": None, "lists": {}, "index": {}}

NIFTY50_FALLBACK = (
    "ADANIENT ADANIPORTS APOLLOHOSP ASIANPAINT AXISBANK BAJAJ-AUTO BAJFINANCE BAJAJFINSV BEL BHARTIARTL CIPLA "
    "COALINDIA DRREDDY EICHERMOT ETERNAL GRASIM HCLTECH HDFCBANK HDFCLIFE HEROMOTOCO HINDALCO HINDUNILVR ICICIBANK "
    "INDIGO INFY ITC JIOFIN JSWSTEEL KOTAKBANK LT M&M MARUTI MAXHEALTH NESTLEIND NTPC ONGC POWERGRID RELIANCE "
    "SBILIFE SBIN SHRIRAMFIN SUNPHARMA TATACONSUM TMPV TATASTEEL TCS TECHM TITAN TRENT ULTRACEMCO WIPRO"
).split()
SENSEX30 = set(
    "ADANIPORTS ASIANPAINT AXISBANK BAJFINANCE BAJAJFINSV BEL BHARTIARTL ETERNAL HCLTECH HDFCBANK HINDUNILVR "
    "ICICIBANK INFY ITC KOTAKBANK LT M&M MARUTI NTPC POWERGRID RELIANCE SBIN SUNPHARMA TATASTEEL TCS "
    "TECHM TITAN TRENT ULTRACEMCO TMPV".split()
)
MCX_GROUPS = {
    "Bullion": {"GOLD", "GOLDM", "SILVER", "SILVERM"},
    "Energy": {"CRUDEOIL", "CRUDEOILM", "NATURALGAS"},
    "Base Metals": {"COPPER", "ZINC", "ALUMINIUM", "LEAD", "NICKEL"},
}
NFO_INDEX = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}
NSE_INDEX_FILES = {
    "Nifty 50": "ind_nifty50list", "Nifty Next 50": "ind_niftynext50list", "Nifty 100": "ind_nifty100list",
    "Nifty 200": "ind_nifty200list", "Nifty 500": "ind_nifty500list",
    "Nifty Bank": "ind_niftybanklist", "Nifty IT": "ind_niftyitlist", "Nifty Midcap 100": "ind_niftymidcap100list",
    "Nifty Auto": "ind_niftyautolist", "Nifty Pharma": "ind_niftypharmalist", "Nifty FMCG": "ind_niftyfmcglist",
    "Nifty Metal": "ind_niftymetallist", "Nifty Financial Services": "ind_niftyfinancelist",
}


def index_options(exchange):
    if exchange == "NSE":
        return list(NSE_INDEX_FILES) + ["All NSE stocks"]
    if exchange == "BSE":
        return ["Sensex 30"]
    if exchange == "NFO":
        return ["Index futures", "Nifty 50 stock futures"]
    return list(MCX_GROUPS)


def load_master(force=False):
    today = time.strftime("%Y-%m-%d")
    with _lock:
        if not force and _mem["df"] is not None and _mem["day"] == today:
            return _mem["df"]
        if not force and os.path.exists(MASTER_CACHE) and \
                time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(MASTER_CACHE))) == today:
            try:
                _mem["df"], _mem["day"] = pd.read_pickle(MASTER_CACHE), today
                _mem["lists"] = {}
                return _mem["df"]
            except Exception:
                pass
        df = pd.DataFrame(requests.get(MASTER_URL, timeout=120).json())
        df.columns = [c.strip().lower() for c in df.columns]
        keep = [c for c in ("exch_seg", "symbol", "name", "token", "lotsize", "instrumenttype", "expiry", "tick_size")
                if c in df.columns]
        df = df[keep]
        try:
            df.to_pickle(MASTER_CACHE)
        except Exception:
            pass
        _mem["df"], _mem["day"], _mem["lists"] = df, today, {}
        return df


def _nse_index(name):
    if name in _mem["index"]:
        return _mem["index"][name]
    f = NSE_INDEX_FILES.get(name)
    syms = []
    if f:
        try:
            r = requests.get(f"https://niftyindices.com/IndexConstituent/{f}.csv",
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            r.raise_for_status()
            syms = [x.strip().upper() for x in pd.read_csv(io.StringIO(r.text))["Symbol"].astype(str) if x.strip()]
        except Exception:
            syms = []
    if not syms and name == "Nifty 50":
        syms = list(NIFTY50_FALLBACK)
    if syms:
        _mem["index"][name] = syms
    return syms


def members(exchange, indices):
    out = set()
    for ix in indices or []:
        if exchange == "NSE":
            out |= set(_nse_index(ix))
        elif exchange == "BSE":
            out |= SENSEX30
        elif exchange == "NFO":
            out |= NFO_INDEX if ix == "Index futures" else set(_nse_index("Nifty 50"))
        else:
            out |= MCX_GROUPS.get(ix, set())
    return out


def to_item(row, exchange):
    try:
        lot = max(int(float(row.get("lotsize") or 1)), 1) if exchange in ("NFO", "MCX") else 1
    except (TypeError, ValueError):
        lot = 1
    try:
        tick = float(row.get("tick_size") or 5) / 100.0          # master gives tick in paise
        tick = tick if tick > 0 else 0.05
    except (TypeError, ValueError):
        tick = 0.05
    return {"symbol": row["label"], "tradingsymbol": row["symbol"], "token": str(row["token"]),
            "exchange": exchange, "lot": lot, "tick": tick}


def instrument_list(exchange, indices=None):
    """Pickable instruments for an exchange, optionally limited to index members."""
    key = (exchange, tuple(sorted(indices or [])))
    if key in _mem["lists"]:
        return _mem["lists"][key]
    df = load_master()
    d = df[df["exch_seg"] == exchange].copy()
    nm, sy = d["name"].fillna("").str.upper(), d["symbol"].fillna("").str.upper()
    all_nse = exchange == "NSE" and indices and "All NSE stocks" in indices
    base = members(exchange, [i for i in (indices or []) if i != "All NSE stocks"])
    if exchange == "NSE":
        d = d[sy.str.endswith("-EQ") & (True if all_nse or not indices else nm.isin(base))]
        d["label"] = d["name"]
    elif exchange == "BSE":
        pick = nm.isin(SENSEX30) | sy.isin(SENSEX30) if indices else d["symbol"].notna()
        d = d[pick]
        d["label"] = d["name"].where(nm.isin(SENSEX30), d["symbol"]) if indices else d["symbol"]
    else:
        itype = d["instrumenttype"].fillna("") if "instrumenttype" in d else pd.Series("", index=d.index)
        if not indices:
            base = NFO_INDEX | set(_nse_index("Nifty 50")) if exchange == "NFO" else set().union(*MCX_GROUPS.values())
        d = d[itype.str.startswith("FUT") & nm.isin(base)]
        d["exp"] = pd.to_datetime(d["expiry"], format="%d%b%Y", errors="coerce")
        d = d[d["exp"] >= pd.Timestamp.now().normalize()].sort_values("exp").drop_duplicates("name")
        d["label"] = d["symbol"]
    d = d.drop_duplicates("label").sort_values("label")
    out = [to_item(r, exchange) for r in d.to_dict("records")]
    _mem["lists"][key] = out
    return out


def find(exchange, names):
    """Typed names -> items (exact symbol / name match on the exchange)."""
    df = load_master()
    d = df[df["exch_seg"] == exchange]
    out, missing = [], []
    for raw in names:
        n = raw.strip().upper()
        if not n:
            continue
        if exchange in ("NSE", "BSE"):
            hit = d[(d["symbol"].str.upper() == n + "-EQ") | (d["symbol"].str.upper() == n)]
            if hit.empty:
                hit = d[d["name"].fillna("").str.upper() == n]
        else:
            hit = d[d["symbol"].str.upper() == n]
            if hit.empty:
                itype = d["instrumenttype"].fillna("")
                h2 = d[(d["name"].fillna("").str.upper() == n) & itype.str.startswith("FUT")].copy()
                h2["exp"] = pd.to_datetime(h2["expiry"], format="%d%b%Y", errors="coerce")
                hit = h2[h2["exp"] >= pd.Timestamp.now().normalize()].sort_values("exp").head(1)
        if hit.empty:
            missing.append(raw.strip())
            continue
        r = hit.iloc[0].to_dict()
        r["label"] = n if exchange in ("NSE", "BSE") else r["symbol"]
        out.append(to_item(r, exchange))
    return out, missing
