import { useEffect, useState } from "react";
import { api, token } from "../api.js";

export default function Login({ onDone }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [setup, setSetup] = useState(false);
  const [pw2, setPw2] = useState("");

  useEffect(() => { api("/api/health").then((h) => setSetup(!!h.needs_setup)).catch(() => {}); }, []);

  const go = async (e) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      if (setup && pw !== pw2) throw new Error("The two passwords don't match");
      const r = await api(setup ? "/api/auth/setup" : "/api/auth/login", { method: "POST", body: { password: pw } });
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
        <p className="muted">{setup ? "First visit: choose the password that protects this app." : "Pattern trading on your Angel One account."}</p>
        <label className="field">
          <span>{setup ? "New password (at least 10 characters)" : "Owner password"}</span>
          <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus autoComplete={setup ? "new-password" : "current-password"} />
        </label>
        {setup && (
          <label className="field">
            <span>Type it again</span>
            <input type="password" value={pw2} onChange={(e) => setPw2(e.target.value)} autoComplete="new-password" />
          </label>
        )}
        {err && <p className="err">{err}</p>}
        <button className="primary wide" disabled={busy || !pw || (setup && pw.length < 10)}>{busy ? "Checking..." : setup ? "Set password and enter" : "Log in"}</button>
      </form>
    </div>
  );
}
