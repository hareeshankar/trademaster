const BASE = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");

export const token = {
  get: () => { try { return localStorage.getItem("tm_token") || ""; } catch { return ""; } },
  set: (t) => { try { localStorage.setItem("tm_token", t); } catch { /* private mode */ } },
  clear: () => { try { localStorage.removeItem("tm_token"); } catch { /* ignore */ } },
};

export class ApiError extends Error {
  constructor(msg, status) { super(msg); this.status = status; }
}

export async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(BASE + path, {
      method,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token.get()}` },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Can't reach the trading server. Check it is running.", 0);
  }
  if (res.status === 401 && path !== "/api/auth/login") {
    token.clear();
    window.dispatchEvent(new Event("tm-logout"));
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new ApiError(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : `Error ${res.status}`, res.status);
  }
  return data;
}

export async function download(path, name) {
  const res = await fetch(BASE + path, { headers: { Authorization: `Bearer ${token.get()}` } });
  if (!res.ok) throw new ApiError("Download failed", res.status);
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

export const enc = (s) => encodeURIComponent(s);

export const money = (x, d = 0) =>
  x === null || x === undefined || Number.isNaN(Number(x)) ? "-" :
  Number(x).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });

export const px = (x) => money(x, 2);
export const sign = (x) => (Number(x) > 0 ? "up" : Number(x) < 0 ? "down" : "");
