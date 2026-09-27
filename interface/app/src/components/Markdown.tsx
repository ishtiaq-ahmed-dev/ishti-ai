import { Fragment, type ReactNode } from "react";
import { CodeBlock } from "@/components/rare/code-block";

// Small, safe markdown for ISHTI's short answers: builds React elements, never injects HTML.
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const rx = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\))/g;
  let last = 0;
  for (const m of text.matchAll(rx)) {
    out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("**")) out.push(<b key={m.index}>{t.slice(2, -2)}</b>);
    else if (t.startsWith("`")) out.push(<code key={m.index} className="rounded bg-muted px-1 py-0.5 font-mono text-[12px]">{t.slice(1, -1)}</code>);
    else {
      const [, label, url] = t.match(/\[([^\]]+)\]\(([^)]+)\)/)!;
      out.push(<a key={m.index} href={url} target="_blank" rel="noreferrer" className="text-accent underline-offset-2 hover:underline break-all">{label}</a>);
    }
    last = (m.index ?? 0) + t.length;
  }
  out.push(text.slice(last));
  return out;
}

export function Markdown({ text }: { text: string }) {
  const parts = text.split(/```(\w*)\n?([\s\S]*?)(?:```|$)/g);
  const blocks: ReactNode[] = [];
  for (let i = 0; i < parts.length; i += 3) {
    const prose = parts[i];
    if (prose?.trim()) {
      const lines = prose.trim().split("\n");
      let list: string[] = [];
      let rows: string[][] = [];
      const flush = () => {
        if (list.length) blocks.push(<ul key={`l${i}-${blocks.length}`} className="my-1 list-disc space-y-0.5 pl-5">{list.map((l, j) => <li key={j}>{inline(l)}</li>)}</ul>);
        list = [];
        if (rows.length) {
          const [head, ...body] = rows;
          blocks.push(
            <div key={`t${i}-${blocks.length}`} className="my-2 overflow-x-auto rounded-xl border border-border">
              <table className="w-full text-left text-[13px]">
                <thead className="bg-muted/60 text-muted-foreground"><tr>{head.map((c, k) => <th key={k} className="px-3 py-1.5 font-medium">{inline(c)}</th>)}</tr></thead>
                <tbody>{body.map((r, n) => <tr key={n} className={`border-t border-border ${n === 0 ? "bg-accent-soft/40" : ""}`}>{r.map((c, k) => <td key={k} className="px-3 py-1.5 tabular-nums">{inline(c)}</td>)}</tr>)}</tbody>
              </table>
            </div>);
        }
        rows = [];
      };
      lines.forEach((line, j) => {
        if (/^\s*\|.*\|\s*$/.test(line)) {
          if (list.length) flush();
          if (!/^\s*\|[\s:|-]+\|\s*$/.test(line)) rows.push(line.trim().slice(1, -1).split("|").map((c) => c.trim()));
          return;
        }
        const bullet = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)/);
        if (bullet) { if (rows.length) flush(); list.push(bullet[1]); return; }
        flush();
        const heading = line.match(/^#{1,4}\s+(.*)/);
        if (heading) blocks.push(<p key={`h${i}-${j}`} className="mt-2 font-semibold">{inline(heading[1])}</p>);
        else if (line.trim()) blocks.push(<p key={`p${i}-${j}`} className="my-1">{inline(line)}</p>);
      });
      flush();
    }
    if (parts[i + 2] !== undefined && parts[i + 2].trim()) {
      const lang = parts[i + 1]?.trim() || "text";
      blocks.push(
        <CodeBlock
          key={`c${i}`}
          code={parts[i + 2].trimEnd()}
          language={lang}
          filename={lang.toUpperCase()}
          showHeader={true}
          showCopyButton={true}
          accent="#6e56cf"
          className="my-3 rounded-2xl overflow-hidden border border-border shadow-xs"
        />
      );
    }
  }
  return <div className="text-sm leading-relaxed break-words min-w-0 max-w-full overflow-hidden">{blocks.map((b, i) => <Fragment key={i}>{b}</Fragment>)}</div>;
}
