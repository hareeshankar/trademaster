# TradeMaster

Pattern trading on Angel One (SmartAPI): React screens + Python engine on one server.
Rebuilt from the Streamlit `Trading_paper_only.py` app - same 10 candle patterns (verified identical), same
strategy folders / files, plus live orders, WebSocket prices, risk limits and a kill switch.

* `backend/` - FastAPI + engine (patterns, Ichimoku filter, paper + live executors, Angel One REST + WebSocket V2)
* `frontend/` - React (Vite). `frontend/dist` is the built version the server serves.
* `deploy/install.sh` - one-shot Ubuntu server install (HTTPS via Caddy on `<ip>.sslip.io`)
* `deploy/update.sh` - pull latest code and restart

Secrets (owner password, Angel login) live only on the server (`/etc/trademaster.env`, encrypted login store),
never in this repo.

Live orders follow the Angel One rules in force since 1 Apr 2026: LIMIT orders only (no market / IOC),
under 10 orders per second, sent from the static IP registered in the SmartAPI dashboard.
