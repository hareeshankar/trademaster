"""Historical candles from Angel One (pattern detection always uses the broker's own candles, like the Excel sheet)."""
import pandas as pd

from ..indicators import CLOUD_DAYS
from ..markettime import session_start

BASE_DAYS = {"FOUR_HOUR": 45, "ONE_DAY": 90}


def resample_4h(d, exchange):
    """1-hour candles -> 4-hour candles aligned to the session start (NSE/BSE/NFO 09:15, MCX 09:00)."""
    if d.empty:
        return d
    d = d.sort_values("date").copy()
    day = d["date"].dt.normalize()
    st = d["date"].map(lambda t: session_start(t, exchange))
    mins = (d["date"] - st).dt.total_seconds() // 60
    d["_g"], d["_d"] = (mins // 240).astype(int), day
    return d.groupby(["_d", "_g"], sort=True).agg(
        date=("date", "first"), open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
    ).reset_index(drop=True)


def fetch_full(session, item, interval, now, cloud=False):
    days = BASE_DAYS.get(interval, 5)
    if cloud:
        days = max(days, CLOUD_DAYS.get(interval, days))
    if interval == "FOUR_HOUR":
        d = session.candles(item, "ONE_HOUR", now - pd.Timedelta(days=days), now)
        d = resample_4h(d, item["exchange"])
    else:
        d = session.candles(item, interval, now - pd.Timedelta(days=days), now)
    return d[["date", "open", "high", "low", "close"]].reset_index(drop=True) if not d.empty else d


def fetch_update(session, item, interval, tf_min, have, now, cloud=False):
    """Only the newest candles, merged into what we already have (much lighter than re-downloading 5 days)."""
    if have is None or have.empty or interval in ("FOUR_HOUR", "ONE_DAY"):
        return fetch_full(session, item, interval, now, cloud)
    since = have["date"].iat[-1] - pd.Timedelta(minutes=tf_min * 3)
    d = session.candles(item, interval, since, now)
    if d.empty:
        return have
    d = d[["date", "open", "high", "low", "close"]]
    out = pd.concat([have[have["date"] < d["date"].iat[0]], d], ignore_index=True)
    keep = 400 if not cloud else 600
    return out.drop_duplicates("date", keep="last").sort_values("date").tail(keep).reset_index(drop=True)
