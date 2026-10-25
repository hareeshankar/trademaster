"""Ready-made strategies: one paper strategy per stock list the original app offered.

Runs once on server start (a marker file stops it running again, so strategies you delete stay deleted).
Needs only the public Angel One instrument list - no login.
"""
import logging
import os
import threading
import time

from .angel import instruments as ins
from .models import Config
from .patterns import PATTERNS
from .settings import DATA_DIR
from . import storage

log = logging.getLogger("trademaster.seed")
MARKER = os.path.join(DATA_DIR, ".seeded_v1")

# (folder, exchange, index list name, strategy name)
SEEDS = [
    *[("NSE stocks", "NSE", ix, ix) for ix in (
        "Nifty 50", "Nifty Next 50", "Nifty 100", "Nifty Bank", "Nifty IT", "Nifty Auto", "Nifty Pharma",
        "Nifty FMCG", "Nifty Metal", "Nifty Financial Services", "Nifty Midcap 100")],
    ("BSE stocks", "BSE", "Sensex 30", "Sensex 30"),
    ("NFO futures", "NFO", "Index futures", "Index futures"),
    ("NFO futures", "NFO", "Nifty 50 stock futures", "Nifty 50 stock futures"),
    ("MCX commodities", "MCX", "Bullion", "Bullion"),
    ("MCX commodities", "MCX", "Energy", "Energy"),
    ("MCX commodities", "MCX", "Base Metals", "Base Metals"),
]


def _config(exchange, items):
    futures = exchange in ("NFO", "MCX")
    return Config.from_dict({
        "exchange": exchange, "items": items, "tf_label": "5 minutes", "patterns": list(PATTERNS),
        "target_pct": 1.0, "sl_pct": 1.0, "max_hold": 1, "product": "intraday", "mode": "paper",
        # one futures lot needs ~10% margin of a large contract, so futures lists get more capital
        "capital": 500000.0 if futures else 100000.0, "max_open": 3 if futures else 5,
        "size_mode": "amount", "position_amount": 0,
    })


def seed_once():
    if os.path.exists(MARKER):
        return 0
    made, failed = 0, []
    for folder, exchange, index, name in SEEDS:
        path = storage.strategy_path(folder, name)
        if os.path.exists(os.path.join(path, "config.json")):
            continue
        try:
            items = ins.instrument_list(exchange, [index])
        except Exception as ex:
            failed.append(f"{name}: {ex}")
            continue
        if not items:
            failed.append(f"{name}: empty list")
            continue
        storage.create_folder(folder)
        storage.save_config(folder, name, _config(exchange, items).to_dict())
        made += 1
    if failed:
        log.warning("seed incomplete, will retry: %s", "; ".join(failed))
        return -1
    with open(MARKER, "w") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    log.info("seeded %d ready-made strategies", made)
    return made


def seed_in_background():
    def run():
        for _ in range(30):                      # retry up to ~30 min if a download fails
            try:
                if seed_once() >= 0:
                    return
            except Exception:
                log.exception("seed failed")
            time.sleep(60)
    threading.Thread(target=run, name="seed", daemon=True).start()
