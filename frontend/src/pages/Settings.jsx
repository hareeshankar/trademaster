import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";

export default function Settings() {
  const { flash } = useApp();
  const [days, setDays] = useState("");
  const [pw, setPw] = useState({ current: "", new: "" });

  useEffect(() => { api("/api/settings/holidays").then((r) => setDays(r.days.join("\n"))).catch(() => {}); }, []);

  const saveDays = async () => {
    try {
      const r = await api("/api/settings/holidays", { method: "PUT", body: { days: days.split(/[\s,]+/).filter(Boolean) } });
      setDays(r.days.join("\n"));
      flash("Holidays saved");
    } catch (e) { flash(e.message, "err"); }
  };
  const savePw = async (e) => {
    e.preventDefault();
    try { await api("/api/auth/password", { method: "POST", body: pw }); setPw({ current: "", new: "" }); flash("Password changed"); }
    catch (ex) { flash(ex.message, "err"); }
  };

  return (
    <div className="page">
      <header className="page-head"><div><h1>Settings</h1></div></header>
      <div className="split">
        <section className="panel">
          <h2>Market holidays</h2>
          <p className="muted">NSE, BSE and NFO don't trade on these days, so no entries, and hold-days skip them.
            One date per line (YYYY-MM-DD). Add next year's list when NSE publishes it.</p>
          <textarea rows={14} value={days} onChange={(e) => setDays(e.target.value)} spellCheck={false} />
          <button className="primary" onClick={saveDays}>Save holidays</button>
        </section>
        <form className="panel" onSubmit={savePw}>
          <h2>Owner password</h2>
          <label className="field"><span>Current password</span>
            <input type="password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} autoComplete="current-password" /></label>
          <label className="field"><span>New password</span>
            <input type="password" value={pw.new} onChange={(e) => setPw({ ...pw, new: e.target.value })} autoComplete="new-password" />
            <small>At least 10 characters.</small></label>
          <button className="primary" disabled={!pw.current || pw.new.length < 10}>Change password</button>
        </form>
      </div>
    </div>
  );
}
