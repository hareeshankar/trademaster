import { useEffect, useState } from "react";
import { api, money, px, sign } from "../api.js";
import { useApp } from "../App.jsx";
import Table from "../components/Table.jsx";

export default function Account() {
  const { account, refreshAccount, flash } = useApp();
  const [form, setForm] = useState({ api_key: "", client_id: "", mpin: "", totp_secret: "", save: true });
  const [busy, setBusy] = useState(false);
  const [book, setBook] = useState(null);
  const [tab, setTab] = useState("positions");

  const loadBook = async () => {
    try { setBook(await api("/api/account/broker")); } catch (e) { flash(e.message, "err"); }
  };
  useEffect(() => { if (account?.connected) loadBook(); }, [account?.connected]); // eslint-disable-line

  const run = async (fn, ok) => {
    setBusy(true);
    try { await fn(); await refreshAccount(); if (ok) flash(ok); } catch (e) { flash(e.message, "err"); }
    setBusy(false);
  };
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  if (!account) return <p className="muted pad">Loading...</p>;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Angel One account</h1>
          <p className="muted">Market data, live prices and real orders all go through this login.</p>
        </div>
      </header>

      {account.connected ? (
        <>
          <section className="panel">
            <div className="kv-grid">
              <div><small>Client ID</small><b>{account.client_id}</b></div>
              <div><small>Name</small><b>{account.name || "-"}</b></div>
              <div><small>Live feed</small><b className={account.feed.connected ? "up" : "down"}>
                {account.feed.connected ? `Connected (${account.feed.instruments})` : "Reconnecting"}</b></div>
              <div><small>This server's IP</small><b>{account.server_ip}</b></div>
              <div><small>Real orders</small><b>{account.allow_live ? "Allowed on this server" : "Switched off (paper only)"}</b></div>
            </div>
            <p className="note">
              Angel One only accepts orders from the static IP registered in your SmartAPI dashboard.
              It must match <b>{account.server_ip}</b>.
            </p>
            {account.feed.error && <p className="err">Feed: {account.feed.error}</p>}
            <div className="row">
              <button className="ghost" disabled={busy} onClick={() => run(() => api("/api/account/disconnect", { method: "POST" }), "Disconnected")}>Disconnect</button>
              <button className="ghost" onClick={loadBook}>Refresh broker data</button>
            </div>
          </section>

          {book && (
            <section className="panel">
              <div className="kv-grid">
                <div><small>Available cash</small><b>{money(book.funds.availablecash, 2)}</b></div>
                <div><small>Net</small><b>{money(book.funds.net, 2)}</b></div>
                <div><small>Utilised</small><b>{money(book.funds.utiliseddebits, 2)}</b></div>
                <div><small>Realised M2M</small><b className={sign(book.funds.m2mrealized)}>{money(book.funds.m2mrealized, 2)}</b></div>
              </div>
              <div className="tabs" role="tablist">
                {["positions", "orders", "holdings"].map((t) => (
                  <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
                    {t[0].toUpperCase() + t.slice(1)} <em>{book[t].length}</em>
                  </button>
                ))}
              </div>
              {tab === "positions" && (
                <Table rows={book.positions} empty="No positions at the broker today." cols={[
                  ["Symbol", "tradingsymbol"], ["Product", "producttype"], ["Net qty", "netqty", "num"],
                  ["Buy avg", (r) => px(r.buyavgprice), "num"], ["Sell avg", (r) => px(r.sellavgprice), "num"],
                  ["LTP", (r) => px(r.ltp), "num"], ["P&L", (r) => <span className={sign(r.pnl)}>{px(r.pnl)}</span>, "num"],
                ]} />
              )}
              {tab === "orders" && (
                <Table rows={book.orders} empty="No orders today." cols={[
                  ["Time", "updated"], ["Order", "order_id"], ["Side", "side"], ["Symbol", "symbol"],
                  ["Qty", "qty", "num"], ["Price", (r) => px(r.price), "num"], ["Filled", "filled", "num"],
                  ["Avg", (r) => px(r.avg), "num"], ["Status", (r) => <span className={`st ${r.status.replace(/\s/g, "-")}`}>{r.status}</span>],
                  ["Reason", "text"],
                ]} />
              )}
              {tab === "holdings" && (
                <Table rows={book.holdings} empty="No holdings." cols={[
                  ["Symbol", "tradingsymbol"], ["Qty", "quantity", "num"], ["Avg", (r) => px(r.averageprice), "num"],
                  ["LTP", (r) => px(r.ltp), "num"], ["P&L", (r) => <span className={sign(r.profitandloss)}>{px(r.profitandloss)}</span>, "num"],
                ]} />
              )}
              {book.errors.length > 0 && <p className="err">{book.errors.join(" | ")}</p>}
            </section>
          )}
        </>
      ) : (
        <div className="split">
          {account.saved_ids.length > 0 && (
            <section className="panel">
              <h2>Saved logins</h2>
              <ul className="plain">
                {account.saved_ids.map((id) => (
                  <li key={id} className="row between">
                    <b>{id}</b>
                    <span className="row">
                      <button className="primary small" disabled={busy}
                        onClick={() => run(() => api("/api/account/connect-saved", { method: "POST", body: { client_id: id } }), `Connected ${id}`)}>Connect</button>
                      <button className="ghost small" disabled={busy}
                        onClick={() => run(() => api("/api/account/forget", { method: "POST", body: { client_id: id } }), "Removed")}>Forget</button>
                    </span>
                  </li>
                ))}
              </ul>
              {account.login_error && <p className="err">Last auto login: {account.login_error}</p>}
            </section>
          )}
          <form className="panel" onSubmit={(e) => { e.preventDefault(); run(() => api("/api/account/connect", { method: "POST", body: form }), "Connected to Angel One"); }}>
            <h2>New login</h2>
            <p className="muted">From smartapi.angelone.in: your app's API key, and the TOTP secret shown when you enabled authenticator login.</p>
            <label className="field"><span>API key</span><input value={form.api_key} onChange={set("api_key")} autoComplete="off" /></label>
            <label className="field"><span>Client ID</span><input value={form.client_id} onChange={set("client_id")} autoComplete="off" /></label>
            <label className="field"><span>MPIN</span><input type="password" value={form.mpin} onChange={set("mpin")} autoComplete="off" /></label>
            <label className="field"><span>TOTP secret</span><input type="password" value={form.totp_secret} onChange={set("totp_secret")} autoComplete="off" /></label>
            <label className="check"><input type="checkbox" checked={form.save} onChange={set("save")} /> Save on the server (encrypted) so it reconnects by itself every day</label>
            <button className="primary" disabled={busy || !form.api_key || !form.client_id || !form.mpin || !form.totp_secret}>
              {busy ? "Connecting..." : "Connect"}
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
