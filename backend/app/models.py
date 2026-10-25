"""Strategy settings (one config.json per strategy file). Backward compatible with the Streamlit app's files."""
import math
from dataclasses import asdict, dataclass, field, fields

from .indicators import cloud_text
from .patterns import PATTERNS

TIMEFRAMES = {
    "1 minute": ("ONE_MINUTE", 1),
    "3 minutes": ("THREE_MINUTE", 3),
    "5 minutes": ("FIVE_MINUTE", 5),
    "10 minutes": ("TEN_MINUTE", 10),
    "15 minutes": ("FIFTEEN_MINUTE", 15),
    "30 minutes": ("THIRTY_MINUTE", 30),
    "1 hour": ("ONE_HOUR", 60),
    "4 hours": ("FOUR_HOUR", 240),      # built from 1-hour candles (Angel One has no 4H interval)
    "1 day": ("ONE_DAY", 1440),
}
EXCHANGES = ("NSE", "BSE", "NFO", "MCX")
FUTURES_MARGIN_PCT = 10.0


@dataclass
class Config:
    # ---- what to watch ----
    exchange: str = "NSE"
    items: list = field(default_factory=list)       # [{symbol, tradingsymbol, token, exchange, lot, tick}]
    tf_label: str = "5 minutes"
    interval: str = "FIVE_MINUTE"
    tf_min: int = 5
    patterns: tuple = PATTERNS
    trend_lookback: int = 0
    cloud_filter: str = ""            # "" off | "Above,Center" ...
    # ---- money ----
    capital: float = 100000.0
    max_open: int = 5
    no_limit: bool = False
    size_mode: str = "amount"         # amount | qty | equal
    position_amount: float = 0.0
    position_qty: int = 0
    # ---- exits ----
    target_pct: float = 1.0
    sl_pct: float = 1.0               # NEW default 1% (the old app had no stop-loss by default)
    max_hold: int = 1                 # trading days (1 = same day)
    brokerage_pct: float = 0.0        # paper only (live uses real fills)
    slippage_pct: float = 0.0         # paper only
    # ---- mode / live orders ----
    mode: str = "paper"               # paper | live
    product: str = "intraday"         # intraday | carry
    entry_buffer_pct: float = 0.2     # BUY LIMIT price = LTP + this % (market orders are banned for algos)
    exit_buffer_pct: float = 0.3      # SELL LIMIT price = LTP - this %
    order_timeout_s: int = 20         # unfilled entry -> cancel; unfilled exit -> re-price
    daily_loss_limit: float = 0.0     # stop new entries for the day when today's P&L <= -this (0 = off)
    max_orders_day: int = 50          # safety cap on entries per day
    ignore_hours: bool = False        # paper testing outside market hours

    @property
    def n_stocks(self):
        return len(self.items or [])

    @property
    def per_position(self):
        if self.size_mode == "equal":
            n = max(self.n_stocks, 1) if self.no_limit else max(self.max_open, 1)
            return self.capital / n
        return self.position_amount if self.position_amount > 0 else self.capital / max(self.max_open, 1)

    @property
    def size_text(self):
        if self.size_mode == "qty":
            return f"qty/position {int(self.position_qty)}"
        return ("equal weight " if self.size_mode == "equal" else "amount/position ") + f"{self.per_position:,.0f}"

    def size_qty(self, one_lot, lot, cash):
        """Quantity in shares (lots x lot size for NFO/MCX), capped by free cash."""
        if one_lot <= 0:
            return 0
        if self.size_mode == "qty":
            n = max(int(self.position_qty), 0)
            return min(n, math.floor(max(cash, 0) / one_lot)) * lot
        return math.floor(min(self.per_position, max(cash, 0)) / one_lot) * lot

    def broker_product(self, exchange):
        if self.product == "intraday":
            return "INTRADAY"
        return "DELIVERY" if exchange in ("NSE", "BSE") else "CARRYFORWARD"

    def to_dict(self):
        d = asdict(self)
        d["patterns"] = list(self.patterns)
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        names = {f.name for f in fields(cls)}
        kw = {k: v for k, v in d.items() if k in names}
        kw["patterns"] = tuple(p for p in (d.get("patterns") or PATTERNS) if p in PATTERNS) or PATTERNS
        kw["cloud_filter"] = cloud_text(d.get("cloud_filter"))
        if "sl_pct" not in d:
            kw["sl_pct"] = 0.0              # old files: keep their (zero) stop-loss rather than silently adding one
        for k, t in (("capital", float), ("target_pct", float), ("sl_pct", float), ("position_amount", float),
                     ("max_open", int), ("position_qty", int), ("tf_min", int), ("max_hold", int),
                     ("entry_buffer_pct", float), ("exit_buffer_pct", float), ("order_timeout_s", int),
                     ("daily_loss_limit", float), ("max_orders_day", int), ("trend_lookback", int)):
            if k in kw:
                try:
                    kw[k] = t(kw[k] or 0)
                except (TypeError, ValueError):
                    kw.pop(k)
        if kw.get("tf_label") not in TIMEFRAMES:
            kw["tf_label"] = next((lbl for lbl, v in TIMEFRAMES.items() if v[0] == kw.get("interval")), "5 minutes")
        kw["interval"], kw["tf_min"] = TIMEFRAMES[kw["tf_label"]]
        kw["mode"] = "live" if kw.get("mode") == "live" else "paper"
        kw["product"] = "carry" if kw.get("product") == "carry" else "intraday"
        if kw["product"] == "intraday":
            kw["max_hold"] = 1
        kw["max_hold"] = max(int(kw.get("max_hold", 1) or 1), 1)
        return cls(**kw)

    def validate(self):
        errs = []
        if self.exchange not in EXCHANGES:
            errs.append("Unknown exchange")
        if not self.items:
            errs.append("Pick at least one stock")
        if self.capital <= 0:
            errs.append("Capital must be above 0")
        if self.target_pct <= 0:
            errs.append("Target % must be above 0")
        if self.sl_pct < 0:
            errs.append("Stop-loss % cannot be negative")
        if self.mode == "live" and self.sl_pct <= 0:
            errs.append("Live strategies need a stop-loss above 0%")
        if self.mode == "live" and self.ignore_hours:
            errs.append("'Ignore market hours' is for paper testing only")
        if not self.patterns:
            errs.append("Pick at least one pattern")
        return errs
