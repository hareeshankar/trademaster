"""The 10 bullish candle patterns - identical to the Google Sheet / Excel formulas of the original app.

Sheet columns: B=Open, C=High, D=Low, E=Close.  Each check gets the pattern's candles OLDEST -> NEWEST
(k[0] = first candle ... k[-1] = latest closed candle) and returns a boolean Series.
"""
from types import SimpleNamespace

import numpy as np
import pandas as pd


def _body(k):
    return (k.c - k.o).abs()


def _p_engulf(k):       # =IF(AND(B2>E2,B3<E3,B3<=E2,E3>=B2),"BULLISH ENGULFING ","")
    a, b = k
    return (a.o > a.c) & (b.o < b.c) & (b.o <= a.c) & (b.c >= a.o)


def _p_harami(k):       # =IF(AND(B3>E3,B4<E4,B4>E3,E4<B3),"BULLISH harami","")
    a, b = k
    return (a.o > a.c) & (b.o < b.c) & (b.o > a.c) & (b.c < a.o)


def _p_marubozu(k):     # =IF(AND(E2>B2,(B2-D2)<=0.05*(E2-B2),(C2-E2)<=0.05*(E2-B2)),"Bullish Marubozu","")
    (a,) = k
    return (a.c > a.o) & ((a.o - a.l) <= 0.05 * (a.c - a.o)) & ((a.h - a.c) <= 0.05 * (a.c - a.o))


def _p_hammer(k):
    (a,) = k
    bd = _body(a)
    return ((a.h - a.l) >= 2 * bd) & ((np.minimum(a.o, a.c) - a.l) >= 2 * bd) & ((a.h - np.maximum(a.o, a.c)) <= 0.1 * bd)


def _p_inv_hammer(k):
    (a,) = k
    bd = _body(a)
    return ((a.h - np.maximum(a.o, a.c)) >= 2 * bd) & ((a.h - a.l) >= 2 * bd) & ((np.minimum(a.o, a.c) - a.l) <= 0.1 * bd)


def _p_morning(k):
    a, b, c = k
    return (a.c < a.o) & (_body(b) <= 0.5 * _body(a)) & (c.c > c.o) & (c.c >= a.o - 0.5 * (a.o - a.c))


def _p_piercing(k):
    a, b = k
    return (a.c < a.o) & (b.c > b.o) & (b.c > a.o - (a.o - a.c) / 2) & (b.c < a.o)


def _p_rising3(k):
    a, b, c, d, e = k
    ok = a.c > a.o
    for m in (b, c, d):
        ok = ok & (_body(m) <= 0.5 * _body(a)) & (m.h <= a.h) & (m.l >= a.l)
    return ok & (e.c > e.o) & (e.c > a.c)


def _p_soldiers(k):
    a, b, c = k
    return ((a.c > a.o) & (b.c > b.o) & (c.c > c.o) & (b.c > a.c) & (c.c > b.c)
            & (b.o >= a.o) & (b.o <= a.c) & (c.o >= b.o) & (c.o <= b.c))


def _p_tweezer(k):      # =IF(AND(E2<B2,E3>B3,ABS(D3-D2)<=0.001*D2),"TWEEZER BOTTOM","")
    a, b = k
    return (a.c < a.o) & (b.c > b.o) & ((b.l - a.l).abs() <= 0.001 * a.l)


# (name, candles used, python check, Excel formula builder: sheet rows -> formula)
PATTERN_DEFS = (
    ("Bullish Engulfing", 2, _p_engulf,
     lambda r: f'=IF(AND(B{r[0]}>E{r[0]},B{r[1]}<E{r[1]},B{r[1]}<=E{r[0]},E{r[1]}>=B{r[0]}),"BULLISH ENGULFING ","")'),
    ("Bullish Harami", 2, _p_harami,
     lambda r: f'=IF(AND(B{r[0]}>E{r[0]},B{r[1]}<E{r[1]},B{r[1]}>E{r[0]},E{r[1]}<B{r[0]}),"BULLISH harami","")'),
    ("Bullish Marubozu", 1, _p_marubozu,
     lambda r: f'=IF(AND(E{r[0]}>B{r[0]},(B{r[0]}-D{r[0]})<=0.05*(E{r[0]}-B{r[0]}),(C{r[0]}-E{r[0]})<=0.05*(E{r[0]}-B{r[0]})),"Bullish Marubozu","")'),
    ("Hammer", 1, _p_hammer,
     lambda r: f'=IF(AND((C{r[0]}-D{r[0]})>=2*ABS(E{r[0]}-B{r[0]}),(MIN(B{r[0]},E{r[0]})-D{r[0]})>=2*ABS(E{r[0]}-B{r[0]}),(C{r[0]}-MAX(B{r[0]},E{r[0]}))<=0.1*ABS(E{r[0]}-B{r[0]})),"HAMMER","")'),
    ("Inverted Hammer", 1, _p_inv_hammer,
     lambda r: f'=IF(AND((C{r[0]}-MAX(B{r[0]},E{r[0]}))>=2*ABS(E{r[0]}-B{r[0]}),(C{r[0]}-D{r[0]})>=2*ABS(E{r[0]}-B{r[0]}),(MIN(B{r[0]},E{r[0]})-D{r[0]})<=0.1*ABS(E{r[0]}-B{r[0]})),"INVERTED HAMMER","")'),
    ("Morning Star", 3, _p_morning,
     lambda r: f'=IF(AND(E{r[0]}<B{r[0]},ABS(E{r[1]}-B{r[1]})<=0.5*ABS(E{r[0]}-B{r[0]}),E{r[2]}>B{r[2]},E{r[2]}>=B{r[0]}-0.5*(B{r[0]}-E{r[0]})),"MORNING STAR","")'),
    ("Piercing", 2, _p_piercing,
     lambda r: f'=IF(AND(E{r[0]}<B{r[0]},E{r[1]}>B{r[1]},E{r[1]}>B{r[0]}-(B{r[0]}-E{r[0]})/2,E{r[1]}<B{r[0]}),"PIERCING","")'),
    ("Rising Three Methods", 5, _p_rising3,
     lambda r: (f'=IF(AND(E{r[0]}>B{r[0]},ABS(E{r[1]}-B{r[1]})<=0.5*ABS(E{r[0]}-B{r[0]}),ABS(E{r[2]}-B{r[2]})<=0.5*ABS(E{r[0]}-B{r[0]}),'
                f'ABS(E{r[3]}-B{r[3]})<=0.5*ABS(E{r[0]}-B{r[0]}),C{r[1]}<=C{r[0]},D{r[1]}>=D{r[0]},C{r[2]}<=C{r[0]},D{r[2]}>=D{r[0]},'
                f'C{r[3]}<=C{r[0]},D{r[3]}>=D{r[0]},E{r[4]}>B{r[4]},E{r[4]}>E{r[0]}),"RISING THREE METHODS","")')),
    ("Three White Soldiers", 3, _p_soldiers,
     lambda r: f'=IF(AND(E{r[0]}>B{r[0]},E{r[1]}>B{r[1]},E{r[2]}>B{r[2]},E{r[1]}>E{r[0]},E{r[2]}>E{r[1]},B{r[1]}>=B{r[0]},B{r[1]}<=E{r[0]},B{r[2]}>=B{r[1]},B{r[2]}<=E{r[1]}),"THREE WHITE SOLDIERS","")'),
    ("Tweezer Bottom", 2, _p_tweezer,
     lambda r: f'=IF(AND(E{r[0]}<B{r[0]},E{r[1]}>B{r[1]},ABS(D{r[1]}-D{r[0]})<=0.001*D{r[0]}),"TWEEZER BOTTOM","")'),
)
PATTERNS = tuple(d[0] for d in PATTERN_DEFS)
CANDLE_KEYS = ("prev_O", "prev_H", "prev_L", "prev_C", "sig_O", "sig_H", "sig_L", "sig_C")
STORE_KEYS = CANDLE_KEYS + ("hist",)


def detect_patterns(df, trend_lookback=0):
    """True/False per candle for every pattern: the pattern ENDS on that candle (its last candle).
    trend_lookback > 0 also requires a prior down-move (close before the pattern < close `lookback` candles earlier)."""
    o, h, l, c = (df[x].astype(float) for x in ("open", "high", "low", "close"))

    def candle(sh):
        return SimpleNamespace(o=o.shift(sh), h=h.shift(sh), l=l.shift(sh), c=c.shift(sh))

    sig = pd.DataFrame(
        {name: fn([candle(n - 1 - j) for j in range(n)]) for name, n, fn, _ in PATTERN_DEFS},
        index=df.index,
    )
    lb = int(trend_lookback or 0)
    if lb > 0:
        sig = sig.mul(c.shift(1) < c.shift(lb + 1), axis=0).astype(bool)
    return sig.fillna(False).astype(bool)


def classify_candles(rec, only=None):
    """Re-check the candles saved with an entry against the formulas (for the trade tables)."""
    hist = rec.get("hist")
    if not isinstance(hist, (list, tuple)) or not hist:
        try:
            hist = [[rec["prev_O"], rec["prev_H"], rec["prev_L"], rec["prev_C"]],
                    [rec["sig_O"], rec["sig_H"], rec["sig_L"], rec["sig_C"]]]
        except (KeyError, TypeError):
            return None
    try:
        df = pd.DataFrame(hist, columns=["open", "high", "low", "close"]).astype(float)
    except (ValueError, TypeError):
        return None
    if df.empty or df.iloc[-1].isna().any():
        return None
    sig = detect_patterns(df)
    names = [n for n in (only or PATTERNS) if n in sig.columns and bool(sig[n].iat[-1])]
    return " + ".join(names) if names else "No pattern"


def pattern_outlook(done, patterns, trend_lookback=0):
    """What the forming candle needs to complete a 2-candle pattern (shown in the watchlist). List of short lines."""
    lb = int(trend_lookback or 0)
    if done is None or len(done) < lb + 2:
        return ["Not enough candles yet"]
    sel = [p for p in patterns if p in PATTERNS]
    p = done.iloc[-1]
    po, pc, pl = float(p["open"]), float(p["close"]), float(p["low"])
    out = []
    if pc < po and (lb == 0 or pc < float(done["close"].iloc[-1 - lb])):
        if "Bullish Engulfing" in sel:
            out.append(f"Engulfing: close above {po:.2f}")
        if "Piercing" in sel:
            out.append(f"Piercing: close {po - (po - pc) / 2:.2f} to {po:.2f}")
        if "Bullish Harami" in sel:
            out.append(f"Harami: open above {pc:.2f}, close below {po:.2f}")
        if "Tweezer Bottom" in sel:
            out.append(f"Tweezer: low near {pl:.2f}, green close")
    return out


def excel_check_rows(entries):
    """Every entry's last candles + the 10 sheet formulas (each formula on the first row of its own candles).
    Returns (columns, rows) ready for CSV."""
    cols = ["candle", "Open", "High", "Low", "Close", *[d[0] for d in PATTERN_DEFS], "App pattern"]
    rows = []
    rec = sorted([e for e in entries if e.get("hist") or all(k in e for k in CANDLE_KEYS)],
                 key=lambda e: str(e.get("entry_time")))
    r = 2
    for e in rec:
        hist = e.get("hist")
        if not isinstance(hist, (list, tuple)) or not hist:
            hist = [[e["prev_O"], e["prev_H"], e["prev_L"], e["prev_C"]], [e["sig_O"], e["sig_H"], e["sig_L"], e["sig_C"]]]
        m = len(hist)
        block = [[""] * len(PATTERN_DEFS) for _ in range(m)]
        for j, (_n, n, _fn, xl) in enumerate(PATTERN_DEFS):
            if m >= n:
                first = m - n
                block[first][j] = xl([r + first + q for q in range(n)])
        for i, (o_, h_, l_, c_) in enumerate(hist):
            last = i == m - 1
            label = (f"{e['symbol']} SIGNAL candle {e.get('signal_date', '')} (entry {e.get('entry_time', '')})"
                     if last else f"{e['symbol']} candle {i + 1}/{m}")
            rows.append([label, o_, h_, l_, c_, *block[i], e.get("pattern", "") if last else ""])
        r += m
    return cols, rows
