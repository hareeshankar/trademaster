import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api, token } from "./api.js";
import Login from "./pages/Login.jsx";
import Home from "./pages/Home.jsx";
import Builder from "./pages/Builder.jsx";
import Run from "./pages/Run.jsx";
import Account from "./pages/Account.jsx";
import Settings from "./pages/Settings.jsx";
import Confirm from "./components/Confirm.jsx";

const Ctx = createContext(null);
export const useApp = () => useContext(Ctx);

export default function App() {
  const [authed, setAuthed] = useState(!!token.get());
  const [account, setAccount] = useState(null);
  const [meta, setMeta] = useState(null);
  const [toast, setToast] = useState(null);
  const [killAsk, setKillAsk] = useState(false);
  const nav = useNavigate();

  const flash = useCallback((msg, kind = "ok") => {
    setToast({ msg, kind, id: Date.now() });
  }, []);

  const refreshAccount = useCallback(async () => {
    try { setAccount(await api("/api/account")); } catch { /* shown elsewhere */ }
  }, []);

  useEffect(() => {
    const out = () => setAuthed(false);
    window.addEventListener("tm-logout", out);
    return () => window.removeEventListener("tm-logout", out);
  }, []);

  useEffect(() => {
    if (!authed) return;
    refreshAccount();
    api("/api/meta").then(setMeta).catch(() => {});
    const t = setInterval(refreshAccount, 8000);
    return () => clearInterval(t);
  }, [authed, refreshAccount]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), toast.kind === "err" ? 7000 : 3500);
    return () => clearTimeout(t);
  }, [toast]);

  if (!authed) return <Login onDone={() => setAuthed(true)} />;

  const kill = async () => {
    setKillAsk(false);
    try {
      const r = await api("/api/kill", { method: "POST" });
      flash(`Kill switch: ${r.closed} position(s) being closed, all entries stopped`);
    } catch (e) { flash(e.message, "err"); }
  };

  return (
    <Ctx.Provider value={{ account, refreshAccount, meta, flash }}>
      <div className="shell">
        <aside className="rail">
          <div className="brand" onClick={() => nav("/")}>
            <svg viewBox="0 0 22 22" aria-hidden="true">
              <rect x="3" y="8" width="5" height="9" className="g" /><rect x="5" y="4" width="1" height="16" className="g" />
              <rect x="13" y="5" width="5" height="7" className="r" /><rect x="15" y="2" width="1" height="13" className="r" />
            </svg>
            <span>TradeMaster</span>
          </div>
          <nav>
            <NavLink to="/" end>Strategies</NavLink>
            <NavLink to="/new">New strategy</NavLink>
            <NavLink to="/account">Angel One</NavLink>
            <NavLink to="/settings">Settings</NavLink>
          </nav>
          <div className="rail-foot">
            <div className={`conn ${account?.connected ? "on" : "off"}`}>
              <span className="dot" />
              <div>
                <b>{account?.connected ? account.client_id : "Not connected"}</b>
                <small>
                  {account?.connected
                    ? account.feed?.connected ? `Live feed on, ${account.feed.instruments} instruments` : "Live feed reconnecting"
                    : "Connect to trade"}
                </small>
              </div>
            </div>
            <button className="kill" onClick={() => setKillAsk(true)}>Kill switch</button>
            <button className="ghost small" onClick={() => { token.clear(); setAuthed(false); }}>Log out</button>
          </div>
        </aside>
        <main className="main">
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/new" element={<Builder />} />
            <Route path="/edit/:folder/:file" element={<Builder edit />} />
            <Route path="/s/:folder/:file" element={<Run />} />
            <Route path="/account" element={<Account />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        </main>
      </div>
      {toast && <div key={toast.id} className={`toast ${toast.kind}`} role="status">{toast.msg}</div>}
      {killAsk && (
        <Confirm
          title="Close everything now?"
          danger
          okText="Close all positions"
          onCancel={() => setKillAsk(false)}
          onOk={kill}
        >
          Every strategy stops taking new trades. Paper positions close at once. Live positions get SELL LIMIT
          orders just below the live price, re-priced until they fill.
        </Confirm>
      )}
    </Ctx.Provider>
  );
}
