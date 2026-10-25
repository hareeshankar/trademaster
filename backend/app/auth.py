"""Single-owner login: password (scrypt hash on disk) -> signed token (12 h)."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from .settings import DATA_DIR, OWNER_PASSWORD, SECRET_KEY

OWNER_FILE = os.path.join(DATA_DIR, "owner.json")
TOKEN_HOURS = 12
_fails = []


def _secret():
    if SECRET_KEY:
        return SECRET_KEY.encode()
    kp = os.path.join(DATA_DIR, ".secret")
    if not os.path.exists(kp):
        with open(kp, "w") as f:
            f.write(base64.urlsafe_b64encode(os.urandom(32)).decode())
        os.chmod(kp, 0o600)
    return open(kp).read().strip().encode()


def _hash(pw, salt):
    return hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1).hex()


def ensure_owner():
    """First start: create the owner hash from TM_OWNER_PASSWORD (the env value is never stored)."""
    if os.path.exists(OWNER_FILE) or not OWNER_PASSWORD:
        return
    salt = os.urandom(16)
    with open(OWNER_FILE, "w") as f:
        json.dump({"salt": salt.hex(), "hash": _hash(OWNER_PASSWORD, salt)}, f)
    os.chmod(OWNER_FILE, 0o600)


def set_password(new_pw):
    salt = os.urandom(16)
    with open(OWNER_FILE, "w") as f:
        json.dump({"salt": salt.hex(), "hash": _hash(new_pw, salt)}, f)
    os.chmod(OWNER_FILE, 0o600)


def check_password(pw):
    now = time.time()
    while _fails and now - _fails[0] > 300:
        _fails.pop(0)
    if len(_fails) >= 8:
        raise PermissionError("Too many wrong passwords - wait 5 minutes")
    try:
        d = json.load(open(OWNER_FILE))
    except (OSError, ValueError):
        raise PermissionError("Owner password not set on the server (TM_OWNER_PASSWORD)")
    ok = hmac.compare_digest(_hash(pw, bytes.fromhex(d["salt"])), d["hash"])
    if not ok:
        _fails.append(now)
        time.sleep(0.5)
    return ok


def make_token():
    body = f"owner.{int(time.time()) + TOKEN_HOURS * 3600}.{secrets.token_hex(8)}"
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_token(tok):
    try:
        who, exp, nonce, sig = tok.split(".")
    except (ValueError, AttributeError):
        return False
    body = f"{who}.{exp}.{nonce}"
    good = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, sig) and int(exp) > time.time()
