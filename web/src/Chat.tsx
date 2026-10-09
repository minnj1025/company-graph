import { Children, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { askAgentStream, fetchAskStatus, fetchSuggest, searchCompanies, type AskEvent } from "./api";
import { shortName } from "./Graph";
import type { AskResult, AskStatus, Company, GraphNode, Suggestion } from "./types";

const DART = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=";
/** 처음 화면의 입력 칸 위에 보이는 예시. 이 DB로 답할 수 있는 질문의 종류가 하나씩 다르다 */
const EXAMPLES = [
  "삼양식품은 뭘 팔아서 돈을 벌어?",
  "전기차 배터리 분리막을 만드는 회사와 매출 비중은?",
  "삼성전자의 최대주주는 누구야?",
  "현대모비스에 올해 공급계약을 공시한 회사는?",
];
/** 입력 칸에 번갈아 보이는 글 */
const HINTS = [
  "기업이나 제품 이름, 또는 궁금한 것을 적어 보세요",
  "농심은 뭘 팔아서 돈을 벌어?",
  "게임을 만드는 회사는 어디야?",
  "카카오가 지분을 가진 상장사는?",
  "반도체 장비를 만드는 회사를 알려줘",
  "한화오션이 올해 공시한 공급계약을 금액이 큰 순서로",
];

/** 답에 나온 14자리 접수번호를 DART 원문 링크로 바꾼다. */
const linkReceipts = (text: string) => text.replace(/(?<![\d/=])(20\d{12})(?!\d)/g, `[$1](${DART}$1)`);
/** 답이 흘러나오는 동안에는 끝에 붙는 "이어서 물을 질문"이 글로 보이지 않게 뗀다 (다 오면 버튼으로 나온다) */
const withoutFollowups = (text: string) => text.split("<<")[0];

const escapeRegExp = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
/** 답에 나온 기업 이름을 그래프의 그 점으로 가는 링크로 바꾼다. 이미 링크인 곳은 건드리지 않는다 */
function linkCompanies(text: string, companies: { id: number; name: string }[]): string {
  const ids = new Map<string, number>();
  for (const company of companies) if (company.name.length >= 2 && !ids.has(company.name)) ids.set(company.name, company.id);
  if (ids.size === 0) return text;
  // 긴 이름부터 맞춰야 "SK"가 "SK하이닉스" 안에서 먼저 잡히지 않는다. 영문·숫자로 이어지는 낱말의 일부는 건너뛴다
  const names = [...ids.keys()].sort((a, b) => b.length - a.length).map(escapeRegExp).join("|");
  const pattern = new RegExp(`(\\[[^\\]]*\\]\\([^)]*\\)|https?:\\S+)|(?<![A-Za-z0-9])(${names})(?![A-Za-z0-9])`, "g");
  return text.replace(pattern, (all, link, name) => (link ? all : `[${name}](#company-${ids.get(name)})`));
}

const plain = (children: ReactNode): string =>
  Children.toArray(children)
    .map((child) => (typeof child === "string" || typeof child === "number" ? String(child) : ""))
    .join("");

/** 표의 칸. "45.2%"처럼 비율만 적힌 칸은 숫자 뒤에 그 크기만큼의 막대를 깐다 */
function Cell({ children }: { children?: ReactNode }) {
  const match = /^\s*(\d{1,3}(?:\.\d+)?)\s*%\s*$/.exec(plain(children));
  const value = match ? Number(match[1]) : null;
  if (value === null || value > 100) return <td>{children}</td>;
  return (
    <td className="pct">
      <i style={{ width: `${Math.max(value, 1.5)}%` }} />
      <span>{children}</span>
    </td>
  );
}

const squeeze = (text: string) => text.replace(/\s/g, "").toLowerCase();

/** steps 와 partial 은 답이 오는 동안에만 쓴다: Agent가 지금까지 한 조회와, 흘러나오고 있는 답의 글 */
type Turn = { question: string; result?: AskResult; error?: string; steps: string[]; partial: string };

const TOOL_LABELS: Record<string, string> = {
  find_company: "기업 찾기",
  get_relations: "관계 조회",
  get_filings: "공시 목록 조회",
  list_companies: "기업 목록 조회",
  find_disclosers: "공시를 낸 기업 찾기",
  find_paths: "두 기업을 잇는 경로 찾기",
  search_business: "사업 내용의 글에서 찾기",
  get_business: "사업 내용 읽기",
  find_by_product: "제품 표에서 찾기",
  get_products: "제품과 매출 비중 읽기",
  get_coverage: "수집 범위 확인",
};

/** "제품 표에서 찾기: 분리막, LiBS" 처럼, Agent가 지금 무엇을 조회하는지 한 줄로 */
function describe(event: Extract<AskEvent, { kind: "tool" }>): string {
  const { keywords, name } = event.input as { keywords?: unknown; name?: unknown };
  const what = Array.isArray(keywords) ? keywords.join(", ") : typeof name === "string" ? name : "";
  return `${TOOL_LABELS[event.name] ?? event.name}${what ? `: ${what}` : ""}`;
}

const CHATS_KEY = "chats";
const OLD_KEY = "chat-turns";   // 대화로 묶기 전에 쓰던 자리
const CHATS_MAX = 50;
const TURNS_MAX = 20;
/** 이어지는 질문에 같이 보내는 앞선 질문의 수 */
const CONTEXT_TURNS = 4;

/** 대화 하나: 이어지는 질문들의 묶음. 같은 대화 안에서는 앞선 질문과 답을 Agent가 안다 */
export interface Conversation {
  id: string;
  turns: Turn[];
}

const newId = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

/** 대화들을 이 브라우저에 적어 둔다. 새로 고치거나 다시 들어와도 남는다 */
function saveChats(chats: Conversation[], current: string) {
  try {
    // 그래프 라이브러리가 점과 선에 그리기용 값을 붙여 두므로, 서버에서 받은 값만 골라 적는다
    const plainNode = ({ id, name, legal_name, stock_code, listed, group, stage, sector, market, in_scope, kind, degree, focus, mentioned }: GraphNode) =>
      ({ id, name, legal_name, stock_code, listed, group, stage, sector, market, in_scope, kind, degree, focus, mentioned });
    const end = (side: number | GraphNode) => (typeof side === "number" ? side : side.id);
    const plainTurns = (turns: Turn[]) =>
      turns
        .filter((turn) => turn.result || turn.error)
        .slice(-TURNS_MAX)
        .map((turn) => ({
          question: turn.question,
          error: turn.error,
          result: turn.result && {
            ...turn.result,
            graph: {
              nodes: turn.result.graph.nodes.map(plainNode),
              links: turn.result.graph.links.map(({ source, target, type, count, value, label }) => ({ source: end(source), target: end(target), type, count, value, label })),
            },
          },
        }));
    const kept = chats.map((chat) => ({ id: chat.id, turns: plainTurns(chat.turns) })).filter((chat) => chat.turns.length > 0).slice(-CHATS_MAX);
    try {
      localStorage.setItem(CHATS_KEY, JSON.stringify({ current, chats: kept }));
    } catch {
      // 자리가 모자라면 대화는 지우지 않고, 오래된 대화의 그래프만 덜어 낸다 (질문과 답의 글은 남는다)
      const light = kept.map((chat, i) =>
        i >= kept.length - 3 ? chat : { ...chat, turns: chat.turns.map((turn) => (turn.result ? { ...turn, result: { ...turn.result, graph: { nodes: [], links: [] } } } : turn)) },
      );
      localStorage.setItem(CHATS_KEY, JSON.stringify({ current, chats: light }));
    }
  } catch {
    // 저장이 막혀 있으면 기록 없이 쓴다
  }
}

type Saved = Pick<Turn, "question" | "result" | "error">[];
const revive = (turns: Saved): Turn[] => turns.map((turn) => ({ ...turn, steps: [], partial: "" }));

function loadChats(): { chats: Conversation[]; current: string } {
  try {
    const saved = JSON.parse(localStorage.getItem(CHATS_KEY) ?? "null") as { current: string; chats: { id: string; turns: Saved }[] } | null;
    const old = JSON.parse(localStorage.getItem(OLD_KEY) ?? "[]") as Saved;
    localStorage.removeItem(OLD_KEY);
    const chats = (saved?.chats ?? []).map((chat) => ({ id: chat.id, turns: revive(chat.turns) }));
    if (old.length > 0) chats.push({ id: newId(), turns: revive(old) });
    if (chats.length > 0) return { chats, current: chats.some((chat) => chat.id === saved?.current) ? saved!.current : chats[chats.length - 1].id };
  } catch {
    // 아래에서 빈 대화로 시작한다
  }
  const first = { id: newId(), turns: [] };
  return { chats: [first], current: first.id };
}

/** 질문과 답의 기록. 입력 칸(그래프 아래)과 답이 보이는 칸(오른쪽)이 떨어져 있어서 화면 맨 위에서 쥐고 내려 준다 */
export function useChat(onShow: (result: AskResult | null) => void) {
  const [{ chats, current }, setBook] = useState(loadChats);
  const [busy, setBusy] = useState(false);
  const turns = chats.find((chat) => chat.id === current)?.turns ?? [];
  /** 지금 대화의 질문들을 바꾼다 */
  const setTurns = useCallback(
    (change: (all: Turn[]) => Turn[]) =>
      setBook((book) => ({ ...book, chats: book.chats.map((chat) => (chat.id === book.current ? { ...chat, turns: change(chat.turns) } : chat)) })),
    [],
  );
  useEffect(() => {
    if (!busy) saveChats(chats, current);
  }, [chats, current, busy]);
  /** 새 대화를 연다. 앞 대화는 지난 대화로 남는다 */
  const startNew = useCallback(
    () =>
      setBook((book) => {
        const kept = book.chats.filter((chat) => chat.turns.length > 0);
        const fresh = { id: newId(), turns: [] };
        return { chats: [...kept, fresh], current: fresh.id };
      }),
    [],
  );
  const open = useCallback((id: string) => setBook((book) => ({ chats: book.chats.filter((chat) => chat.turns.length > 0 || chat.id === id), current: id })), []);
  /** 지금 대화를 지운다 */
  const clear = useCallback(
    () =>
      setBook((book) => {
        const fresh = { id: newId(), turns: [] };
        return { chats: [...book.chats.filter((chat) => chat.id !== book.current), fresh], current: fresh.id };
      }),
    [],
  );
  const [status, setStatus] = useState<AskStatus | null>(null);

  useEffect(() => {
    fetchAskStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  const owner = status?.owner === true;
  const left = status && !owner ? Math.min(status.left_for_you ?? 0, status.left_today ?? 0) : null;
  const closed = status !== null && (!status.enabled || left === 0);

  const ask = useCallback(
    async (question: string) => {
      const clean = question.trim();
      if (clean.length < 2 || busy || closed) return false;
      setBusy(true);
      // 같은 대화의 앞선 질문과 답(글만)을 같이 보낸다
      const context = turns
        .filter((turn) => turn.result)
        .slice(-CONTEXT_TURNS)
        .map((turn) => ({ question: turn.question, answer: turn.result!.answer.slice(0, 6000) }));
      setTurns((all) => [...all, { question: clean, steps: [], partial: "" }]);
      const patch = (change: (turn: Turn) => Turn) => setTurns((all) => all.map((t, i) => (i === all.length - 1 ? change(t) : t)));
      try {
        const result = await askAgentStream(
          clean,
          (event) => {
            if (event.kind === "text") patch((t) => ({ ...t, partial: t.partial + event.text }));
            // 도구를 부르기 전에 쓴 말은 답이 아니라서, 조회가 시작되면 지운다
            else if (event.kind === "tool") patch((t) => ({ ...t, partial: "", steps: [...t.steps, describe(event)] }));
            else patch((t) => ({ ...t, partial: "" }));
          },
          context,
        );
        setTurns((all) => all.map((t, i) => (i === all.length - 1 ? { ...t, result } : t)));
        setStatus((s) => (s && !s.owner ? { ...s, left_for_you: result.left_for_you, left_today: (s.left_today ?? 1) - 1 } : s));
        if (result.graph.nodes.length > 0) onShow(result);
      } catch (error) {
        setTurns((all) => all.map((t, i) => (i === all.length - 1 ? { ...t, error: (error as Error).message } : t)));
      } finally {
        setBusy(false);
      }
      return true;
    },
    [busy, closed, onShow, turns, setTurns],
  );

  /** 지금 대화 말고 남아 있는 대화들. 최근 것이 위로 (지금 대화의 질문은 아래에 이미 죽 보인다) */
  const others = chats.filter((chat) => chat.id !== current && chat.turns.length > 0).reverse();
  return { turns, busy, status, left, closed, ask, owner, clear, startNew, open, others, current };
}

export type ChatState = ReturnType<typeof useChat>;

/** 답을 기다리는 동안: 지금까지 한 조회와 지난 시간, 그리고 글이 오기 시작하면 흘러나오는 답 */
function Waiting({ turn }: { turn: Turn }) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setSeconds((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <div className="a pending">
      <ul className="steps-done">
        {turn.steps.map((step, i) => (
          <li key={i} className={i === turn.steps.length - 1 && !turn.partial ? "now" : ""}>
            {step}
          </li>
        ))}
      </ul>
      {turn.partial ? (
        <div className="answer streaming">
          <Markdown remarkPlugins={[remarkGfm]} components={{ td: Cell }}>
            {linkReceipts(withoutFollowups(turn.partial))}
          </Markdown>
        </div>
      ) : (
        <p className="waiting">
          {turn.steps.length === 0 ? "질문을 읽는 중입니다…" : "조회한 것을 읽고 있습니다…"} {seconds}초
        </p>
      )}
    </div>
  );
}

interface LogProps {
  chat: ChatState;
  /** 지금 그래프에 띄운 답. 다른 답을 누르면 그 답의 그래프로 바뀐다 */
  shown: AskResult | null;
  onShow: (result: AskResult | null) => void;
  /** 답에 나온 기업 이름을 눌렀다. 그 답의 그래프에서 그 기업을 고른다 */
  onCompany: (result: AskResult, id: number) => void;
}

/** 답의 글. 접수번호는 원문으로, 기업 이름은 그래프의 점으로 이어진다 */
function Answer({ result, onCompany }: { result: AskResult; onCompany: (id: number) => void }) {
  const text = useMemo(() => {
    const mentioned = result.graph.nodes.filter((node) => !node.kind && node.mentioned);
    return linkReceipts(linkCompanies(result.answer, mentioned));
  }, [result]);
  return (
    <div className="answer">
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          td: Cell,
          a: ({ href, children }) =>
            href?.startsWith("#company-") ? (
              <button className="company-link" title="그래프에서 이 기업 보기" onClick={() => onCompany(Number(href.slice(9)))}>
                {children}
              </button>
            ) : (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            ),
        }}
      >
        {text}
      </Markdown>
    </div>
  );
}

/** 오른쪽 칸에 쌓이는 질문과 답 */
export function ChatLog({ chat, shown, onShow, onCompany }: LogProps) {
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // 새 브라우저에서는 scrollIntoView 가 Promise 를 돌려주므로, 그 값을 effect 의 반환값으로 내보내지 않는다
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [chat.turns, chat.busy]);
  // 펼쳐 둔 질문 하나. 나머지는 질문만 한 줄씩 쌓인다. 새로 물으면 그 질문이 펼쳐진다
  // 대화를 열었을 때는 질문만 죽 보이고, 새로 물은 질문만 펼쳐진다
  const [open, setOpen] = useState(-1);
  const seen = useRef({ id: chat.current, count: chat.turns.length });
  useEffect(() => {
    const before = seen.current;
    seen.current = { id: chat.current, count: chat.turns.length };
    if (before.id !== chat.current) setOpen(-1);
    else if (chat.turns.length > before.count) setOpen(chat.turns.length - 1);
  }, [chat.current, chat.turns.length]);

  return (
    <div className="chat-log">
      {
        <div className="chat-bar">
          <button onClick={chat.startNew} disabled={chat.busy || chat.turns.length === 0} title="앞 내용을 잇지 않는 새 대화를 엽니다. 지금 대화는 지난 대화로 남습니다">
            + 새 대화
          </button>
          <details className="past">
            <summary>지난 대화 {chat.others.length}</summary>
            <div className="past-list">
              {chat.others.length === 0 && <p>이 대화 말고 남아 있는 대화가 없습니다.</p>}
              {chat.others.map((other) => (
                <button
                  key={other.id}
                  disabled={chat.busy}
                  onClick={(event) => {
                    (event.currentTarget.closest("details") as HTMLDetailsElement).open = false;
                    chat.open(other.id);
                  }}
                >
                  <span>{other.turns[0].question}</span>
                  <em>질문 {other.turns.length}개</em>
                </button>
              ))}
            </div>
          </details>
        </div>
      }
      {chat.turns.length === 0 && (
        <p className="empty">
          아래 입력 칸에 궁금한 것을 적으면 답이 여기에 쌓입니다. 같은 대화 안에서는 "그중 가장 큰 곳은?"처럼 이어서 물을 수 있습니다.
        </p>
      )}
      {chat.turns.map((turn, i) => (
        <div key={i} className={i === open ? "turn" : "turn folded"}>
          <button
            className={turn.result !== undefined && turn.result === shown ? "q shown" : "q"}
            onClick={() => {
              // 펼친 질문을 다시 누르면 접힌다
              if (i === open) return setOpen(-1);
              setOpen(i);
              if (turn.result && turn.result.graph.nodes.length > 0) onShow(turn.result);
            }}
            aria-expanded={i === open}
            title={i === open ? "다시 누르면 답을 접습니다" : "답을 펼치고, 이 질문으로 찾은 그래프를 봅니다"}
          >
            {turn.question}
          </button>
          {turn.error && <div className="a error">{turn.error}</div>}
          {turn.result && (
            <div className={turn.result === shown ? "a shown" : "a"}>
              <Tools result={turn.result} />
              <Answer result={turn.result} onCompany={(id) => onCompany(turn.result!, id)} />
              {turn.result.sources.length === 0 ? (
                <div className="sources">
                  <span>이 답은 근거로 든 공시가 없습니다 (조회 결과가 없거나, 답하지 않는 질문입니다)</span>
                </div>
              ) : (
                <details className="sources">
                  <summary>출처 {turn.result.sources.length}건</summary>
                  {turn.result.sources.map((source) => (
                    <a key={source.rcept_no} href={source.url} target="_blank" rel="noreferrer" title="DART 공시 원문">
                      <em>{source.filed ?? source.rcept_no}</em>
                      {source.company ? `${source.company} · ${source.report}` : `접수번호 ${source.rcept_no}`}
                    </a>
                  ))}
                </details>
              )}
              <div className="a-foot">
                <span className="facts">
                  <span>{turn.result.seconds}초</span>
                  {" · "}
                  <span>{turn.result.tokens.toLocaleString()}토큰</span>
                  {" · "}
                  <span>조회 시점 {turn.result.as_of}</span>
                  {turn.result.graph.nodes.length > 0 && (
                    <>
                      {" · "}
                      <span>그래프에 기업 {turn.result.graph.nodes.filter((node) => !node.kind).length}곳 (답은 그 일부)</span>
                    </>
                  )}
                </span>
                {turn.result.graph.nodes.length > 0 && turn.result !== shown && <button onClick={() => onShow(turn.result!)}>그래프에 보기</button>}
              </div>
              {turn.result.unverified_citations.length > 0 && (
                <div className="warn">조회 결과에 없는 접수번호가 답에 있습니다: {turn.result.unverified_citations.join(", ")}</div>
              )}
              {i === chat.turns.length - 1 && !chat.closed && (turn.result.followups ?? []).length > 0 && (
                <div className="followups">
                  <b>이어서 물어보기</b>
                  {turn.result.followups!.map((q) => (
                    <button key={q} onClick={() => void chat.ask(q)} disabled={chat.busy} title="Agent에게 묻습니다 (하루 횟수를 한 번 씁니다)">
                      {q}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {!turn.result && !turn.error && <Waiting turn={turn} />}
        </div>
      ))}
      {chat.turns.length > 0 && !chat.busy && (
        <button
          className="clear-history"
          onClick={() => {
            onShow(null);
            chat.clear();
          }}
          title="이 대화를 이 브라우저에서 지웁니다. 다른 대화는 남습니다"
        >
          이 대화 지우기
        </button>
      )}
      <div ref={bottom} />
    </div>
  );
}

interface ComposerProps {
  chat: ChatState;
  /** 기업을 골랐다. Agent를 부르지 않고 그 기업 중심의 그래프와 상세를 연다 */
  onPick: (company: Company) => void;
  /** 질문을 보냈다. 답이 보이는 칸을 연다 */
  onAsk: () => void;
  /** 예시 질문을 보일지 (처음 화면에서만) */
  examples: boolean;
  /** 제품, 제품군, 공식 분류를 골랐다. Agent를 부르지 않고 그것을 파는 기업을 그린다 */
  onPickProduct: (item: Suggestion) => void;
  /** 입력 칸에 들어왔다. 지난 답이 있으면 답이 보이는 칸을 다시 연다 */
  onFocus: () => void;
}

const KIND_LABELS: Record<Suggestion["kind"], string> = { product: "제품", family: "제품군", class: "공식 분류" };

/** 그래프 아래의 입력 칸 하나. 기업 이름을 적으면 DB에서 바로 찾고, 문장을 적으면 Agent에게 묻는다 */
export function Composer({ chat, onPick, onAsk, examples, onPickProduct, onFocus }: ComposerProps) {
  const [text, setText] = useState("");
  const [results, setResults] = useState<Company[]>([]);
  const [things, setThings] = useState<Suggestion[]>([]);
  const [hint, setHint] = useState(0);
  const typed = text.trim();

  useEffect(() => {
    const timer = setInterval(() => setHint((n) => (n + 1) % HINTS.length), 3200);
    return () => clearInterval(timer);
  }, []);

  // 적는 동안 기업을 찾아 둔다. 문장처럼 길면 기업 이름이 아니다
  useEffect(() => {
    if (typed.length < 1 || typed.length > 20) {
      setResults([]);
      setThings([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      searchCompanies(typed)
        .then((found) => !cancelled && setResults(found.slice(0, 5)))
        .catch(() => !cancelled && setResults([]));
      fetchSuggest(typed)
        .then((found) => !cancelled && setThings(found.slice(0, 5)))
        .catch(() => !cancelled && setThings([]));
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [typed]);

  /** 적은 글이 이 기업의 이름이나 종목코드와 똑같은가. 그러면 Enter 가 질문이 아니라 이 기업으로 간다 */
  const isExact = (company: Company) => company.stock_code === typed || squeeze(shortName(company.name)) === squeeze(typed);

  const clear = () => {
    setText("");
    setResults([]);
    setThings([]);
  };
  const pick = (company: Company) => {
    clear();
    onPick(company);
  };

  const ask = (question: string) => {
    if (question.trim().length < 2 || chat.busy || chat.closed) return;
    clear();
    onAsk();
    void chat.ask(question);
  };

  async function submit() {
    if (typed.length < 1) return;
    // 기업 이름이나 종목코드를 그대로 적고 Enter 를 누른 경우. Agent를 부르지 않는다
    if (typed.length <= 20) {
      const found = await searchCompanies(typed).catch(() => [] as Company[]);
      const exact = found.find(isExact);
      if (exact) return pick(exact);
    }
    ask(typed);
  }

  return (
    <div className="composer">
      {examples && (
        <div className="examples-row">
          {EXAMPLES.map((q) => (
            <button key={q} onClick={() => ask(q)} disabled={chat.busy || chat.closed} title="Agent에게 묻습니다 (하루 횟수를 한 번 씁니다)">
              {q}
            </button>
          ))}
        </div>
      )}
      {results.length + things.length > 0 && (
        <ul className="composer-results">
          {things.map((item) => (
            <li key={`${item.kind}-${item.key}`}>
              <button
                onClick={() => {
                  clear();
                  onPickProduct(item);
                }}
              >
                <span>{item.name}</span>
                <span className="sub">
                  {KIND_LABELS[item.kind]} · 기업 {item.companies}곳
                </span>
                <em>이것을 파는 기업 보기</em>
              </button>
            </li>
          ))}
          {results.map((company) => (
            <li key={company.id}>
              <button onClick={() => pick(company)}>
                <span>{shortName(company.name)}</span>
                <span className="sub">
                  {company.stock_code ?? "비상장"}
                  {company.group ? ` · ${company.group}` : ""}
                </span>
                <em>기업으로 이동{isExact(company) ? " · Enter" : ""}</em>
              </button>
            </li>
          ))}
          {typed.length >= 2 && !chat.closed && (
            <li>
              <button className="ask-row" onClick={() => ask(typed)} disabled={chat.busy}>
                <span>“{typed}”</span>
                <em>Agent에게 묻기{results.some(isExact) ? "" : " · Enter"}</em>
              </button>
            </li>
          )}
        </ul>
      )}
      <form
        className="composer-input"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <input
          value={text}
          onChange={(event) => setText(event.target.value)}
          onFocus={onFocus}
          maxLength={300}
          placeholder={HINTS[hint]}
          aria-label="기업·제품 찾기 또는 질문"
        />
        <button disabled={typed.length < 1}>{chat.busy ? "답하는 중…" : "보내기"}</button>
      </form>
      <div className="composer-status" title={chat.status?.enabled ? `답하는 모델: ${chat.status.model}` : undefined}>
        <span>기업·제품 이름은 횟수 없이 바로 찾습니다</span>
        {" · "}
        <span>
          {chat.status === null
            ? "Agent 상태를 확인하지 못했습니다"
            : !chat.status.enabled
              ? "Agent가 아직 연결되지 않았습니다"
              : chat.owner
                ? "운영자로 열려 있어 질문 횟수에 한도가 없습니다"
                : chat.left === 0
                ? "오늘 물을 수 있는 횟수를 다 썼습니다"
                : `질문은 오늘 ${chat.left}번 남았습니다`}
        </span>
        {" · "}
        <span>매수·매도 판단은 답하지 않습니다</span>
      </div>
    </div>
  );
}

function Tools({ result }: { result: AskResult }) {
  const [open, setOpen] = useState(false);
  if (result.tools.length === 0) return null;
  return (
    <div className="tools">
      <button className="tools-head" onClick={() => setOpen(!open)}>
        조회 {result.tools.length}번 {open ? "접기" : "보기"}
      </button>
      {open &&
        result.tools.map((tool, i) => (
          <div key={i} className="tool">
            <b>{tool.name}</b>
            <code>{JSON.stringify(tool.input)}</code>
            {tool.error ? <em>{tool.error}</em> : tool.total !== null && <em>{tool.total}건</em>}
          </div>
        ))}
    </div>
  );
}
