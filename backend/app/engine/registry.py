"""The account (Angel session), all strategy engines, saved logins, kill switch."""
import base64
import hashlib
import json
import logging
import os
import threading
import time

from ..angel.client import AngelSession
from ..angel.feed import FEED
from ..settings import ALLOW_LIVE, ANGEL_ENV, DATA_DIR, SECRET_KEY
from ..storage import STRATEGIES_ROOT, add_log, read_json, save_state
from .engine import Engine

log = logging.getLogger("trademaster.registry")
LOGIN_FILE = os.path.join(DATA_DIR, "angel_logins.bin")


def _fernet():
    from cryptography.fernet import Fernet
    key = SECRET_KEY or _local_key()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def _local_key():
    kp = os.path.join(DATA_DIR, ".secret")
    if not os.path.exists(kp):
        with open(kp, "w") as f:
            f.write(base64.urlsafe_b64encode(os.urandom(32)).decode())
        os.chmod(kp, 0o600)
    return open(kp).read().strip()


class Broker:
    def __init__(self):
        self.session = None
        self.lock = threading.RLock()
        self.engines = {}
        self.allow_live = ALLOW_LIVE
        self._funds = (None, 0.0)
        self.login_error = ""

    # ---------------------------------------------------------------- login
    def connect(self, api_key, client_id, mpin, totp, save=True):
        s = AngelSession(api_key, client_id, mpin, totp)
        s.login()
        s.listeners.append(lambda sess: FEED.attach(sess))
        with self.lock:
            old = self.session
            self.session = s
        if old is not None and old is not s:
            old.listeners.clear()
        FEED.attach(s)
        if save:
            self.save_login((api_key, client_id, mpin, totp))
        self.login_error = ""
        threading.Thread(target=self.autoresume, daemon=True).start()
        return s

    def disconnect(self):
        with self.lock:
            s, self.session = self.session, None
        if s is not None:
            s.logout()
        FEED.stop()

    def save_login(self, creds):
        store = self.saved()
        cid = creds[1].strip().upper()
        store["logins"][cid] = [c.strip() for c in creds]
        store["last"] = cid
        with open(LOGIN_FILE, "wb") as f:
            f.write(_fernet().encrypt(json.dumps(store).encode()))
        os.chmod(LOGIN_FILE, 0o600)

    def saved(self):
        try:
            with open(LOGIN_FILE, "rb") as f:
                d = json.loads(_fernet().decrypt(f.read()).decode())
            return {"last": d.get("last", ""), "logins": d.get("logins", {})}
        except Exception:
            return {"last": "", "logins": {}}

    def forget(self, cid):
        store = self.saved()
        store["logins"].pop(cid.upper(), None)
        if store["last"] == cid.upper():
            store["last"] = next(iter(store["logins"]), "")
        with open(LOGIN_FILE, "wb") as f:
            f.write(_fernet().encrypt(json.dumps(store).encode()))

    def connect_saved(self, cid=None):
        store = self.saved()
        c = store["logins"].get((cid or store["last"] or "").upper())
        if not c:
            raise ValueError("No saved login for that Client ID")
        return self.connect(*c, save=True)

    def auto_login(self):
        """On server start: env login, else the last saved login."""
        try:
            if all(ANGEL_ENV):
                self.connect(*ANGEL_ENV, save=False)
            elif self.saved()["last"]:
                self.connect_saved()
        except Exception as ex:
            self.login_error = str(ex)
            log.warning("auto login failed: %s", ex)

    # ---------------------------------------------------------------- account
    def available_cash(self):
        val, ts = self._funds
        if time.time() - ts < 20:
            return val
        try:
            f = self.session.funds()
            val = float(f.get("availablecash") or f.get("net") or 0)
        except Exception:
            val = None
        self._funds = (val, time.time())
        return val

    # ---------------------------------------------------------------- engines
    def engine(self, path):
        path = os.path.abspath(path)
        with self.lock:
            e = self.engines.get(path)
            if e is None:
                e = self.engines[path] = Engine(path, self)
            return e

    def peek(self, path):
        return self.engines.get(os.path.abspath(path))

    def autoresume(self):
        for folder in os.listdir(STRATEGIES_ROOT):
            fp = os.path.join(STRATEGIES_ROOT, folder)
            if not os.path.isdir(fp):
                continue
            for name in os.listdir(fp):
                sp = os.path.join(fp, name)
                if read_json(os.path.join(sp, "run_config.json")).get("running"):
                    e = self.engine(sp)
                    try:
                        e.resume_from_disk()
                    except Exception as ex:
                        with e.lock:
                            add_log(e.state, f"Auto-resume failed: {ex}")
                            save_state(e.state)

    def kill_switch(self):
        """Emergency: stop every strategy from entering and exit every position (live = SELL LIMIT orders)."""
        n = 0
        for e in list(self.engines.values()):
            try:
                n += e.exit_all("KILL SWITCH")
            except Exception as ex:
                log.exception("kill switch on %s: %s", e.path, ex)
        return n


BROKER = Broker()
