"""End-to-end engine run with a fake Angel account: paper entry/exit, then live order flow."""
import os, sys, time, shutil, itertools
os.environ["TM_DATA_DIR"] = "/tmp/tm_engine_test"
shutil.rmtree("/tmp/tm_engine_test", ignore_errors=True)
os.environ["TM_ALLOW_LIVE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pandas as pd
from app import markettime as mt
from app.models import Config
from app.engine import engine as E
from app.engine.registry import BROKER
from app.engine.orders import TRACKER
from app.angel.feed import EXCHANGE_TYPE

ITEM = {"symbol": "SBIN", "tradingsymbol": "SBIN-EQ", "token": "3045", "exchange": "NSE", "lot": 1, "tick": 0.05}

class FakeSession:
    def __init__(self):
        self.orders, self.n, self.ltp_v, self.fill_mode = {}, itertools.count(1000), 100.0, "fill"
    def candles(self, item, interval, start, end):
        now = mt.now_ist().floor("5min")
        dates = [now - pd.Timedelta(minutes=5 * i) for i in range(40, 0, -1)]   # all closed
        rows = []
        for d in dates[:-2]:
            rows.append([d, 101, 102, 99, 100.5, 1000])
        rows.append([dates[-2], 102, 102.5, 99.5, 100, 1000])   # red
        rows.append([dates[-1], 99.8, 103, 99.6, 102.6, 1000])  # green engulfing it
        return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    def ltp(self, item): return self.ltp_v
    def quotes(self, items, mode="LTP"): return {str(i["token"]): {"ltp": self.ltp_v} for i in items}
    def funds(self): return {"availablecash": "50000"}
    def positions(self): return [{"exchange": "NSE", "symboltoken": "3045", "netqty": str(sum(
        (o["qty"] if o["side"] == "BUY" else -o["qty"]) for o in self.orders.values() if o["status"] == "complete"))}]
    def place_limit(self, item, side, qty, price, product, variety="NORMAL"):
        oid = str(next(self.n))
        st = "complete" if self.fill_mode == "fill" else "open"
        self.orders[oid] = {"orderid": oid, "status": st, "side": side, "qty": qty, "price": price,
                            "filled": qty if st == "complete" else 0}
        return oid
    def modify_limit(self, oid, item, qty, price, product, variety="NORMAL"):
        self.orders[oid]["price"] = price; return True
    def cancel(self, oid, variety="NORMAL"):
        self.orders[oid]["status"] = "cancelled"; return True
    def order_book(self):
        return [{"orderid": o["orderid"], "status": o["status"], "filledshares": str(o["filled"]),
                 "unfilledshares": str(o["qty"] - o["filled"]), "quantity": str(o["qty"]),
                 "averageprice": str(o["price"] if o["filled"] else 0), "price": str(o["price"]),
                 "transactiontype": o["side"], "tradingsymbol": "SBIN-EQ", "text": ""} for o in self.orders.values()]

fake = FakeSession()
BROKER.session = fake
E.FEED.subscribe = lambda *a, **k: None
E.FEED.unsubscribe = lambda *a, **k: None
E.FEED.listen = lambda *a, **k: None

def tick(eng, px):
    fake.ltp_v = px
    eng._on_tick({"exchange_type": 1, "token": "3045", "ltp": px})

def wait(cond, sec=8):
    t = time.time()
    while time.time() - t < sec:
        if cond(): return True
        time.sleep(0.1)
    return False

# ---------------- PAPER
eng = BROKER.engine("/tmp/tm_engine_test/strategies/F/paper")
cfg = Config.from_dict({"exchange": "NSE", "items": [ITEM], "capital": 10000, "max_open": 2, "target_pct": 1,
                        "sl_pct": 1, "ignore_hours": True, "tf_label": "5 minutes"})
tick(eng, 100.0)
eng.start(cfg)
assert wait(lambda: eng.state["open"]), eng.snapshot()["errors"]
p = eng.state["open"][0]
print("PAPER entry:", p["symbol"], p["entry"], "qty", p["qty"], "tgt", p["target"], "sl", p["stop_loss"], p["pattern"])
assert p["pattern"] == "Bullish Engulfing" and p["qty"] == 50          # 10000/2 per position / 100
tick(eng, 100.5); time.sleep(0.5); assert eng.state["open"]
tick(eng, 101.2)                                                       # through target 101.00
assert wait(lambda: not eng.state["open"])
c = eng.state["closed"][-1]
print("PAPER exit:", c["reason"], c["exit"], "pnl", c["pnl"])
assert c["reason"] == "Target" and c["exit"] == 101.0 and c["pnl"] == 50.0
eng.stop()

# ---------------- LIVE (fake broker)
mt_open = mt.market_open
E.mt.market_open = lambda now, ex="NSE": True
E.FRESH_SECONDS = 10 ** 6
E.mt.square_off_time = lambda ex: pd.Timestamp("23:59").time()
eng2 = BROKER.engine("/tmp/tm_engine_test/strategies/F/live")
cfg2 = Config.from_dict({"exchange": "NSE", "items": [ITEM], "capital": 10000, "max_open": 1, "target_pct": 1,
                         "sl_pct": 1, "mode": "live", "tf_label": "5 minutes", "order_timeout_s": 1})
tick(eng2, 100.0)
eng2.start(cfg2)
assert wait(lambda: eng2.state["open"] and eng2.state["open"][0]["status"] == "OPEN"), (eng2.state["log"][-5:], eng2.snapshot()["errors"])
p = eng2.state["open"][0]
print("LIVE bought:", p["qty"], "@", p["entry"], "order", p["order_id"], "tgt", p["target"], "sl", p["stop_loss"])
assert p["entry"] == 100.2                                             # LTP 100 + 0.2% buffer, LIMIT
fake.fill_mode = "rest"                                                # next sell stays open -> re-price path
tick(eng2, 98.9)                                                       # stop-loss 99.15 hit
assert wait(lambda: eng2.state["open"][0]["status"] == "PENDING_EXIT")
time.sleep(2.5)
po = eng2.state["open"][0]
print("LIVE sell pending, tries:", po["exit_tries"], "price now", po["exit_price_sent"])
assert po["exit_tries"] >= 2
oid = po["exit_order_id"]; o = fake.orders[oid]; o["status"], o["filled"] = "complete", o["qty"]
TRACKER.ts = 0
assert wait(lambda: not eng2.state["open"])
c = eng2.state["closed"][-1]
print("LIVE closed:", c["reason"], c["exit"], "pnl", c["pnl"])
assert c["reason"] == "SL"
# reconcile has no mismatch
eng2._reconcile(); print("errors:", list(eng2.errors.values()))
print("orders sent:", [(o["side"], o["status"]) for o in eng2.state["orders"]])
eng2.stop()
print("ALL ENGINE TESTS PASSED")
