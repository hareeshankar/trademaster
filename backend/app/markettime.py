"""Market clock: IST time, sessions, holidays, candle boundaries."""
import json
import os
from datetime import time as dtime

import numpy as np
import pandas as pd

from .settings import DATA_DIR

IST = "Asia/Kolkata"

# NSE equity trading holidays 2026 (weekdays). Editable from the Settings page (stored in holidays.json).
DEFAULT_HOLIDAYS = [
    "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03", "2026-04-14",
    "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10",
    "2026-11-24", "2026-12-25",
]
HOLIDAY_FILE = os.path.join(DATA_DIR, "holidays.json")
_cache = {"mtime": None, "days": set(DEFAULT_HOLIDAYS)}

SESSIONS = {      # exchange -> (open, close, square-off)
    "NSE": (dtime(9, 15), dtime(15, 30), dtime(15, 15)),
    "BSE": (dtime(9, 15), dtime(15, 30), dtime(15, 15)),
    "NFO": (dtime(9, 15), dtime(15, 30), dtime(15, 15)),
    "MCX": (dtime(9, 0), dtime(23, 30), dtime(23, 15)),
}


def now_ist():
    return pd.Timestamp.now(tz=IST).tz_localize(None)


def holidays():
    try:
        m = os.path.getmtime(HOLIDAY_FILE)
        if m != _cache["mtime"]:
            with open(HOLIDAY_FILE) as f:
                _cache["days"] = {str(x)[:10] for x in json.load(f)}
            _cache["mtime"] = m
    except (OSError, ValueError):
        pass
    return _cache["days"]


def save_holidays(days):
    days = sorted({str(d)[:10] for d in days if str(d).strip()})
    with open(HOLIDAY_FILE, "w") as f:
        json.dump(days, f, indent=1)
    return days


def trading_day(d, exchange="NSE"):
    d = pd.Timestamp(d)
    if d.weekday() >= 5:
        return False
    return exchange == "MCX" or f"{d:%Y-%m-%d}" not in holidays()


def market_open(now, exchange="NSE"):
    start, end, _ = SESSIONS.get(exchange, SESSIONS["NSE"])
    return trading_day(now, exchange) and start <= now.time() <= end


def square_off_time(exchange):
    return SESSIONS.get(exchange, SESSIONS["NSE"])[2]


def session_start(day, exchange):
    t = SESSIONS.get(exchange, SESSIONS["NSE"])[0]
    return pd.Timestamp(day).normalize() + pd.Timedelta(hours=t.hour, minutes=t.minute)


def session_close(day, exchange):
    t = SESSIONS.get(exchange, SESSIONS["NSE"])[1]
    return pd.Timestamp(day).normalize() + pd.Timedelta(hours=t.hour, minutes=t.minute)


def trading_days_held(entry_day, today, exchange="NSE"):
    """Trading days from entry day to today, both included (holidays skipped)."""
    hol = [] if exchange == "MCX" else sorted(holidays())
    return int(np.busday_count(str(entry_day)[:10], str(pd.Timestamp(today).date()), holidays=hol)) + 1


def candle_boundary(now, exchange, tf_min):
    """Start time of the candle that is forming now."""
    start = session_start(now, exchange)
    if now < start:
        return start
    if tf_min >= 1440:
        return now.normalize()
    step = pd.Timedelta(minutes=tf_min)
    return start + ((now - start) // step) * step


def candle_end(ts, tf, exchange):
    """When a candle starting at ts is complete. 4H / day candles end at the session close at the latest."""
    end = ts + tf
    if tf >= pd.Timedelta(minutes=240):
        end = min(end, session_close(ts, exchange))
    return end
