import { Fragment, useState, type ReactNode } from "react";
import { CodeBlock } from "@/components/rare/code-block";

// Safe, structured markdown parser for ISHTI answers: builds React elements with zero dangerouslySetInnerHTML.
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const rx = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\))/g;
  let last = 0;
  for (const m of text.matchAll(rx)) {
    out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("**")) {
      out.push(
        <strong key={m.index} className="font-semibold text-foreground">
          {t.slice(2, -2)}
        </strong>
      );
    } else if (t.startsWith("`")) {
      out.push(
        <code
          key={m.index}
          className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[12px] text-foreground font-medium border border-border/50"
        >
          {t.slice(1, -1)}
        </code>
      );
    } else {
      const match = t.match(/\[([^\]]+)\]\(([^)]+)\)/);
      if (match) {
        const [, label, url] = match;
        out.push(
          <a
            key={m.index}
            href={url}
            target="_blank"
            rel="noreferrer"
            className="text-accent font-medium underline underline-offset-2 hover:opacity-80 transition-opacity break-all"
          >
            {label}
          </a>
        );
      }
    }
    last = (m.index ?? 0) + t.length;
  }
  out.push(text.slice(last));
  return out;
}

// ---- tables (the digest and every other table answer) --------------------------------------------------
// GFM rules a hand-rolled parser gets wrong and the chat digest made visible: an escaped pipe (\|) inside a
// cell must not split it, every row must be padded to the header width, and Urdu/Pashto cells must lay out
// right-to-left without flipping the rest of the table. Sticky header + copy-table come from the same need:
// a 200-row digest is unusable without them.
function splitRow(line: string): string[] {
  const body = line.trim().replace(/^\|/, "").replace(/(?<!\\)\|$/, "");
  const cells: string[] = [];
  let cur = "";
  for (let i = 0; i < body.length; i++) {
    const ch = body[i];
    if (ch === "\\" && i + 1 < body.length) {
      cur += body[i + 1];
      i++;
      continue;
    }
    if (ch === "|") {
      cells.push(cur.trim());
      cur = "";
      continue;
    }
    cur += ch;
  }
  cells.push(cur.trim());
  return cells;
}

function isDividerRow(line: string): boolean {
  const cells = splitRow(line).map((c) => c.trim());
  return cells.length > 0 && cells.every((c) => c.length > 0 && /^:?-+:?$/.test(c));
}

function DigestTable({ rows, no }: { rows: string[][]; no: number }) {
  const [copied, setCopied] = useState(false);
  const width = Math.max(...rows.map((r) => r.length), 1);
  const head = rows[0] ?? [];
  const body = rows.slice(1);
  const copyTable = () => {
    const text = rows.map((r) => r.join("\t")).join("\n");
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <div className="my-3 min-w-0 max-w-full rounded-xl border border-border/80 bg-card/40 shadow-xs">
      <div className="flex items-center justify-between gap-2 rounded-t-xl border-b border-border/70 bg-muted/50 px-2.5 py-1">
        <span className="text-[10px] font-semibold tracking-wider text-muted-foreground uppercase">
          Table {no} · {body.length} row{body.length === 1 ? "" : "s"}
        </span>
        <button
          type="button"
          onClick={copyTable}
          className="rounded-md border border-border/70 px-2 py-0.5 text-[10px] font-medium text-muted-foreground transition-colors hover:text-foreground"
        >
          {copied ? "Copied" : "Copy table"}
        </button>
      </div>
      <div className="max-h-[70vh] max-w-full overflow-auto rounded-b-xl">
        <table className="w-max min-w-full border-collapse text-left text-xs">
          <thead className="bg-muted/95 sticky top-0 z-10 backdrop-blur">
            <tr>
              {head.map((c, k) => (
                <th
                  key={k}
                  dir="auto"
                  className="border-b border-border px-3.5 py-2 align-top font-semibold break-words whitespace-normal text-foreground"
                  style={{ maxWidth: "22rem" }}
                >
                  {inline(c)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {body.map((row, rIdx) => (
              <tr key={rIdx} className="align-top transition-colors hover:bg-muted/30">
                {Array.from({ length: width }).map((_, cIdx) => (
                  <td
                    key={cIdx}
                    dir="auto"
                    className="px-3.5 py-2 align-top leading-snug break-words whitespace-normal text-foreground/90"
                    style={{ maxWidth: "22rem" }}
                  >
                    {inline(row[cIdx] ?? "")}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function Markdown({ text }: { text: string }) {
  const parts = text.split(/```(\w*)\n?([\s\S]*?)(?:```|$)/g);
  const blocks: ReactNode[] = [];
  let tableNo = 0;

  for (let i = 0; i < parts.length; i += 3) {
    const prose = parts[i];
    if (prose?.trim()) {
      const lines = prose.trim().split("\n");
      let bulletList: string[] = [];
      let orderedList: string[] = [];
      let tableRows: string[][] = [];

      const flushLists = () => {
        if (bulletList.length) {
          blocks.push(
            <ul
              key={`ul-${i}-${blocks.length}`}
              className="my-2 list-disc space-y-1 pl-5 text-foreground leading-relaxed"
            >
              {bulletList.map((item, idx) => (
                <li key={idx} className="pl-1">
                  {inline(item)}
                </li>
              ))}
            </ul>
          );
          bulletList = [];
        }
        if (orderedList.length) {
          blocks.push(
            <ol
              key={`ol-${i}-${blocks.length}`}
              className="my-2 list-decimal space-y-1 pl-5 text-foreground leading-relaxed"
            >
              {orderedList.map((item, idx) => (
                <li key={idx} className="pl-1">
                  {inline(item)}
                </li>
              ))}
            </ol>
          );
          orderedList = [];
        }
      };

      const flushTable = () => {
        if (tableRows.length) {
          tableNo += 1;
          blocks.push(<DigestTable key={`tbl-${i}-${blocks.length}`} rows={tableRows} no={tableNo} />);
          tableRows = [];
        }
      };

      const flushAll = () => {
        flushLists();
        flushTable();
      };

      lines.forEach((line, j) => {
        const trimmed = line.trim();

        // 1. Table line detection (rows are split with escaped pipes honoured - see splitRow)
        if (/^\s*\|.*\|\s*$/.test(line)) {
          flushLists();
          if (!isDividerRow(line)) {
            tableRows.push(splitRow(line));
          }
          return;
        }

        // 2. Ordered list item (e.g. "1. Step")
        const orderedMatch = line.match(/^\s*(\d+)\.\s+(.*)/);
        if (orderedMatch) {
          if (bulletList.length || tableRows.length) flushAll();
          orderedList.push(orderedMatch[2]);
          return;
        }

        // 3. Bullet list item (e.g. "- item" or "* item" or "• item")
        const bulletMatch = line.match(/^\s*[-*•]\s+(.*)/);
        if (bulletMatch) {
          if (orderedList.length || tableRows.length) flushAll();
          bulletList.push(bulletMatch[1]);
          return;
        }

        // 4. Other content: flush ongoing lists/tables
        flushAll();

        // 5. Headings
        const headingMatch = line.match(/^(#{1,4})\s+(.*)/);
        if (headingMatch) {
          const level = headingMatch[1].length;
          const textContent = headingMatch[2];
          if (level === 1) {
            blocks.push(
              <h3 key={`h1-${i}-${j}`} className="mt-3.5 mb-1.5 font-bold text-foreground text-base">
                {inline(textContent)}
              </h3>
            );
          } else if (level === 2) {
            blocks.push(
              <h4 key={`h2-${i}-${j}`} className="mt-3 mb-1 font-semibold text-foreground text-sm">
                {inline(textContent)}
              </h4>
            );
          } else {
            blocks.push(
              <h5 key={`h3-${i}-${j}`} className="mt-2.5 mb-1 font-semibold text-foreground text-xs uppercase tracking-wider text-muted-foreground">
                {inline(textContent)}
              </h5>
            );
          }
          return;
        }

        // 6. Regular prose paragraph
        if (trimmed) {
          blocks.push(
            <p key={`p-${i}-${j}`} className="my-1.5 leading-relaxed text-foreground">
              {inline(line)}
            </p>
          );
        }
      });

      flushAll();
    }

    // Code block part
    if (parts[i + 2] !== undefined && parts[i + 2].trim()) {
      const lang = parts[i + 1]?.trim() || "text";
      blocks.push(
        <CodeBlock
          key={`c-${i}`}
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

  return (
    <div className="text-sm leading-relaxed break-words min-w-0 max-w-full overflow-hidden">
      {blocks.map((b, i) => (
        <Fragment key={i}>{b}</Fragment>
      ))}
    </div>
  );
}
