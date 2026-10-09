import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { askAgent, fetchAskStatus, searchCompanies } from "./api";
import { shortName } from "./Graph";
import type { AskResult, AskStatus, Company } from "./types";

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
  "기업 이름이나 종목코드, 또는 궁금한 것을 적어 보세요",
  "농심은 뭘 팔아서 돈을 벌어?",
  "게임을 만드는 회사는 어디야?",
  "카카오가 지분을 가진 상장사는?",
  "반도체 장비를 만드는 회사를 알려줘",
  "한화오션이 올해 공시한 공급계약을 금액이 큰 순서로",
];

/** 답에 나온 14자리 접수번호를 DART 원문 링크로 바꾼다. */
const linkReceipts = (text: string) => text.replace(/(?<![\d/=])(20\d{12})(?!\d)/g, `[$1](${DART}$1)`);
const squeeze = (text: string) => text.replace(/\s/g, "").toLowerCase();

type Turn = { question: string; result?: AskResult; error?: string };

/** 질문과 답의 기록. 입력 칸(그래프 아래)과 답이 보이는 칸(오른쪽)이 떨어져 있어서 화면 맨 위에서 쥐고 내려 준다 */
export function useChat(onShow: (result: AskResult | null) => void) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<AskStatus | null>(null);

  useEffect(() => {
    fetchAskStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  const left = status ? Math.min(status.left_for_you, status.left_today) : null;
  const closed = status !== null && (!status.enabled || left === 0);

  const ask = useCallback(
    async (question: string) => {
      const clean = question.trim();
      if (clean.length < 2 || busy || closed) return false;
      setBusy(true);
      setTurns((all) => [...all, { question: clean }]);
      try {
        const result = await askAgent(clean);
        setTurns((all) => all.map((t, i) => (i === all.length - 1 ? { ...t, result } : t)));
        setStatus((s) => (s ? { ...s, left_for_you: result.left_for_you, left_today: s.left_today - 1 } : s));
        if (result.graph.nodes.length > 0) onShow(result);
      } catch (error) {
        setTurns((all) => all.map((t, i) => (i === all.length - 1 ? { ...t, error: (error as Error).message } : t)));
      } finally {
        setBusy(false);
      }
      return true;
    },
    [busy, closed, onShow],
  );

  return { turns, busy, status, left, closed, ask };
}

export type ChatState = ReturnType<typeof useChat>;

/** 답을 기다리는 동안. 답은 한꺼번에 오므로 얼마나 지났는지만 보여 준다 */
function Waiting() {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setSeconds((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <div className="a pending">
      공시 DB를 조회하는 중입니다… {seconds}초<span className="fine"> 보통 10~20초 걸립니다</span>
    </div>
  );
}

interface LogProps {
  chat: ChatState;
  /** 지금 그래프에 띄운 답. 다른 답을 누르면 그 답의 그래프로 바뀐다 */
  shown: AskResult | null;
  onShow: (result: AskResult | null) => void;
}

/** 오른쪽 칸에 쌓이는 질문과 답 */
export function ChatLog({ chat, shown, onShow }: LogProps) {
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // 새 브라우저에서는 scrollIntoView 가 Promise 를 돌려주므로, 그 값을 effect 의 반환값으로 내보내지 않는다
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [chat.turns, chat.busy]);

  return (
    <div className="chat-log">
      {chat.turns.length === 0 && <p className="empty">아래 입력 칸에 궁금한 것을 적으면 답이 여기에 쌓입니다.</p>}
      {chat.turns.map((turn, i) => (
        <div key={i} className="turn">
          <div className="q">{turn.question}</div>
          {turn.error && <div className="a error">{turn.error}</div>}
          {turn.result && (
            <div className={turn.result === shown ? "a shown" : "a"}>
              <Tools result={turn.result} />
              <div className="answer">
                <Markdown remarkPlugins={[remarkGfm]} components={{ a: (p) => <a {...p} target="_blank" rel="noreferrer" /> }}>
                  {linkReceipts(turn.result.answer)}
                </Markdown>
              </div>
              <div className="sources">
                <b>출처</b>
                {turn.result.sources.length === 0 && <span>이 답은 근거로 든 공시가 없습니다 (조회 결과가 없거나, 답하지 않는 질문입니다)</span>}
                {turn.result.sources.map((source) => (
                  <a key={source.rcept_no} href={source.url} target="_blank" rel="noreferrer" title="DART 공시 원문">
                    <em>{source.filed ?? source.rcept_no}</em>
                    {source.company ? `${source.company} · ${source.report}` : `접수번호 ${source.rcept_no}`}
                  </a>
                ))}
              </div>
              <div className="a-foot">
                <span>
                  {turn.result.seconds}초 · {turn.result.tokens.toLocaleString()}토큰 · 조회 시점 {turn.result.as_of}
                  {turn.result.graph.nodes.length > 0 &&
                    ` · 그래프에 기업 ${turn.result.graph.nodes.filter((node) => !node.kind).length}곳 (답은 그 일부입니다)`}
                </span>
                {turn.result.graph.nodes.length > 0 && turn.result !== shown && <button onClick={() => onShow(turn.result!)}>그래프에 보기</button>}
              </div>
              {turn.result.unverified_citations.length > 0 && (
                <div className="warn">조회 결과에 없는 접수번호가 답에 있습니다: {turn.result.unverified_citations.join(", ")}</div>
              )}
            </div>
          )}
          {!turn.result && !turn.error && <Waiting />}
        </div>
      ))}
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
}

/** 그래프 아래의 입력 칸 하나. 기업 이름을 적으면 DB에서 바로 찾고, 문장을 적으면 Agent에게 묻는다 */
export function Composer({ chat, onPick, onAsk, examples }: ComposerProps) {
  const [text, setText] = useState("");
  const [results, setResults] = useState<Company[]>([]);
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
      return;
    }
    let cancelled = false;
    const timer = setTimeout(
      () =>
        searchCompanies(typed)
          .then((found) => !cancelled && setResults(found.slice(0, 6)))
          .catch(() => !cancelled && setResults([])),
      200,
    );
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [typed]);

  /** 적은 글이 이 기업의 이름이나 종목코드와 똑같은가. 그러면 Enter 가 질문이 아니라 이 기업으로 간다 */
  const isExact = (company: Company) => company.stock_code === typed || squeeze(shortName(company.name)) === squeeze(typed);

  const pick = (company: Company) => {
    setText("");
    setResults([]);
    onPick(company);
  };

  const ask = (question: string) => {
    if (question.trim().length < 2 || chat.busy || chat.closed) return;
    setText("");
    setResults([]);
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
      {results.length > 0 && (
        <ul className="composer-results">
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
        <input value={text} onChange={(event) => setText(event.target.value)} maxLength={300} placeholder={HINTS[hint]} aria-label="기업 찾기 또는 질문" />
        <button disabled={typed.length < 1}>{chat.busy ? "답하는 중…" : "보내기"}</button>
      </form>
      <div className="composer-status">
        기업 이름·종목코드는 바로 찾고 횟수를 쓰지 않습니다 ·{" "}
        {chat.status === null
          ? "Agent 상태를 확인하지 못했습니다"
          : !chat.status.enabled
            ? "Agent가 아직 연결되지 않았습니다"
            : chat.left === 0
              ? "오늘 물을 수 있는 횟수를 다 썼습니다. '평가 문항'에서 미리 돌려 둔 답을 볼 수 있습니다"
              : `질문은 오늘 ${chat.left}번 더 할 수 있습니다 (${chat.status.model})`}{" "}
        · 매수·매도 판단은 답하지 않습니다
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
