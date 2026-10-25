import { useEffect, useRef, useState } from "react";

/** Modal confirm. `typed` = a word the person must type (used for real-money actions). */
export default function Confirm({ title, children, okText = "Confirm", onOk, onCancel, danger, typed }) {
  const [val, setVal] = useState("");
  const ref = useRef(null);
  useEffect(() => {
    ref.current?.focus();
    const k = (e) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onCancel]);
  const ready = !typed || val.trim().toUpperCase() === typed;
  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label={title}>
        <h2>{title}</h2>
        <div className="dialog-body">{children}</div>
        {typed && (
          <label className="field">
            <span>Type {typed} to continue</span>
            <input ref={ref} value={val} onChange={(e) => setVal(e.target.value)} autoComplete="off" />
          </label>
        )}
        <div className="row end">
          <button className="ghost" onClick={onCancel} ref={typed ? null : ref}>Cancel</button>
          <button className={danger ? "danger" : "primary"} disabled={!ready} onClick={() => onOk(val.trim().toUpperCase())}>
            {okText}
          </button>
        </div>
      </div>
    </div>
  );
}
