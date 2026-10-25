import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, enc, money, sign } from "../api.js";
import { useApp } from "../App.jsx";
import Confirm from "../components/Confirm.jsx";

export default function Home() {
  const { flash, account } = useApp();
  const [data, setData] = useState(null);
  const [newFolder, setNewFolder] = useState("");
  const [delAsk, setDelAsk] = useState(null);
  const nav = useNavigate();

  const load = () => api("/api/strategies").then(setData).catch((e) => flash(e.message, "err"));
  useEffect(() => { load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, []); // eslint-disable-line

  const files = (data?.folders || []).flatMap((f) => f.files);
  const tot = files.reduce((a, f) => ({ r: a.r + f.realized, u: a.u + f.unrealized, run: a.run + (f.running ? 1 : 0),
    open: a.open + f.open }), { r: 0, u: 0, run: 0, open: 0 });

  const addFolder = async (e) => {
    e.preventDefault();
    try { await api("/api/folders", { method: "POST", body: { name: newFolder } }); setNewFolder(""); load(); }
    catch (ex) { flash(ex.message, "err"); }
  };
  const delFolder = async () => {
    try { await api(`/api/folders/${enc(delAsk)}`, { method: "DELETE" }); flash("Folder deleted"); load(); }
    catch (ex) { flash(ex.message, "err"); }
    setDelAsk(null);
  };

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Strategies</h1>
          <p className="muted">
            {tot.run} running, {tot.open} open position{tot.open === 1 ? "" : "s"}
            {!account?.connected && <> - <Link to="/account">connect Angel One</Link> to start trading</>}
          </p>
        </div>
        <button className="primary" onClick={() => nav("/new")}>New strategy</button>
      </header>

      <section className="ledger">
        <div><small>Booked P&L, all strategies</small><b className={sign(tot.r)}>{money(tot.r)}</b></div>
        <div><small>Open P&L right now</small><b className={sign(tot.u)}>{money(tot.u)}</b></div>
        <div><small>Total</small><b className={sign(tot.r + tot.u)}>{money(tot.r + tot.u)}</b></div>
      </section>

      {data && data.folders.length === 0 && (
        <div className="panel empty-state">
          <h2>No strategies yet</h2>
          <p className="muted">A strategy is a list of stocks, a timeframe, the candle patterns to act on, and your target and stop-loss.</p>
          <button className="primary" onClick={() => nav("/new")}>Create your first strategy</button>
        </div>
      )}

      {(data?.folders || []).map((f) => (
        <section className="folder" key={f.folder}>
          <div className="folder-head">
            <h2>{f.folder.replace(/_/g, " ")}</h2>
            <span className="muted">{f.files.length} strateg{f.files.length === 1 ? "y" : "ies"}</span>
            <button className="ghost small push" onClick={() => setDelAsk(f.folder)}>Delete folder</button>
          </div>
          {f.files.length === 0 ? <p className="empty">Empty folder. Pick it when you create a strategy.</p> : (
            <div className="tbl-wrap">
              <table className="tbl strat">
                <thead><tr>
                  <th>Strategy</th><th>Mode</th><th>Market</th><th>Status</th>
                  <th className="num">Open</th><th className="num">Trades</th><th className="num">Win rate</th>
                  <th className="num">Booked</th><th className="num">Open P&L</th>
                </tr></thead>
                <tbody>
                  {f.files.map((s) => (
                    <tr key={s.file} className="click" onClick={() => nav(`/s/${enc(f.folder)}/${enc(s.file)}`)}>
                      <td><b>{s.file.replace(/_/g, " ")}</b></td>
                      <td><span className={`mode ${s.mode}`}>{s.mode === "live" ? "Live" : "Paper"}</span></td>
                      <td>{s.exchange} {s.tf_label}, {s.n_items} stock{s.n_items === 1 ? "" : "s"}</td>
                      <td>{s.running ? <span className="state run">{s.paused ? "Running, entries paused" : "Running"}</span>
                        : s.was_running ? <span className="state warn">Waiting to resume</span> : <span className="state">Stopped</span>}</td>
                      <td className="num">{s.open}</td>
                      <td className="num">{s.closed}</td>
                      <td className="num">{s.win_rate === null ? "-" : `${s.win_rate}%`}</td>
                      <td className={`num ${sign(s.realized)}`}>{money(s.realized)}</td>
                      <td className={`num ${sign(s.unrealized)}`}>{money(s.unrealized)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ))}

      <form className="row add-folder" onSubmit={addFolder}>
        <input placeholder="New folder name" value={newFolder} onChange={(e) => setNewFolder(e.target.value)} />
        <button className="ghost" disabled={!newFolder.trim()}>Add folder</button>
      </form>

      {delAsk && (
        <Confirm title={`Delete folder "${delAsk.replace(/_/g, " ")}"?`} danger okText="Delete folder"
          onCancel={() => setDelAsk(null)} onOk={delFolder}>
          Every strategy inside it and its trade history is deleted. This can't be undone.
        </Confirm>
      )}
    </div>
  );
}
