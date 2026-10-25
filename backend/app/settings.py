"""Server settings, read from environment variables (set in /etc/trademaster.env on the server)."""
import os

DATA_DIR = os.environ.get("TM_DATA_DIR") or os.path.join(os.path.expanduser("~"), "trademaster_data")
os.makedirs(DATA_DIR, exist_ok=True)

# Owner login for the app (single user). TM_OWNER_PASSWORD is only used to create the hash on first start.
OWNER_PASSWORD = os.environ.get("TM_OWNER_PASSWORD", "")
SECRET_KEY = os.environ.get("TM_SECRET_KEY", "")          # signs login tokens + encrypts saved Angel logins

# Live (real money) orders are refused unless this is 1 - a server-side master switch.
ALLOW_LIVE = os.environ.get("TM_ALLOW_LIVE", "0") == "1"

# Front-end origins allowed to call the API (your Vercel URL). Comma separated.
CORS_ORIGINS = [o.strip() for o in os.environ.get("TM_CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]

# Optional: Angel login from env so the server can auto-login and auto-resume after a reboot.
ANGEL_ENV = tuple(os.environ.get(k, "").strip() for k in
                  ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_MPIN", "ANGEL_TOTP_SECRET"))
