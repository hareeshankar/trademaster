"""Strategy folders/files on disk + crash-safe trade state. Same layout as the Streamlit app, so old files load."""
import json
import os
import re
import shutil
import threading

import pandas as pd

from .markettime import now_ist
from .settings import DATA_DIR

STRATEGIES_ROOT = os.path.join(DATA_DIR, "strategies")
os.makedirs(STRATEGIES_ROOT, exist_ok=True)


def safe_name(name):
    name = re.sub(r"[^\w\s\-]", "", str(name).strip())
    name = re.sub(r"\s+", "_", name)
    return name[:80] or "unnamed"


def read_json(path, default=None):
    """json.load that survives an empty / half-written / missing file."""
    dflt = {} if default is None else default
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, type(dflt)) else dflt
    except Exception:
        return dflt


def write_json(path, data):
    tmp = f"{path}.{threading.get_ident()}.tmp"          # write whole file, then swap -> never half-written
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, default=str)
    os.replace(tmp, path)


def strategy_path(folder, file):
    return os.path.join(STRATEGIES_ROOT, safe_name(folder), safe_name(file))


def list_strategies():
    """{folder: [ {folder, file, path, config...}, ... ]} - empty folders kept."""
    out = {}
    for folder in sorted(os.listdir(STRATEGIES_ROOT)):
        fp = os.path.join(STRATEGIES_ROOT, folder)
        if not os.path.isdir(fp) or folder.startswith("."):
            continue
        files = []
        for fname in sorted(os.listdir(fp)):
            spath = os.path.join(fp, fname)
            cfg = os.path.join(spath, "config.json")
            if os.path.isdir(spath) and not fname.startswith(".") and os.path.exists(cfg):
                files.append({"folder": folder, "file": fname, "path": spath, "config": read_json(cfg)})
        out[folder] = files
    return out


def create_folder(name):
    safe = safe_name(name)
    os.makedirs(os.path.join(STRATEGIES_ROOT, safe), exist_ok=True)
    return safe


def delete_folder(name):
    fp = os.path.join(STRATEGIES_ROOT, safe_name(name))
    if os.path.isdir(fp):
        shutil.rmtree(fp, ignore_errors=True)


def save_config(folder, file, data):
    path = strategy_path(folder, file)
    os.makedirs(path, exist_ok=True)
    data = dict(data)
    data.update({"folder": safe_name(folder), "file": safe_name(file), "updated": str(now_ist())})
    write_json(os.path.join(path, "config.json"), data)
    return path


def load_config(path):
    return read_json(os.path.join(path, "config.json"))


def delete_strategy(path):
    if os.path.isdir(path) and os.path.abspath(path).startswith(os.path.abspath(STRATEGIES_ROOT)):
        shutil.rmtree(path, ignore_errors=True)


# ------------------------------------------------------------------ trade state
def load_state(path):
    s = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                s = json.load(f)
            if not isinstance(s, dict):
                raise ValueError("state is not a dict")
        except Exception:
            try:
                os.replace(path, path + ".corrupt")          # keep the broken file, start clean
            except OSError:
                pass
            s = {}
    for k in ("open", "closed", "handled", "log", "signals", "orders"):
        s.setdefault(k, [])
    s.setdefault("slot_last", {})
    s.setdefault("day", {})                                   # {"YYYY-MM-DD": {"entries": n}}
    s["_path"] = path
    for g in s["signals"]:
        if str(g.get("status", "")).startswith(("WAITING", "EXPIRED")):
            g["status"] = "SKIPPED (positions were full)"
    return s


def save_state(state):
    path = state.get("_path")
    if path:
        write_json(path, {k: v for k, v in state.items() if not k.startswith("_")})


def add_log(state, msg, now=None):
    state["log"].append(f"{pd.Timestamp(now or now_ist()):%Y-%m-%d %H:%M:%S}  {msg}")
    state["log"] = state["log"][-500:]


def fill_slots(state):
    used = {p["slot"] for p in state["open"] if p.get("slot")}
    for p in sorted([p for p in state["open"] if not p.get("slot")], key=lambda p: str(p.get("entry_time"))):
        n = 1
        while n in used:
            n += 1
        p["slot"] = n
        used.add(n)


def pick_slot(state, now):
    """Reuse today's freed position number first (so #3 stays #3 after its exit), else the lowest free one."""
    fill_slots(state)
    used = {p["slot"] for p in state["open"]}
    today = f"{pd.Timestamp(now):%Y-%m-%d}"
    last = state.setdefault("slot_last", {})
    fresh = [(str(v.get("exit_time", "")), int(k)) for k, v in last.items()
             if int(k) not in used and str(v.get("exit_time", ""))[:10] == today]
    if fresh:
        slot = min(fresh)[1]
        return slot, last[str(slot)]
    n = 1
    while n in used:
        n += 1
    return n, None
