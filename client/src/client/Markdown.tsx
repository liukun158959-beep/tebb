import { Children, isValidElement, useState, type ReactNode } from "react";
import { ReportChart } from './report-charts';
import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// 不启用 rehype-raw：模型输出里的 HTML 当文本显示，不执行。
export function openableHref(href: string | undefined): string | null {
  if (!href) return null;
  const raw = href.trim();
  if (raw.length > 2000 || /\s/.test(raw)) return null;
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  if (!parsed.hostname || parsed.username || parsed.password) return null;
  return parsed.href;
}

async function openExternal(url: string): Promise<void> {
  window.dispatchEvent(new CustomEvent("desk-open-preview", { detail: { kind: "web", url } }));
}

export function MdLink(props: { href?: string; children?: ReactNode }) {
  const [error, setError] = useState("");
  const safe = openableHref(props.href);
  if (!safe) {
    return <span className="md-link">{props.children}</span>;
  }
  return (
    <>
      <a
        href={safe}
        className="md-link"
        data-md-link=""
        onClick={(ev) => {
          ev.preventDefault();
          setError("");
          void openExternal(safe).catch((err: unknown) => {
            setError(String(err) || "打不开链接。恢复：复制地址到浏览器打开。");
          });
        }}
      >
        {props.children}
      </a>
      {error ? (
        <span data-md-link-error className="text-destructive">
          {" "}
          {error}
        </span>
      ) : null}
    </>
  );
}

const components: Components = {
  a: MdLink,
};

type StreamNode = { type: string; value?: string; children?: StreamNode[]; position?: { start: { offset?: number }; end: { offset?: number } }; tagName?: string; properties?: Record<string, unknown> };

// Reveal only new text nodes, keeping the complete Markdown tree intact while streaming.
function streamingText(source: string, from: number, revision: number) {
  return () => (tree: StreamNode) => {
    function visit(parent: StreamNode) {
      parent.children = parent.children?.flatMap(node => {
        if (node.type !== 'text') { if (node.children) visit(node); return [node]; }
        const start = node.position?.start.offset, end = node.position?.end.offset;
        if (start === undefined || end === undefined || end <= from || !node.value) return [node];
        // Escaped/entity text may differ from source: reveal the token without guessing an offset.
        const split = source.slice(start, end) === node.value ? Math.max(0, from - start) : 0;
        const prefix = node.value.slice(0, split), tail = node.value.slice(split);
        if (!tail) return [node];
        return [...(prefix ? [{ type: 'text', value: prefix }] : []), { type: 'element', tagName: 'span', properties: { className: ['tebb-stream-new', `tebb-stream-phase-${revision % 2}`] }, children: [{ type: 'text', value: tail }] }];
      });
    }
    visit(tree);
  };
}

// 引号贴着 ** 时，CommonMark 不把 ** 当成加粗开头。标记后面补一个零宽空格，让加粗包住引号里的字。
const QUOTE_AFTER_MARK = /^["“”„«»「」『』'‘’＂＇]/u;

export function loosenQuotedEmphasis(text: string): string {
  let out = "";
  let i = 0;
  let fence = false;
  while (i < text.length) {
    if (text.startsWith("```", i)) {
      fence = !fence;
      out += "```";
      i += 3;
      continue;
    }
    if (!fence && text[i] === "`") {
      const end = text.indexOf("`", i + 1);
      if (end > i) {
        out += text.slice(i, end + 1);
        i = end + 1;
        continue;
      }
    }
    if (!fence && (text[i] === "*" || text[i] === "_")) {
      const mark = text[i];
      let j = i;
      while (j < text.length && text[j] === mark) j += 1;
      const run = j - i;
      const prev = i > 0 ? text[i - 1] : "";
      const next = text.slice(j);
      const prevOk = prev !== "" && !/\s/u.test(prev) && !/\p{P}/u.test(prev);
      if (run === 2 && prevOk && QUOTE_AFTER_MARK.test(next)) {
        out += `${text.slice(i, j)}\u200B`;
        i = j;
        continue;
      }
    }
    out += text[i];
    i += 1;
  }
  return out;
}

/** 把已知的 [n] 收成锚点，代码块里的原样保留。 */
export function withCiteLinks(text: string, numbers: number[]): string {
  const known = new Set(numbers);
  let out = "";
  let i = 0;
  let fence = false;
  while (i < text.length) {
    if (text.startsWith("```", i)) {
      fence = !fence;
      out += "```";
      i += 3;
      continue;
    }
    if (!fence && text[i] === "`") {
      const end = text.indexOf("`", i + 1);
      if (end > i) {
        out += text.slice(i, end + 1);
        i = end + 1;
        continue;
      }
    }
    if (!fence && text[i] === "[") {
      const mark = /^\[(\d+)\](?!\()/.exec(text.slice(i));
      const n = mark ? Number(mark[1]) : 0;
      if (mark && known.has(n)) {
        out += `[${n}](#cite-${n})`;
        i += mark[0].length;
        continue;
      }
    }
    out += text[i];
    i += 1;
  }
  return out;
}

function CiteLink(props: { href?: string; children?: ReactNode; onCite: (n: number) => void }) {
  const mark = /^#cite-(\d+)$/.exec(props.href || "");
  if (!mark) return <MdLink href={props.href}>{props.children}</MdLink>;
  const n = Number(mark[1]);
  return (
    <button
      type="button"
      data-note-cite={n}
      className="desk-cite"
      onClick={() => props.onCite(n)}
    >
      [{n}]
    </button>
  );
}

const DOC_LINK = /\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g;

/** 用户气泡只把 [标题](http) 画成链接，其余文字保持原样，不解析加粗或 HTML。 */
export function PlainLinks(props: { text: string }) {
  const nodes: ReactNode[] = [];
  let last = 0;
  let index = 0;
  for (const match of props.text.matchAll(DOC_LINK)) {
    const start = match.index ?? 0;
    if (start > last) nodes.push(props.text.slice(last, start));
    nodes.push(
      <MdLink key={index} href={match[2]}>
        {match[1]}
      </MdLink>,
    );
    index += 1;
    last = start + match[0].length;
  }
  if (last < props.text.length) nodes.push(props.text.slice(last));
  if (nodes.length === 0) return <>{props.text}</>;
  return <>{nodes}</>;
}

export function Markdown(props: { text: string; cites?: number[]; onCite?: (n: number) => void; externalLinks?: boolean; reveal?: { from: number; revision: number } }) {
  const opened = loosenQuotedEmphasis(props.text);
  const text = props.onCite ? withCiteLinks(opened, props.cites || []) : opened;
  const view: Components = props.onCite
    ? { a: (link) => <CiteLink href={link.href} onCite={props.onCite as (n: number) => void}>{link.children}</CiteLink> }
    : props.externalLinks ? { a: ({ href, children }) => {
      const safe = openableHref(href);
      return safe ? <a href={safe} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>;
    } } : {...components};
  view.pre=({children})=>{
    const child=Children.toArray(children)[0];
    if(isValidElement<{className?:string;children?:ReactNode}>(child)&&child.props.className?.split(' ').includes('language-tebb-chart'))return <ReportChart id={String(child.props.children||'').trim()}/>;
    return <pre>{children}</pre>;
  };
  return (
    <div className="md" data-md="">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={props.reveal ? [streamingText(text, props.reveal.from, props.reveal.revision)] : []} components={view}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
