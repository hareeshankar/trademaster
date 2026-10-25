"""Angel One SmartAPI REST client.

Written directly against the SmartAPI HTTP endpoints (same routes as the official smartapi-python package) so we can:
  * send the server's REAL public IP (the official package hard-codes a fake one - matters for the static-IP rule),
  * never log secrets (the official package logs full request headers on errors),
  * respect each endpoint's rate limit across all threads,
  * re-login automatically (TOTP) when the session token expires.
"""
import json
import logging
import re
import socket
import threading
import time
import uuid
from collections import deque

import pandas as pd
import requests

log = logging.getLogger("trademaster.angel")

ROOT = "https://apiconnect.angelone.in"
ROUTES = {
    "login": "/rest/auth/angelbroking/user/v1/loginByPassword",
    "logout": "/rest/secure/angelbroking/user/v1/logout",
    "token": "/rest/auth/angelbroking/jwt/v1/generateTokens",
    "profile": "/rest/secure/angelbroking/user/v1/getProfile",
    "place": "/rest/secure/angelbroking/order/v1/placeOrder",
    "modify": "/rest/secure/angelbroking/order/v1/modifyOrder",
    "cancel": "/rest/secure/angelbroking/order/v1/cancelOrder",
    "orderbook": "/rest/secure/angelbroking/order/v1/getOrderBook",
    "tradebook": "/rest/secure/angelbroking/order/v1/getTradeBook",
    "ltp": "/rest/secure/angelbroking/order/v1/getLtpData",
    "rms": "/rest/secure/angelbroking/user/v1/getRMS",
    "position": "/rest/secure/angelbroking/order/v1/getPosition",
    "holding": "/rest/secure/angelbroking/portfolio/v1/getHolding",
    "candle": "/rest/secure/angelbroking/historical/v1/getCandleData",
    "quote": "/rest/secure/angelbroking/market/v1/quote",
    "search": "/rest/secure/angelbroking/order/v1/searchScrip",
}

# (max calls per second, max calls per minute) - SmartAPI published limits, kept slightly under.
LIMITS = {
    "login": (1, 20), "token": (1, 20), "profile": (2, 100), "logout": (1, 20),
    "place": (9, 400), "modify": (9, 400), "cancel": (9, 400),      # <= 10 orders/sec: no exchange algo registration needed
    "orderbook": (1, 55), "tradebook": (1, 55), "ltp": (9, 500), "rms": (2, 100),
    "position": (1, 55), "holding": (1, 55), "candle": (3, 170), "quote": (1, 55), "search": (1, 50),
}


class RateLimiter:
    """Thread-safe: spaces calls so neither the per-second nor the per-minute limit is crossed."""

    def __init__(self, per_sec, per_min):
        self.gap = 1.0 / per_sec
        self.per_min = per_min
        self.lock = threading.Lock()
        self.last = 0.0
        self.window = deque()

    def wait(self):
        with self.lock:
            now = time.time()
            while self.window and now - self.window[0] > 60:
                self.window.popleft()
            delay = max(0.0, self.last + self.gap - now)
            if len(self.window) >= self.per_min:
                delay = max(delay, 60 - (now - self.window[0]) + 0.05)
            if delay > 0:
                time.sleep(delay)
            self.last = time.time()
            self.window.append(self.last)


_LIMITERS = {k: RateLimiter(*v) for k, v in LIMITS.items()}


def _public_ip():
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            ip = requests.get(url, timeout=5).text.strip()
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
                return ip
        except requests.RequestException:
            continue
    return "127.0.0.1"


def _local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


class AngelError(Exception):
    def __init__(self, msg, code=""):
        super().__init__(msg)
        self.code = code


def is_rate_error(msg):
    m = str(msg).lower()
    return "access rate" in m or "exceeding" in m or "too many" in m


class AngelSession:
    """One logged-in Angel One account. Safe to share across threads."""

    def __init__(self, api_key, client_id, mpin, totp_secret):
        self.api_key = api_key.strip()
        self.client_id = client_id.strip().upper()
        self.mpin = mpin.strip()
        self.totp_secret = totp_secret.replace(" ", "").strip()
        self.jwt = self.refresh = self.feed_token = ""
        self.login_time = 0.0
        self.login_day = ""
        self.profile = {}
        self.public_ip = _public_ip()
        self.local_ip = _local_ip()
        self.mac = ":".join(re.findall("..", "%012x" % uuid.getnode()))
        self.http = requests.Session()
        self._login_lock = threading.Lock()
        self.listeners = []                      # called after every (re)login - the live feed reconnects with new tokens

    # -------------------------------------------------------------- core
    def _headers(self):
        h = {
            "Content-Type": "application/json", "Accept": "application/json",
            "X-UserType": "USER", "X-SourceID": "WEB",
            "X-ClientLocalIP": self.local_ip, "X-ClientPublicIP": self.public_ip,
            "X-MACAddress": self.mac, "X-PrivateKey": self.api_key,
        }
        if self.jwt:
            h["Authorization"] = f"Bearer {self.jwt}"
        return h

    def _raw(self, kind, method, params=None, path_suffix=""):
        _LIMITERS[kind].wait()
        url = ROOT + ROUTES[kind] + path_suffix
        body = json.dumps(params or {})
        try:
            r = self.http.request(method, url, data=body if method == "POST" else None,
                                  params=None, headers=self._headers(), timeout=12)
        except requests.RequestException as ex:
            raise AngelError(f"network: {type(ex).__name__}") from ex
        try:
            data = r.json()
        except ValueError:
            raise AngelError(f"HTTP {r.status_code}: not JSON ({r.text[:120]})", str(r.status_code))
        if r.status_code in (401, 403) or data.get("error_type") == "TokenException":
            raise AngelError(data.get("message") or "session expired", "AUTH")
        return data

    def call(self, kind, method="POST", params=None, path_suffix="", retries=2):
        """Call an endpoint. Re-logins once on an expired session, backs off on rate errors."""
        self.ensure_fresh()
        last = None
        for attempt in range(retries + 1):
            used = self.jwt
            try:
                data = self._raw(kind, method, params, path_suffix)
            except AngelError as ex:
                last = ex
                if ex.code == "AUTH" and attempt == 0:
                    self.login(stale_jwt=used)
                    continue
                if "network" in str(ex) and attempt < retries:
                    time.sleep(1 + attempt)
                    continue
                raise
            msg = str(data.get("message") or "")
            if not data.get("status") and str(data.get("errorcode") or "") in ("AG8001", "AG8002", "AG8003") and attempt == 0:
                self.login(stale_jwt=used)                 # invalid / expired token codes
                continue
            if not data.get("status") and is_rate_error(msg) and attempt < retries:
                time.sleep(2 + 2 * attempt)
                continue
            return data
        raise last or AngelError("request failed")

    # -------------------------------------------------------------- session
    def login(self, stale_jwt=None, only_if_new_day=False):
        """Log in with MPIN + TOTP. stale_jwt: skip if another thread already replaced that token."""
        import pyotp
        with self._login_lock:
            today = f"{pd.Timestamp.now(tz='Asia/Kolkata'):%Y-%m-%d}"
            if stale_jwt is not None and self.jwt and self.jwt != stale_jwt:
                return self
            if only_if_new_day and self.jwt and self.login_day == today:
                return self
            self.jwt = ""
            otp = pyotp.TOTP(self.totp_secret).now()
            data = self._raw("login", "POST", {"clientcode": self.client_id, "password": self.mpin, "totp": otp})
            if not data.get("status"):
                raise AngelError(f"Login failed: {data.get('message') or data.get('errorcode') or 'unknown'}")
            d = data["data"]
            self.jwt, self.refresh, self.feed_token = d["jwtToken"], d["refreshToken"], d["feedToken"]
            if self.jwt.startswith("Bearer "):
                self.jwt = self.jwt[7:]
            self.login_time = time.time()
            self.login_day = f"{pd.Timestamp.now(tz='Asia/Kolkata'):%Y-%m-%d}"
            try:
                p = self._raw("profile", "GET", {"refreshToken": self.refresh})
                self.profile = p.get("data") or {}
            except AngelError:
                self.profile = {}
        for fn in list(self.listeners):
            try:
                fn(self)
            except Exception:
                log.exception("login listener failed")
        return self

    def ensure_fresh(self):
        """Angel sessions end at midnight: log in again on a new IST day (and the first time)."""
        today = f"{pd.Timestamp.now(tz='Asia/Kolkata'):%Y-%m-%d}"
        if not self.jwt or self.login_day != today:
            self.login(only_if_new_day=True)

    def logout(self):
        try:
            self._raw("logout", "POST", {"clientcode": self.client_id})
        except AngelError:
            pass
        self.jwt = ""

    # -------------------------------------------------------------- market data
    def candles(self, item, interval, start, end):
        """DataFrame[date, open, high, low, close, volume] (IST, naive)."""
        params = {"exchange": item["exchange"], "symboltoken": str(item["token"]), "interval": interval,
                  "fromdate": pd.Timestamp(start).strftime("%Y-%m-%d %H:%M"),
                  "todate": pd.Timestamp(end).strftime("%Y-%m-%d %H:%M")}
        data = self.call("candle", "POST", params, retries=3)
        rows = data.get("data") if data.get("status") else None
        if rows is None:
            raise AngelError(f"candles {item['symbol']}: {data.get('message') or 'no data'}")
        if not rows:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        d = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
        d["date"] = pd.to_datetime(d["date"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        return d

    def ltp(self, item):
        data = self.call("ltp", "POST", {"exchange": item["exchange"], "tradingsymbol": item["tradingsymbol"],
                                         "symboltoken": str(item["token"])})
        if not data.get("status") or not data.get("data"):
            raise AngelError(f"LTP {item['symbol']}: {data.get('message')}")
        return float(data["data"]["ltp"])

    def quotes(self, items, mode="LTP"):
        """{token: quote dict} for any number of items (50 per request)."""
        by_ex = {}
        for it in items:
            by_ex.setdefault(it["exchange"], []).append(str(it["token"]))
        out = {}
        for ex, toks in by_ex.items():
            for i in range(0, len(toks), 50):
                data = self.call("quote", "POST", {"mode": mode, "exchangeTokens": {ex: toks[i:i + 50]}})
                for r in ((data.get("data") or {}).get("fetched") or []):
                    out[str(r.get("symbolToken"))] = r
        return out

    def search(self, exchange, text):
        data = self.call("search", "POST", {"exchange": exchange, "searchscrip": text})
        return data.get("data") or []

    # -------------------------------------------------------------- account
    def funds(self):
        data = self.call("rms", "GET")
        return data.get("data") or {}

    def positions(self):
        data = self.call("position", "GET")
        return data.get("data") or []

    def holdings(self):
        data = self.call("holding", "GET")
        return data.get("data") or []

    def order_book(self):
        data = self.call("orderbook", "GET")
        return data.get("data") or []

    def trade_book(self):
        data = self.call("tradebook", "GET")
        return data.get("data") or []

    # -------------------------------------------------------------- orders (LIMIT only - market/IOC are banned for algos)
    def place_limit(self, item, side, qty, price, product, variety="NORMAL"):
        params = {
            "variety": variety, "tradingsymbol": item["tradingsymbol"], "symboltoken": str(item["token"]),
            "transactiontype": side, "exchange": item["exchange"], "ordertype": "LIMIT",
            "producttype": product, "duration": "DAY", "price": f"{price:.2f}",
            "quantity": str(int(qty)), "squareoff": "0", "stoploss": "0",
        }
        data = self.call("place", "POST", params, retries=0)      # never auto-retry an order (no duplicates)
        if not data.get("status") or not (data.get("data") or {}).get("orderid"):
            raise AngelError(f"Order rejected by Angel: {data.get('message') or data.get('errorcode') or data}")
        return data["data"]["orderid"]

    def modify_limit(self, order_id, item, qty, price, product, variety="NORMAL"):
        params = {
            "variety": variety, "orderid": order_id, "ordertype": "LIMIT", "producttype": product,
            "duration": "DAY", "price": f"{price:.2f}", "quantity": str(int(qty)),
            "tradingsymbol": item["tradingsymbol"], "symboltoken": str(item["token"]), "exchange": item["exchange"],
        }
        data = self.call("modify", "POST", params, retries=0)
        if not data.get("status"):
            raise AngelError(f"Modify failed: {data.get('message')}")
        return True

    def cancel(self, order_id, variety="NORMAL"):
        data = self.call("cancel", "POST", {"variety": variety, "orderid": order_id}, retries=0)
        if not data.get("status"):
            raise AngelError(f"Cancel failed: {data.get('message')}")
        return True
