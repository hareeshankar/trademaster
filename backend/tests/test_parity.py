"""New pattern engine must give exactly the same signals as the original Streamlit app."""
import os, sys, types
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("TM_DATA_DIR", "/tmp/tm_test")
from app.patterns import detect_patterns, PATTERNS
from app.indicators import cloud_position

src = open(sys.argv[1]).read().splitlines()
seg = "\n".join(src[0:15] + src[16:19] + src[27:126] + src[230:247] + src[268:314])   # skip 'import streamlit'
ns = {}
exec(seg, ns)

rng = np.random.default_rng(7)
total = {p: 0 for p in PATTERNS}
for trial in range(300):
    n = 400
    c = 100 + np.cumsum(rng.normal(0, 1, n))
    o = c + rng.normal(0, 0.8, n)
    # sprinkle exact equalities and tiny bodies so edge cases get hit
    m = rng.random(n) < 0.05
    o[m] = c[m]                                   # doji candles (open == close)
    h = np.maximum(o, c) + np.abs(rng.normal(0, 0.6, n)) * (rng.random(n) > 0.1)
    l = np.minimum(o, c) - np.abs(rng.normal(0, 0.6, n)) * (rng.random(n) > 0.1)
    df = pd.DataFrame({"open": o.round(1), "high": h.round(1), "low": l.round(1), "close": c.round(1)})
    for lb in (0, 3):
        new = detect_patterns(df, lb)
        old = ns["detect_patterns"](df, types.SimpleNamespace(trend_lookback=lb))
        assert new.equals(old), (trial, lb)
        for p in PATTERNS:
            total[p] += int(new[p].sum())
    assert cloud_position(df) == ns["cloud_position"](df)
print("identical on 600 series; signals found per pattern:", total)
