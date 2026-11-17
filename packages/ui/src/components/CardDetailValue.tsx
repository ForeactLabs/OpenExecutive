// A held card's detail value: a link by name, a small table of rows, or the
// words in full. Every card that shows what Approve does draws it this way.
import type { CardDetail } from "@/lib/cardDetails";

export function CardDetailValue({ detail }: { detail: Pick<CardDetail, "value" | "href" | "table" | "header"> }) {
  if (detail.href) {
    return (
      <a href={detail.href} target="_blank" rel="noopener noreferrer" className="text-accent underline underline-offset-2 break-words">
        {detail.value}
      </a>
    );
  }
  if (detail.table && detail.table.length > 0) {
    // Rows written below row 1 have no header row: all of them are data.
    const [head, ...rows] = detail.header === false ? [[], ...detail.table] : detail.table;
    return (
      <div className="mt-1 max-h-80 overflow-auto rounded-lg border border-line">
        <table className="w-full border-collapse text-left text-[13px] leading-snug">
          {head.length > 0 && (
          <thead className="sticky top-0 bg-surface">
            <tr>
              {head.map((cell, i) => (
                <th key={i} className="border-b border-line px-2 py-1.5 font-semibold text-fg align-top">
                  {cell}
                </th>
              ))}
            </tr>
          </thead>
          )}
          <tbody>
            {rows.map((row, r) => (
              <tr key={r} className="border-t border-line">
                {row.map((cell, i) => (
                  <td key={i} className="px-2 py-1.5 text-fg align-top whitespace-pre-wrap break-words">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  return <>{detail.value}</>;
}
