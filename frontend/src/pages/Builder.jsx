import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, enc, money } from "../api.js";
import { useApp } from "../App.jsx";
import StockPicker from "../components/StockPicker.jsx";

const NEW_FOLDER = "__new__";

function Num({ label, value, onChange, step = "any", min = 0, suffix, hint }) {
  return (
    <label className="field">
      <span>{label}</span>
      <div className="with-suffix">
        <input type="number" inputMode="decimal" step={step} min={min} value={value}
          onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} />
        {suffix && <em>{suffix}</em>}
      </div>
      {hint && <small>{hint}</small>}
    </label>
  );
}

export default function Builder({ edit }) {
  const { meta, flash } = useApp();
  const params = useParams();
  const nav = useNavigate();
  const [folders, setFolders] = useState([]);
  const [folder, setFolder] = useState("");
  const [newFolder, setNewFolder] = useState("");
  const [name, setName] = useState("");
  const [c, setC] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api("/api/strategies").then((r) => {
      const fs = r.folders.map((f) => f.folder);
      setFolders(fs);
      if (!edit) setFolder(fs[0] || NEW_FOLDER);
    }).catch(() => {});
  }, [edit]);

  useEffect(() => {
    if (!meta) return;
    if (edit) {
      api(`/api/strategies/${enc(params.folder)}/${enc(params.file)}`).then((v) => {
        setC({ ...meta.defaults, ...v.config });
        setFolder(params.folder);
        setName(params.file);
      }).catch((e) => flash(e.message, "err"));
    } else {
      setC({ ...meta.defaults, sl_pct: 1 });
    }
  }, [meta, edit, params.folder, params.file]); // eslint-disable-line

  const set = (k) => (v) => setC((o) => ({ ...o, [k]: v }));
  const perPos = useMemo(() => {
    if (!c) return 0;
    if (c.size_mode === "equal") return c.capital / Math.max(c.no_limit ? c.items.length : c.max_open, 1);
    return c.position_amount > 0 ? c.position_amount : c.capital / Math.max(c.max_open, 1);
  }, [c]);

  if (!c || !meta) return <p className="muted pad">Loading...</p>;
  const live = c.mode === "live";
  const loadSecs = Math.round(c.items.length / 2.8);
  const tooMany = c.tf_label && loadSecs > ({ "1 minute": 50, "3 minutes": 150 }[c.tf_label] || 1e9);

  const save = async (e) => {
    e.preventDefault();
    const f = folder === NEW_FOLDER ? newFolder.trim() : folder;
    if (!f || !name.trim()) return flash("Give the folder and the strategy a name", "err");
    setBusy(true);
    try {
      const r = await api("/api/strategies", { method: "POST", body: { folder: f, file: name.trim(), config: c } });
      flash(edit ? "Settings saved" : "Strategy created");
      nav(`/s/${enc(r.folder)}/${enc(r.file)}`);
    } catch (ex) { flash(ex.message, "err"); }
    setBusy(false);
  };

  return (
    <form className="page builder" onSubmit={save}>
      <header className="page-head">
        <div>
          <h1>{edit ? `Edit ${name.replace(/_/g, " ")}` : "New strategy"}</h1>
          <p className="muted">{edit ? "Changes apply straight away if it is running. Open positions get the new target and stop-loss." :
            "Watch a list of stocks and buy when a bullish candle pattern closes."}</p>
        </div>
      </header>

      <section className="panel form-sec">
        <h2>Name</h2>
        <div className="grid3">
          <label className="field">
            <span>Folder</span>
            <select value={folder} onChange={(e) => setFolder(e.target.value)} disabled={edit}>
              {folders.map((f) => <option key={f} value={f}>{f.replace(/_/g, " ")}</option>)}
              <option value={NEW_FOLDER}>New folder...</option>
            </select>
          </label>
          {folder === NEW_FOLDER && (
            <label className="field"><span>New folder name</span>
              <input value={newFolder} onChange={(e) => setNewFolder(e.target.value)} placeholder="Intraday" /></label>
          )}
          <label className="field"><span>Strategy name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} disabled={edit} placeholder="Nifty 50 on 5 min" /></label>
        </div>
      </section>

      <section className="panel form-sec">
        <h2>Paper or real money</h2>
        <div className="mode-switch" role="radiogroup">
          <button type="button" role="radio" aria-checked={!live} className={!live ? "on" : ""} onClick={() => set("mode")("paper")}>
            <b>Paper</b><small>Pretend trades on live prices. Nothing is sent to Angel One.</small>
          </button>
          <button type="button" role="radio" aria-checked={live} className={live ? "on live" : ""} disabled={!meta.allow_live}
            onClick={() => { set("mode")("live"); set("ignore_hours")(false); if (!c.sl_pct) set("sl_pct")(1); }}>
            <b>Live</b><small>{meta.allow_live ? "Real LIMIT orders on your Angel One account." : "Switched off on the server."}</small>
          </button>
        </div>
      </section>

      <section className="panel form-sec">
        <h2>Stocks</h2>
        <div className="grid3">
          <label className="field">
            <span>Exchange</span>
            <select value={c.exchange} onChange={(e) => setC({ ...c, exchange: e.target.value, items: [] })}>
              {meta.exchanges.map((x) => <option key={x}>{x}</option>)}
            </select>
          </label>
        </div>
        <StockPicker exchange={c.exchange} indices={meta.indices[c.exchange]} value={c.items} onChange={set("items")} flash={flash} />
      </section>

      <section className="panel form-sec">
        <h2>Signals</h2>
        <div className="grid3">
          <label className="field">
            <span>Candle timeframe</span>
            <select value={c.tf_label} onChange={(e) => set("tf_label")(e.target.value)}>
              {meta.timeframes.map((t) => <option key={t}>{t}</option>)}
            </select>
            {tooMany && <small className="warn-text">{c.items.length} stocks take about {loadSecs}s to load each candle. Fewer stocks or a longer timeframe will trade on fresher signals.</small>}
          </label>
          <Num label="Needs a fall first (candles)" value={c.trend_lookback} onChange={set("trend_lookback")} step="1"
            hint="0 = off. 3 = close before the pattern must be below the close 3 candles earlier." />
        </div>
        <fieldset className="pattern-grid">
          <legend>Buy on these patterns</legend>
          {meta.patterns.map((p) => (
            <label key={p} className="check">
              <input type="checkbox" checked={c.patterns.includes(p)}
                onChange={() => set("patterns")(c.patterns.includes(p) ? c.patterns.filter((x) => x !== p) : [...c.patterns, p])} />
              {p} <small>{meta.pattern_candles[p]} candle{meta.pattern_candles[p] > 1 ? "s" : ""}</small>
            </label>
          ))}
        </fieldset>
        <fieldset className="chips-field">
          <legend>Ichimoku cloud filter (optional)</legend>
          <div className="chips">
            {meta.cloud_options.map((o) => {
              const on = c.cloud_filter.split(",").includes(o);
              const next = on ? c.cloud_filter.split(",").filter((x) => x && x !== o) : [...c.cloud_filter.split(",").filter(Boolean), o];
              return (
                <button type="button" key={o} className={`chip ${on ? "on" : ""}`} aria-pressed={on}
                  onClick={() => set("cloud_filter")(next.join(","))}>
                  {{ Above: "Price above cloud", Center: "Inside cloud", Below: "Below cloud" }[o]}
                </button>
              );
            })}
          </div>
          <small className="muted">None picked = filter off. Picked = only buy when the closed candle is on that side of the cloud.</small>
        </fieldset>
      </section>

      <section className="panel form-sec">
        <h2>Money</h2>
        <div className="grid3">
          <Num label="Capital for this strategy" value={c.capital} onChange={set("capital")} suffix="Rs" />
          <Num label="Max open positions" value={c.max_open} onChange={set("max_open")} step="1" min={1} />
          <label className="check self-end"><input type="checkbox" checked={c.no_limit} onChange={(e) => set("no_limit")(e.target.checked)} />No limit on positions</label>
        </div>
        <div className="seg" role="radiogroup" aria-label="Position size">
          {[["amount", "Fixed amount"], ["equal", "Equal split"], ["qty", "Fixed quantity"]].map(([k, l]) => (
            <button type="button" role="radio" key={k} aria-checked={c.size_mode === k} className={c.size_mode === k ? "on" : ""}
              onClick={() => set("size_mode")(k)}>{l}</button>
          ))}
        </div>
        <div className="grid3">
          {c.size_mode === "amount" && <Num label="Amount per position" value={c.position_amount} onChange={set("position_amount")} suffix="Rs"
            hint="0 = capital divided by max positions" />}
          {c.size_mode === "qty" && <Num label={c.exchange === "NFO" || c.exchange === "MCX" ? "Lots per position" : "Shares per position"}
            value={c.position_qty} onChange={set("position_qty")} step="1" />}
          {c.size_mode !== "qty" && <div className="calc"><small>Each position gets</small><b>Rs {money(perPos)}</b></div>}
        </div>
      </section>

      <section className="panel form-sec">
        <h2>Exits</h2>
        <div className="grid3">
          <Num label="Target" value={c.target_pct} onChange={set("target_pct")} suffix="%" />
          <Num label="Stop-loss" value={c.sl_pct} onChange={set("sl_pct")} suffix="%" hint={live ? "Required for live" : "0 = no stop-loss"} />
          <label className="field">
            <span>Holding</span>
            <select value={c.product} onChange={(e) => set("product")(e.target.value)}>
              <option value="intraday">Intraday (square off 3:15 pm)</option>
              <option value="carry">Carry overnight (delivery / futures)</option>
            </select>
          </label>
          {c.product === "carry" && <Num label="Exit after (trading days)" value={c.max_hold} onChange={set("max_hold")} step="1" min={1}
            hint="Sold at square-off time on that day if target / stop-loss didn't hit" />}
        </div>
      </section>

      {live ? (
        <section className="panel form-sec live-sec">
          <h2>Live order safety</h2>
          <p className="muted">Exchange rules ban market orders from algos, so every order is a LIMIT order priced just past the live price.</p>
          <div className="grid3">
            <Num label="Buy price above LTP" value={c.entry_buffer_pct} onChange={set("entry_buffer_pct")} suffix="%" />
            <Num label="Sell price below LTP" value={c.exit_buffer_pct} onChange={set("exit_buffer_pct")} suffix="%" />
            <Num label="Wait for fill" value={c.order_timeout_s} onChange={set("order_timeout_s")} step="1" suffix="sec"
              hint="Unfilled buy is cancelled; unfilled sell is re-priced lower" />
            <Num label="Daily loss limit" value={c.daily_loss_limit} onChange={set("daily_loss_limit")} suffix="Rs" hint="0 = off. No new buys once today's loss reaches this" />
            <Num label="Max buys per day" value={c.max_orders_day} onChange={set("max_orders_day")} step="1" />
          </div>
        </section>
      ) : (
        <section className="panel form-sec">
          <h2>Paper test options</h2>
          <div className="grid3">
            <Num label="Brokerage" value={c.brokerage_pct} onChange={set("brokerage_pct")} suffix="%" />
            <Num label="Slippage" value={c.slippage_pct} onChange={set("slippage_pct")} suffix="%" />
            <label className="check self-end"><input type="checkbox" checked={c.ignore_hours} onChange={(e) => set("ignore_hours")(e.target.checked)} />
              Run outside market hours (testing)</label>
          </div>
        </section>
      )}

      <div className="row end sticky-save">
        <button type="button" className="ghost" onClick={() => nav(-1)}>Cancel</button>
        <button className="primary" disabled={busy}>{busy ? "Saving..." : edit ? "Save changes" : "Create strategy"}</button>
      </div>
    </form>
  );
}
