import os, sys, shutil
os.environ["TM_DATA_DIR"] = "/tmp/tm_api_test"; shutil.rmtree("/tmp/tm_api_test", ignore_errors=True)
os.environ["TM_OWNER_PASSWORD"] = "correct horse battery"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fastapi.testclient import TestClient
from app.main import app
from app.engine.registry import BROKER
BROKER.auto_login = lambda: None
with TestClient(app) as c:
    assert c.get("/api/strategies").status_code == 401
    assert c.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    tok = c.post("/api/auth/login", json={"password": "correct horse battery"}).json()["token"]
    H = {"Authorization": f"Bearer {tok}"}
    m = c.get("/api/meta", headers=H).json(); assert len(m["patterns"]) == 10
    item = {"symbol": "SBIN", "tradingsymbol": "SBIN-EQ", "token": "3045", "exchange": "NSE", "lot": 1, "tick": 0.05}
    r = c.post("/api/strategies", headers=H, json={"folder": "Intraday", "file": "Nifty 5m", "config": {"items": [item]}})
    print(r.status_code, r.json())
    r = c.post("/api/strategies", headers=H, json={"folder": "Intraday", "file": "Bad", "config": {"items": [item], "mode": "live", "sl_pct": 0}})
    print("live blocked:", r.status_code, r.json()["detail"])
    print([ (f["folder"], [x["file"] for x in f["files"]]) for f in c.get("/api/strategies", headers=H).json()["folders"]])
    v = c.get("/api/strategies/Intraday/Nifty_5m", headers=H).json(); print("view keys:", sorted(v)[:8], v["config"]["sl_pct"])
    r = c.post("/api/strategies/Intraday/Nifty_5m/start", headers=H, json={}); print("start w/o angel:", r.status_code, r.json()["detail"])
    r = c.get("/api/strategies/Intraday/Nifty_5m/csv/excel-check", headers=H); print("csv:", r.status_code, r.text[:60])
    print(c.get("/api/account", headers=H).json()["connected"])
print("API OK")
