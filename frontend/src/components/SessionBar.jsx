/** The trading day as a strip: session hours, candle closes, square-off line, now, and today's trades on it. */
const SESS = { MCX: [9 * 60, 23 * 60 + 30, 23 * 60 + 15] };
const DEF = [9 * 60 + 15, 15 * 60 + 30, 15 * 60 + 15];

const mins = (s) => {
  const m = /(\d{2}):(\d{2})/.exec(String(s).slice(11, 16));
  return m ? +m[1] * 60 + +m[2] : null;
};
const hm = (m) => `${Math.floor(m / 60) % 24}:${String(m % 60).padStart(2, "0")}`;

export default function SessionBar({ exchange, tfMin, now, closed, open }) {
  const [a, b, sq] = SESS[exchange] || DEF;
  const span = b - a;
  const today = String(now).slice(0, 10);
  const n = mins(now);
  const pos = (m) => `${Math.min(100, Math.max(0, ((m - a) / span) * 100))}%`;
  const ticks = [];
  if (tfMin && tfMin <= 60 && span / tfMin <= 160) for (let m = a + tfMin; m < b; m += tfMin) ticks.push(m);
  const hours = [];
  for (let h = Math.ceil(a / 60) * 60; h <= b; h += exchange === "MCX" ? 120 : 60) hours.push(h);
  const marks = [
    ...closed.filter((t) => String(t.exit_time).startsWith(today)).map((t) => ({ m: mins(t.exit_time), win: t.pnl > 0, k: `c${t.symbol}${t.exit_time}`, label: `${t.symbol} ${t.reason} ${t.pnl}` })),
    ...open.filter((p) => String(p.entry_time).startsWith(today)).map((p) => ({ m: mins(p.entry_time), openPos: true, k: `o${p.symbol}`, label: `${p.symbol} bought ${p.entry_time.slice(11, 16)}` })),
  ].filter((x) => x.m !== null);
  const inSession = n !== null && n >= a && n <= b;

  return (
    <div className="session" aria-label={`Session ${hm(a)} to ${hm(b)}`}>
      <div className="track">
        <div className="done" style={{ width: inSession ? pos(n) : n > b ? "100%" : "0%" }} />
        {ticks.map((m) => <span key={m} className="tick" style={{ left: pos(m) }} />)}
        <span className="sq" style={{ left: pos(sq) }} title={`Square-off ${hm(sq)}`} />
        {marks.map((x) => (
          <span key={x.k} className={`mark ${x.openPos ? "open" : x.win ? "win" : "loss"}`} style={{ left: pos(x.m) }} title={x.label} />
        ))}
        {inSession && <span className="now" style={{ left: pos(n) }}><b>{hm(n)}</b></span>}
      </div>
      <div className="hours">
        {hours.map((h) => <span key={h} style={{ left: pos(h) }}>{hm(h)}</span>)}
      </div>
    </div>
  );
}
