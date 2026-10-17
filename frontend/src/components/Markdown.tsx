// Safe Markdown renderer for notebook cells.
//
// Output is React elements only: no innerHTML, no raw HTML pass-through (tags render as
// text), no images or iframes, and links only to http(s) URLs or in-app paths, opened with
// rel="noopener noreferrer". Velociraptor template expressions ({{ ... }}) are shown
// literally and never evaluated. Supported: headings, paragraphs, lists (nested by
// indentation), fenced code, block quotes, GFM tables, rules, inline code, bold, italic, links.
import { Fragment, type ReactNode } from "react";

type Block =
  | { t: "h"; level: number; text: string }
  | { t: "p"; text: string }
  | { t: "code"; lang: string; text: string }
  | { t: "quote"; blocks: Block[] }
  | { t: "hr" }
  | { t: "list"; ordered: boolean; items: ListItem[] }
  | { t: "table"; head: string[]; rows: string[][] };

interface ListItem {
  text: string;
  children: Block[];
}

const MAX_SOURCE = 200_000;
// Quotes and lists nest by recursion; deeper levels are shown as plain text so hostile input
// cannot exhaust the stack.
const MAX_DEPTH = 16;
const LIST_RE = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;

export function parseMarkdown(source: string): Block[] {
  const text = source.length > MAX_SOURCE ? source.slice(0, MAX_SOURCE) : source;
  return parseBlocks(text.replace(/\r\n?/g, "\n").split("\n"));
}

function parseBlocks(lines: string[], depth = 0): Block[] {
  if (depth > MAX_DEPTH) return [{ t: "code", lang: "", text: lines.join("\n") }];
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    const fence = line.match(/^\s*(```+|~~~+)\s*([\w+-]*)\s*$/);
    if (fence) {
      const marker = fence[1];
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(marker)) body.push(lines[i++]);
      i++;
      blocks.push({ t: "code", lang: fence[2] ?? "", text: body.join("\n") });
      continue;
    }
    const h = line.match(/^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/);
    if (h) {
      blocks.push({ t: "h", level: h[1].length, text: h[2] });
      i++;
      continue;
    }
    if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) {
      blocks.push({ t: "hr" });
      i++;
      continue;
    }
    if (/^\s{0,3}>/.test(line)) {
      const q: string[] = [];
      while (i < lines.length && /^\s{0,3}>/.test(lines[i])) q.push(lines[i++].replace(/^\s{0,3}>\s?/, ""));
      blocks.push({ t: "quote", blocks: parseBlocks(q, depth + 1) });
      continue;
    }
    if (isTableStart(lines, i)) {
      const head = splitRow(lines[i]);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(splitRow(lines[i++]));
      blocks.push({ t: "table", head, rows });
      continue;
    }
    if (LIST_RE.test(line)) {
      const [block, next] = parseList(lines, i, depth);
      blocks.push(block);
      i = next;
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !startsBlock(lines, i)) para.push(lines[i++].trim());
    if (para.length === 0) {
      para.push(lines[i].trim());
      i++;
    }
    blocks.push({ t: "p", text: para.join(" ") });
  }
  return blocks;
}

function startsBlock(lines: string[], i: number): boolean {
  const l = lines[i];
  return /^\s*(```|~~~)/.test(l) || /^\s{0,3}#{1,6}\s/.test(l) || /^\s{0,3}>/.test(l) || LIST_RE.test(l) || isTableStart(lines, i) || /^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(l);
}

function isTableStart(lines: string[], i: number): boolean {
  return i + 1 < lines.length && lines[i].includes("|") && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1]);
}

function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

function parseList(lines: string[], start: number, depth: number): [Block, number] {
  const first = lines[start].match(LIST_RE)!;
  const indent = first[1].length;
  const ordered = /\d/.test(first[2]);
  const items: ListItem[] = [];
  let i = start;
  while (i < lines.length) {
    const m = lines[i].match(LIST_RE);
    if (m && m[1].length === indent && /\d/.test(m[2]) === ordered) {
      const item: ListItem = { text: m[3], children: [] };
      i++;
      const nested: string[] = [];
      while (i < lines.length) {
        const l = lines[i];
        if (!l.trim()) {
          if (i + 1 < lines.length && lines[i + 1].match(/^(\s*)/)![1].length > indent) {
            nested.push(l);
            i++;
            continue;
          }
          break;
        }
        const lead = l.match(/^(\s*)/)![1].length;
        if (lead > indent) {
          nested.push(l);
          i++;
          continue;
        }
        break;
      }
      if (nested.length) {
        const minIndent = Math.min(...nested.filter((l) => l.trim()).map((l) => l.match(/^(\s*)/)![1].length));
        const dedented = nested.map((l) => l.slice(Math.min(minIndent, l.length)));
        const childBlocks = parseBlocks(dedented, depth + 1);
        // Continuation text directly under the item belongs to the item's paragraph.
        if (childBlocks[0]?.t === "p" && !LIST_RE.test(dedented.find((l) => l.trim()) ?? "")) {
          item.text += " " + (childBlocks.shift() as { t: "p"; text: string }).text;
        }
        item.children = childBlocks;
      }
      items.push(item);
      continue;
    }
    break;
  }
  return [{ t: "list", ordered, items }, i];
}

// ------------------------------------------------------------------------------- inline

// In-app paths start with one slash that is not followed by another slash or a backslash
// (browsers read a backslash as a slash, which would make a scheme-relative external URL);
// no backslash is accepted anywhere.
const SAFE_HREF = /^(https?:\/\/[^\s\\]+|\/(?![\/\\])[^\s\\]*)$/i;

export function safeHref(url: string): string | null {
  const u = url.trim();
  if (!SAFE_HREF.test(u)) return null;
  return u;
}

const MAX_INLINE = 20_000;

export function renderInline(text: string, keyPrefix = "i"): ReactNode[] {
  // Very long runs are shown as plain text (bounded work on hostile input).
  if (text.length > MAX_INLINE) return [text];
  const out: ReactNode[] = [];
  let rest = text;
  let n = 0;
  const pattern = /(`+)([\s\S]*?[^`])\1(?!`)|\*\*([^*]+?)\*\*|__([^_]+?)__|\*([^*\s][^*]*?)\*|(?<![\w])_([^_\s][^_]*?)_(?![\w])|\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)|!\[([^\]]*)\]\(([^)]*)\)/;
  while (rest.length) {
    const m = rest.match(pattern);
    if (!m || m.index === undefined) {
      out.push(rest);
      break;
    }
    if (m.index > 0) out.push(rest.slice(0, m.index));
    const key = `${keyPrefix}-${n++}`;
    if (m[1]) out.push(<code key={key}>{m[2]}</code>);
    else if (m[3] !== undefined) out.push(<strong key={key}>{renderInline(m[3], key)}</strong>);
    else if (m[4] !== undefined) out.push(<strong key={key}>{renderInline(m[4], key)}</strong>);
    else if (m[5] !== undefined) out.push(<em key={key}>{renderInline(m[5], key)}</em>);
    else if (m[6] !== undefined) out.push(<em key={key}>{renderInline(m[6], key)}</em>);
    else if (m[7] !== undefined) {
      const href = safeHref(m[8]);
      out.push(
        href ? (
          <a key={key} href={href} target={href.startsWith("/") ? undefined : "_blank"} rel="noopener noreferrer">
            {renderInline(m[7], key)}
          </a>
        ) : (
          <span key={key} title="Link removed: only http(s) and in-app links are allowed">
            {m[7]} <span className="md-blocked">(link removed)</span>
          </span>
        ),
      );
    } else if (m[9] !== undefined) {
      out.push(
        <span key={key} className="md-blocked">
          [image not displayed{m[9] ? `: ${m[9]}` : ""}]
        </span>,
      );
    }
    rest = rest.slice(m.index + m[0].length);
  }
  return out;
}

// ------------------------------------------------------------------------------- render

function renderBlocks(blocks: Block[], prefix: string): ReactNode[] {
  return blocks.map((b, idx) => {
    const key = `${prefix}-${idx}`;
    switch (b.t) {
      case "h": {
        const level = Math.min(6, b.level + 1); // page owns h1; notebook headings start at h2
        const Tag = `h${level}` as "h2";
        return (
          <Tag key={key} dir="auto">
            {renderInline(b.text, key)}
          </Tag>
        );
      }
      case "p":
        return (
          <p key={key} dir="auto">
            {renderInline(b.text, key)}
          </p>
        );
      case "code":
        return (
          <pre key={key} className="code" data-lang={b.lang || undefined}>
            {b.text}
          </pre>
        );
      case "quote":
        return <blockquote key={key}>{renderBlocks(b.blocks, key)}</blockquote>;
      case "hr":
        return <hr key={key} />;
      case "list": {
        const Tag = b.ordered ? "ol" : "ul";
        return (
          <Tag key={key}>
            {b.items.map((it, j) => (
              <li key={`${key}-${j}`} dir="auto">
                {renderInline(it.text, `${key}-${j}`)}
                {it.children.length > 0 && <Fragment>{renderBlocks(it.children, `${key}-${j}-c`)}</Fragment>}
              </li>
            ))}
          </Tag>
        );
      }
      case "table":
        return (
          <div key={key} className="table-wrap" style={{ marginBottom: 8 }}>
            <table>
              <thead>
                <tr>
                  {b.head.map((h, j) => (
                    <th key={j} dir="auto">
                      {renderInline(h, `${key}-h${j}`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {b.rows.map((r, j) => (
                  <tr key={j}>
                    {b.head.map((_, c) => (
                      <td key={c} dir="auto">
                        {renderInline(r[c] ?? "", `${key}-${j}-${c}`)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
    }
  });
}

export function Markdown({ source }: { source: string }) {
  const truncated = source.length > MAX_SOURCE;
  return (
    <div className="markdown">
      {renderBlocks(parseMarkdown(source), "md")}
      {truncated && <p className="md-blocked">Markdown longer than {MAX_SOURCE.toLocaleString()} characters was truncated for display.</p>}
    </div>
  );
}
