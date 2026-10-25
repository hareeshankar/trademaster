import { useState } from "react";
import { api, token } from "../api.js";

export default function Login({ onDone }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const go = async (e) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r = await api("/api/auth/login", { method: "POST", body: { password: pw } });
      token.set(r.token);
      onDone();
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form onSubmit={go} className="login-card">
        <svg viewBox="0 0 22 22" className="login-mark" aria-hidden="true">
          <rect x="3" y="8" width="5" height="9" className="g" /><rect x="5" y="4" width="1" height="16" className="g" />
          <rect x="13" y="5" width="5" height="7" className="r" /><rect x="15" y="2" width="1" height="13" className="r" />
        </svg>
        <h1>TradeMaster</h1>
        <p className="muted">Pattern trading on your Angel One account.</p>
        <label className="field">
          <span>Owner password</span>
          <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus autoComplete="current-password" />
        </label>
        {err && <p className="err">{err}</p>}
        <button className="primary wide" disabled={busy || !pw}>{busy ? "Checking..." : "Log in"}</button>
      </form>
    </div>
  );
}
