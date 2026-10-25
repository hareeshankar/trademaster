"""Shared order-book cache (getOrderBook is limited to 1 call/sec for the whole account) + price rounding."""
import math
import threading
import time

FINAL_OK = {"complete"}
FINAL_DEAD = {"rejected", "cancelled"}


def round_up(price, tick):
    tick = tick or 0.05
    return round(math.ceil(round(price / tick, 6)) * tick, 2)


def round_down(price, tick):
    tick = tick or 0.05
    return round(math.floor(round(price / tick, 6)) * tick, 2)


def norm_order(r):
    """Angel order-book row -> the few fields we use."""
    def f(k):
        try:
            return float(r.get(k) or 0)
        except (TypeError, ValueError):
            return 0.0
    return {
        "order_id": str(r.get("orderid") or ""), "status": str(r.get("orderstatus") or r.get("status") or "").lower(),
        "filled": int(f("filledshares")), "unfilled": int(f("unfilledshares")), "qty": int(f("quantity")),
        "avg": f("averageprice"), "price": f("price"), "text": str(r.get("text") or "")[:200],
        "side": r.get("transactiontype"), "symbol": r.get("tradingsymbol"), "updated": r.get("updatetime"),
    }


class OrderTracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.rows = {}
        self.ts = 0.0
        self.error = ""

    def refresh(self, session, max_age=2.0):
        with self.lock:
            if time.time() - self.ts < max_age:
                return
            try:
                book = session.order_book()
                self.rows = {str(r.get("orderid")): norm_order(r) for r in book}
                self.error = ""
            except Exception as ex:
                self.error = f"order book: {ex}"
            self.ts = time.time()

    def get(self, session, order_id, max_age=2.0):
        self.refresh(session, max_age)
        return self.rows.get(str(order_id))

    def all(self):
        return list(self.rows.values())


TRACKER = OrderTracker()
