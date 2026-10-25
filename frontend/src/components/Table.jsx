/** Simple data table. cols: [label, key | fn(row), "num"?] */
export default function Table({ rows, cols, empty, rowKey, rowClass, maxHeight }) {
  if (!rows || rows.length === 0) return <p className="empty">{empty || "Nothing yet."}</p>;
  return (
    <div className="tbl-wrap" style={maxHeight ? { maxHeight } : undefined}>
      <table className="tbl">
        <thead>
          <tr>{cols.map(([label, , cls], i) => <th key={i} className={cls || ""}>{label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={rowKey ? rowKey(r, i) : i} className={rowClass ? rowClass(r) : ""}>
              {cols.map(([, k, cls], j) => {
                const v = typeof k === "function" ? k(r, i) : r[k];
                return <td key={j} className={cls || ""}>{v === null || v === undefined || v === "" ? "-" : v}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
