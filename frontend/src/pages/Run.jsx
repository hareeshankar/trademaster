import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, download, enc, money, px, sign } from "../api.js";
import { useApp } from "../App.jsx";
import Confirm from "../components/Confirm.jsx";
import SessionBar from "../components/SessionBar.jsx";
import Table from "../components/Table.jsx";

const TABS = [["watch", "Watchlist"], ["positions", "Positions"], ["signals", "Signals"], ["closed", "Closed trades"],
  ["orders", "Orders"], ["log", "Log"]];
const STATUS_TXT = { OPEN: "Open", PENDING_ENTRY: "Buying", PENDING_EXIT: "Selling", STUCK: "Exit stuck" };

export default function Run() {
  const { folder, file } = useParams();
  const { flash, account } = useApp();
  const nav = useNavigate();
  const base = `/api/strategies/${enc(folder)}/${enc(file)}`;
  const [v, setV] = useState(null);
  const [tab, setTab] = useState("watch");
  const [sel, setSel] = useState(new Set());
  const [ask, setAsk] = useState(null);
  const [busy, setBusy] = useState(false);
  const [buySym, setBuySym] = useState("");
  const timer = useRef(null);

  const load = useCallback(async () => {
    try { setV(await api(base)); } catch (e) { if (e.status === 404) nav("/"); }
  }, [base, nav]);

  useEffect(() => {
    load();
    const tickFn = () => { if (!document.hidden) load(); };
    timer.current = setInterval(tickFn, 1500);
    return () => clearInterval(timer.current);
  }, [load]);

  const act = async (action, body = {}, ok) => {
    setBusy(true);
    try {
      setV(await api(`${base}/${action}`, { method: "POST", body }));
      if (ok) flash(ok);
      return true;
    } catch (e) {
      if (e.status === 409 && action === "stop") setAsk({ kind: "stop-force", msg: e.message });
      else flash(e.message, "err");
      return false;
    } finally { setBusy(false); }
  };

  if (!v) return <p className="muted pad">Loading...</p>;
  const c = v.config;
  const live = c.mode === "live";
  const s = v.summary;
  const name = file.replace(/_/g, " ");
  const csv = (kind) => download(`${base}/csv/${kind}`, `${file}_${kind}.csv`).catch((e) => flash(e.message, "err"));
  const openKeys = v.open.map((p) => p.key);
  const chosen = [...sel].filter((k) => openKeys.includes(k));

  return (
    <div className="page run">
      <header className="run-head">
        <div className="title">
          <button className="ghost small back" onClick={() => nav("/")}>All strategies</button>
          <h1>{name} <span className={`mode ${c.mode}`}>{live ? "Live" : "Paper"}</span></h1>
          <p className="muted">{folder.replace(/_/g, " ")} - {c.exchange} {c.tf_label}, {c.items.length} stocks, target {c.target_pct}%,
            stop-loss {c.sl_pct ? `${c.sl_pct}%` : "off"}, {c.product === "carry" ? `hold up to ${c.max_hold} days` : "intraday"}</p>
        </div>
        <div className="controls">
          {!v.running ? (
            <button className="primary" disabled={busy || !account?.connected}
              title={account?.connected ? "" : "Connect Angel One first"}
              onClick={() => live ? setAsk({ kind: "live-start" }) : act("start", {}, v.has_run ? "Resumed" : "Started")}>
              {v.has_run ? "Resume" : "Start"}
            </button>
          ) : (
            <button className="ghost" disabled={busy} onClick={() => act("pause", { on: !v.paused }, v.paused ? "Entries back on" : "Entries paused")}>
              {v.paused ? "Allow new entries" : "Pause new entries"}
            </button>
          )}
          {v.running && <button className="ghost" disabled={busy} onClick={() => act("stop", {}, "Stopped")}>Stop</button>}
          <button className="danger-ghost" disabled={busy || !v.open.length} onClick={() => setAsk({ kind: "exit-all" })}>Exit all</button>
          <button className="ghost" onClick={() => nav(`/edit/${enc(folder)}/${enc(file)}`)}>Edit</button>
        </div>
      </header>

      <div className={`status-line ${v.running ? "on" : ""}`}>
        <span className="dot" />
        <span>{v.running ? v.status : "Stopped"}</span>
        <span className="muted">{v.market_open ? `${c.exchange} open` : `${c.exchange} closed`}</span>
        <span className="muted">{v.feed.connected ? "live ticks" : "feed reconnecting"}</span>
        {v.last_update && <span className="muted">updated {v.last_update.slice(11, 19)}</span>}
      </div>

      <SessionBar exchange={c.exchange} tfMin={c.tf_min} now={v.now} closed={v.closed} open={v.open} />

      {v.errors.length > 0 && (
        <div className="alert" role="alert">{v.errors.slice(0, 4).map((e, i) => <div key={i}>{e}</div>)}</div>
      )}

      <section className="ledger">
        <div><small>Open positions</small><b>{s.open}{c.no_limit ? "" : ` / ${c.max_open}`}</b></div>
        <div><small>Closed trades</small><b>{s.closed}</b></div>
        <div><small>Booked P&L</small><b className={sign(s.realized)}>{money(s.realized)}</b></div>
        <div><small>Open P&L</small><b className={sign(s.unrealized)}>{money(s.unrealized)}</b></div>
        <div><small>Win rate</small><b>{s.win_rate === null ? "-" : `${s.win_rate}%`}</b></div>
      </section>

      <div className="tabs" role="tablist">
        {TABS.filter(([k]) => k !== "orders" || live || v.orders.length).map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
            {l}{k === "positions" && v.open.length ? <em>{v.open.length}</em> : null}
            {k === "signals" && v.signals.length ? <em>{v.signals.length}</em> : null}
          </button>
        ))}
        <span className="push" />
        <button className="ghost small" onClick={() => csv({ watch: "monitoring", positions: "positions", signals: "signals",
          closed: "closed", orders: "orders", log: "logs" }[tab])}>Download CSV</button>
        <button className="ghost small" onClick={() => csv("excel-check")} title="Every entry's candles with your 10 Excel formulas">Excel check</button>
      </div>

      {tab === "watch" && (
        <Table rows={v.rows} empty={v.running ? "Loading candles..." : "Start the strategy to see live prices and what each stock needs to signal."}
          rowKey={(r) => r.symbol} rowClass={(r) => (r.position !== "-" ? "held" : "")} cols={[
            ["Stock", (r) => <b>{r.symbol}</b>], ["LTP", (r) => px(r.ltp), "num"],
            ["Change vs last candle", (r) => r.change_pct === null ? "-" : <span className={sign(r.change_pct)}>{r.change_pct}%</span>, "num"],
            ["Candle open", (r) => px(r.open), "num"], ["High", (r) => px(r.high), "num"], ["Low", (r) => px(r.low), "num"],
            ["Last close", (r) => px(r.prev_close), "num"],
            ...(c.cloud_filter ? [["Cloud", "cloud"]] : []),
            ["Position", "position"],
            ["Needs on this candle", (r) => r.outlook.length ? <span className="outlook">{r.outlook.map((x) => <span key={x}>{x}</span>)}</span>
              : <span className="muted">Checked when the candle closes</span>],
          ]} />
      )}

      {tab === "positions" && (
        <>
          <div className="row toolbar">
            <button className="danger-ghost small" disabled={!chosen.length || busy}
              onClick={() => act("exit", { keys: chosen }, `Exit sent for ${chosen.length}`).then(() => setSel(new Set()))}>
              Exit selected ({chosen.length})
            </button>
            <span className="push" />
            <select value={buySym} onChange={(e) => setBuySym(e.target.value)} aria-label="Stock to buy">
              <option value="">Buy a stock now...</option>
              {c.items.map((it) => <option key={it.token} value={it.symbol}>{it.symbol}</option>)}
            </select>
            <button className="ghost small" disabled={!buySym || !v.running || busy}
              onClick={() => live ? setAsk({ kind: "buy", sym: buySym }) : act("buy", { symbol: buySym }, `Bought ${buySym}`)}>Buy</button>
            <button className="ghost small" disabled={!v.running || busy}
              onClick={() => setAsk({ kind: "buy-all" })}>Buy all</button>
          </div>
          <Table rows={v.open} rowKey={(p) => p.key} empty="No open positions." cols={[
            [<input type="checkbox" aria-label="Select all" checked={chosen.length > 0 && chosen.length === openKeys.length}
              onChange={(e) => setSel(new Set(e.target.checked ? openKeys : []))} />,
              (p) => <input type="checkbox" aria-label={`Select ${p.symbol}`} checked={sel.has(p.key)}
                onChange={() => { const n = new Set(sel); n.has(p.key) ? n.delete(p.key) : n.add(p.key); setSel(n); }} />],
            ["#", "slot"], ["Stock", (p) => <b>{p.symbol}</b>], ["Pattern", "pattern"],
            ["Bought", (p) => String(p.entry_time).slice(5, 16)], ["Qty", "qty", "num"], ["Entry", (p) => px(p.entry), "num"],
            ["Target", (p) => px(p.target), "num"], ["Stop", (p) => px(p.stop_loss), "num"], ["LTP", (p) => px(p.last_price), "num"],
            ["P&L", (p) => <span className={sign(p.pnl)}>{px(p.pnl)}</span>, "num"],
            ["%", (p) => <span className={sign(p.pnl_pct)}>{p.pnl_pct}%</span>, "num"],
            ["State", (p) => <span className={`st ${p.status || "OPEN"}`}>{STATUS_TXT[p.status || "OPEN"]}{p.order_status && p.status !== "OPEN" ? `: ${p.order_status}` : ""}</span>],
            ...(!live ? [["", (p) => <button className="link" onClick={() => setAsk({ kind: "del-pos", key: p.key, sym: p.symbol })}>Remove</button>]] : []),
          ]} />
        </>
      )}

      {tab === "signals" && (
        <Table rows={v.signals} empty="No signals yet. They appear when a candle closes with a pattern." maxHeight="60vh" cols={[
          ["Time", (g) => String(g.time).slice(5)], ["Stock", (g) => <b>{g.symbol}</b>], ["Pattern", "pattern"],
          ["Candle", (g) => String(g.candle).slice(5, 16)], ...(c.cloud_filter ? [["Cloud", "cloud"]] : []),
          ["Result", (g) => <span className={g.status.startsWith("ENTERED") || g.status.startsWith("ORDER") ? "up" : "muted"}>{g.status}</span>],
        ]} />
      )}

      {tab === "closed" && (
        <Table rows={v.closed} empty="No closed trades yet." maxHeight="60vh" cols={[
          ["#", "slot"], ["Stock", (t) => <b>{t.symbol}</b>], ["Pattern", "pattern"],
          ["Bought", (t) => String(t.entry_time).slice(5, 16)], ["Entry", (t) => px(t.entry), "num"], ["Qty", "qty", "num"],
          ["Sold", (t) => String(t.exit_time).slice(5, 16)], ["Exit", (t) => px(t.exit), "num"], ["Why", "reason"],
          ["P&L", (t) => <span className={sign(t.pnl)}>{px(t.pnl)}</span>, "num"],
          ["%", (t) => <span className={sign(t.pnl_pct)}>{t.pnl_pct}%</span>, "num"], ["R", "r_multiple", "num"],
          ...(!live ? [["", (t, i) => <button className="link" onClick={() => setAsk({ kind: "del-closed", t, i })}>Delete</button>]] : []),
        ]} />
      )}

      {tab === "orders" && (
        <Table rows={v.orders} empty="No orders sent yet." maxHeight="60vh" cols={[
          ["Time", (o) => String(o.time).slice(5)], ["Order", "order_id"], ["Side", "side"], ["Stock", "symbol"],
          ["Qty", "qty", "num"], ["Limit", (o) => px(o.price), "num"], ["Filled", "filled", "num"], ["Avg", (o) => px(o.avg), "num"],
          ["Status", (o) => <span className={`st ${String(o.status).replace(/\s/g, "-")}`}>{o.status}</span>], ["Note", (o) => o.text || o.note],
        ]} />
      )}

      {tab === "log" && (
        <div className="log">{v.log.length ? v.log.map((l, i) => {
          const [t, ...rest] = l.split("  ");
          const msg = rest.join("  ");
          const ev = msg.split(" ")[0];
          return <div key={i} className={`ev ${ev}`}><time>{t.slice(5)}</time><span>{msg}</span></div>;
        }) : <p className="empty">No events yet.</p>}</div>
      )}

      <div className="row foot-actions">
        {!live && <button className="link" onClick={() => setAsk({ kind: "reset" })}>Clear all trades of this strategy</button>}
        <button className="link" onClick={() => setAsk({ kind: "delete" })}>Delete strategy</button>
      </div>

      {ask?.kind === "live-start" && (
        <Confirm title="Start real-money trading?" danger typed="LIVE" okText="Start live" onCancel={() => setAsk(null)}
          onOk={() => { setAsk(null); act("start", { confirm: "LIVE" }, "Live trading started"); }}>
          Real BUY and SELL LIMIT orders will be placed on {account?.client_id} for up to Rs {money(c.capital)}, as signals come.
          Orders only go through if this server's IP is the one registered in your SmartAPI dashboard.
        </Confirm>
      )}
      {ask?.kind === "stop-force" && (
        <Confirm title="Stop with live positions open?" danger okText="Stop anyway" onCancel={() => setAsk(null)}
          onOk={() => { setAsk(null); act("stop", { force: true }, "Stopped"); }}>
          {ask.msg} Use "Pause new entries" instead to keep exits watched.
        </Confirm>
      )}
      {ask?.kind === "exit-all" && (
        <Confirm title={`Exit all ${v.open.length} positions?`} danger okText="Exit all" onCancel={() => setAsk(null)}
          onOk={() => { setAsk(null); act("exit-all", {}, live ? "Sell orders sent" : "All positions closed"); }}>
          {live ? "SELL LIMIT orders go out now and are re-priced until filled. The strategy stops once everything is sold."
            : "Every paper position closes at its last price and the strategy stops."}
        </Confirm>
      )}
      {ask?.kind === "buy" && (
        <Confirm title={`Buy ${ask.sym} now?`} okText="Send buy order" onCancel={() => setAsk(null)}
          onOk={() => { setAsk(null); act("buy", { symbol: ask.sym }, `Buy order sent for ${ask.sym}`); }}>
          A real BUY LIMIT order, sized like this strategy's pattern entries.
        </Confirm>
      )}
      {ask?.kind === "buy-all" && (
        <Confirm title={`Buy all ${c.items.length} stocks?`} danger={live} okText="Buy all" onCancel={() => setAsk(null)}
          onOk={() => { setAsk(null); act("buy-all", {}, "Buying until positions are full"); }}>
          One {live ? "real BUY order" : "paper buy"} per stock, until the positions are full or cash runs out.
        </Confirm>
      )}
      {ask?.kind === "del-pos" && (
        <Confirm title={`Remove ${ask.sym}?`} okText="Remove" onCancel={() => setAsk(null)}
          onOk={() => { setAsk(null); act("delete-position", { symbol: ask.key }, "Removed"); }}>
          The paper position disappears with no trade recorded.
        </Confirm>
      )}
      {ask?.kind === "del-closed" && (
        <Confirm title={`Delete the ${ask.t.symbol} trade?`} okText="Delete" onCancel={() => setAsk(null)}
          onOk={() => { setAsk(null); act("delete-closed", { index: v.closed.length - 1 - ask.i, trade: ask.t }, "Deleted"); }}>
          It is removed from the closed trades and P&L.
        </Confirm>
      )}
      {ask?.kind === "reset" && (
        <Confirm title="Clear all trades?" danger okText="Clear everything" onCancel={() => setAsk(null)}
          onOk={() => { setAsk(null); act("reset", {}, "Cleared"); }}>
          Open positions, closed trades, signals and the log of this paper strategy are wiped. Settings stay.
        </Confirm>
      )}
      {ask?.kind === "delete" && (
        <Confirm title={`Delete ${name}?`} danger okText="Delete strategy" onCancel={() => setAsk(null)}
          onOk={async () => {
            setAsk(null);
            try { await api(base, { method: "DELETE" }); flash("Strategy deleted"); nav("/"); } catch (e) { flash(e.message, "err"); }
          }}>
          The settings and all trade history of this strategy are deleted.
        </Confirm>
      )}
    </div>
  );
}
