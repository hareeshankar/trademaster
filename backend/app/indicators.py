"""Ichimoku cloud (optional entry filter) - same maths as the original app."""
import numpy as np
import pandas as pd

CLOUD_OPTIONS = ("Above", "Center", "Below")
CLOUD_MIN_BARS = 78               # Senkou B needs 52 candles + the cloud is drawn 26 candles ahead
# calendar days of history to load when the cloud filter is ON
CLOUD_DAYS = {"ONE_MINUTE": 5, "THREE_MINUTE": 5, "FIVE_MINUTE": 6, "TEN_MINUTE": 8, "FIFTEEN_MINUTE": 10,
              "THIRTY_MINUTE": 18, "ONE_HOUR": 35, "FOUR_HOUR": 130, "ONE_DAY": 160}


def cloud_list(v):
    parts = v if isinstance(v, (list, tuple, set)) else str(v or "").split(",")
    got = {str(x).strip() for x in parts}
    return [k for k in CLOUD_OPTIONS if k in got]


def cloud_text(v):
    return ",".join(cloud_list(v))


def ichimoku_cloud(df, tenkan=9, kijun=26, senkou_b=52, shift=26):
    h, l = df["high"].astype(float), df["low"].astype(float)

    def mid(n):
        return (h.rolling(n).max() + l.rolling(n).min()) / 2

    span_a = ((mid(tenkan) + mid(kijun)) / 2).shift(shift)
    span_b = mid(senkou_b).shift(shift)
    return pd.DataFrame({"span_a": span_a, "span_b": span_b,
                         "top": np.maximum(span_a, span_b), "bottom": np.minimum(span_a, span_b)}, index=df.index)


def cloud_position(df):
    if df is None or len(df) < CLOUD_MIN_BARS:
        return None
    last = ichimoku_cloud(df).iloc[-1]
    if pd.isna(last["top"]) or pd.isna(last["bottom"]):
        return None
    close = float(df["close"].iat[-1])
    return "Above" if close > last["top"] else "Below" if close < last["bottom"] else "Center"
