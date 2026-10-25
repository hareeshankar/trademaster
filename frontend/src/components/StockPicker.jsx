import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";

const qs = (exchange, idx) => `?exchange=${exchange}` + idx.map((i) => `&index=${encodeURIComponent(i)}`).join("");

/** Pick instruments for one exchange: index filter, search, tick boxes, or type names. */
export default function StockPicker({ exchange, indices, value, onChange, flash }) {
  const [idx, setIdx] = useState(indices?.[0] ? [indices[0]] : []);
  const [list, setList] = useState([]);
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const [typed, setTyped] = useState("");

  useEffect(() => { setIdx(indices?.[0] ? [indices[0]] : []); }, [exchange]); // eslint-disable-line

  useEffect(() => {
    let live = true;
    setBusy(true);
    api(`/api/instruments${qs(exchange, idx)}`)
      .then((r) => live && setList(r.items))
      .catch((e) => live && (setList([]), flash(e.message, "err")))
      .finally(() => live && setBusy(false));
    return () => { live = false; };
  }, [exchange, idx.join("|")]); // eslint-disable-line

  const chosen = useMemo(() => new Set(value.map((v) => v.token)), [value]);
  const shown = useMemo(() => {
    const s = q.trim().toUpperCase();
    return s ? list.filter((x) => x.symbol.toUpperCase().includes(s) || x.tradingsymbol.toUpperCase().includes(s)) : list;
  }, [list, q]);

  const toggle = (it) => onChange(chosen.has(it.token) ? value.filter((v) => v.token !== it.token) : [...value, it]);
  const addAll = () => {
    const add = shown.filter((x) => !chosen.has(x.token));
    onChange([...value, ...add]);
  };
  const addTyped = async () => {
    const names = typed.split(/[,\s]+/).filter(Boolean);
    if (!names.length) return;
    try {
      const r = await api("/api/instruments/find", { method: "POST", body: { exchange, names } });
      const add = r.items.filter((x) => !chosen.has(x.token));
      onChange([...value, ...add]);
      setTyped("");
      if (r.missing.length) flash(`Not found on ${exchange}: ${r.missing.join(", ")}`, "err");
    } catch (e) { flash(e.message, "err"); }
  };

  return (
    <div className="picker">
      <div className="chips" role="group" aria-label="Index">
        {(indices || []).map((i) => (
          <button type="button" key={i} className={`chip ${idx.includes(i) ? "on" : ""}`} aria-pressed={idx.includes(i)}
            onClick={() => setIdx(idx.includes(i) ? idx.filter((x) => x !== i) : [...idx, i])}>{i}</button>
        ))}
      </div>
      <div className="picker-grid">
        <div className="picker-list">
          <div className="row">
            <input placeholder={`Search ${list.length} instruments`} value={q} onChange={(e) => setQ(e.target.value)} />
            <button type="button" className="ghost small" onClick={addAll} disabled={!shown.length}>Add all {shown.length}</button>
          </div>
          <div className="list-box">
            {busy && <p className="empty">Loading list from Angel One...</p>}
            {!busy && shown.slice(0, 400).map((it) => (
              <label key={it.token} className="pick-row">
                <input type="checkbox" checked={chosen.has(it.token)} onChange={() => toggle(it)} />
                <span>{it.symbol}</span>
                {it.symbol !== it.tradingsymbol && <small>{it.tradingsymbol}</small>}
                {it.lot > 1 && <small>lot {it.lot}</small>}
              </label>
            ))}
            {!busy && shown.length > 400 && <p className="empty">Showing 400 of {shown.length}. Search to narrow down.</p>}
            {!busy && !shown.length && <p className="empty">Nothing matches. Connect Angel One if the list is empty.</p>}
          </div>
          <div className="row">
            <input placeholder="Or type names: SBIN, TCS, INFY" value={typed} onChange={(e) => setTyped(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addTyped())} />
            <button type="button" className="ghost small" onClick={addTyped}>Add</button>
          </div>
        </div>
        <div className="picker-sel">
          <div className="row between">
            <b>{value.length} selected</b>
            {value.length > 0 && <button type="button" className="ghost small" onClick={() => onChange([])}>Clear</button>}
          </div>
          <div className="sel-box">
            {value.map((v) => (
              <button type="button" key={v.token} className="sel" onClick={() => toggle(v)} title="Remove">
                {v.symbol} <span aria-hidden="true">x</span>
              </button>
            ))}
            {!value.length && <p className="empty">Tick stocks on the left.</p>}
          </div>
        </div>
      </div>
    </div>
  );
}
