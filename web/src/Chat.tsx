import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { askAgent, fetchAskStatus } from "./api";
import type { AskResult, AskStatus, GraphData } from "./types";

const DART = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=";
/** 처음 화면에 보이는 예시. 묶음마다 이 DB로 답할 수 있는 질문의 종류가 다르다 */
const SUGGESTIONS: [string, string[]][] = [
  ["누가 누구와", ["삼성물산의 최대주주와 지분율은?", "SK하이닉스에 올해 공급계약을 공시한 회사는?", "포스코홀딩스가 지분을 가진 상장사를 모두 알려줘"]],
  ["무엇을 하는 회사", ["전기차 배터리 분리막을 실제로 만드는 상장사와 매출 비중은?", "책 매출 비중이 큰 상장사는 어디야?", "HBM 장비를 만드는 회사를 매출 비중과 함께 알려줘"]],
  ["바뀐 것", ["한화오션이 올해 공시한 공급계약을 금액이 큰 순서로", "두산에너빌리티가 올해 낸 공급계약 중 나중에 정정된 것은?"]],
];
/** 입력 칸에 번갈아 보이는 질문 */
const HINTS = [
  "대한조선이 올해 공시한 공급계약은?",
  "감열지를 만드는 상장사는?",
  "카카오가 지분을 가진 상장사는?",
  "현대모비스에 공급계약을 공시한 회사는?",
  "웹툰 매출 비중이 큰 회사는?",
  "한미반도체의 최대주주와 특수관계인 지분은?",
  "원전 주기기를 만드는 회사와 최근 수주는?",
  "예스24는 어떤 사업으로 돈을 버나?",
];

/** 답에 나온 14자리 접수번호를 DART 원문 링크로 바꾼다. */
const linkReceipts = (text: string) => text.replace(/(?<![\d/=])(20\d{12})(?!\d)/g, `[$1](${DART}$1)`);

type Turn = { question: string; result?: AskResult; error?: string };

interface Props {
  /** 지금 그래프에 띄운 답. 다른 답을 누르면 그 답의 그래프로 바뀐다 */
  shown: AskResult | null;
  onShow: (result: AskResult | null) => void;
}

export function Chat({ shown, onShow }: Props) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<AskStatus | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const [hint, setHint] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => setHint((n) => (n + 1) % HINTS.length), 2500);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    fetchAskStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    // 새 브라우저에서는 scrollIntoView 가 Promise 를 돌려주므로, 그 값을 effect 의 반환값으로 내보내지 않는다
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, busy]);

  const left = status ? Math.min(status.left_for_you, status.left_today) : null;
  const closed = status !== null && (!status.enabled || left === 0);

  async function ask(question: string) {
    const clean = question.trim();
    if (clean.length < 2 || busy || closed) return;
    setText("");
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
  }

  return (
    <div className="chat">
      <div className="chat-log">
        {turns.length === 0 && (
          <div className="chat-intro">
            <p>
              공시에서 뽑은 관계와 사업 내용에 질문해 보세요. 답에는 근거 공시가 붙고, 조회된 기업과 관계만 왼쪽 그래프에 남습니다.
            </p>
            {SUGGESTIONS.map(([label, questions]) => (
              <div key={label} className="suggestion-group">
                <span>{label}</span>
                {questions.map((q) => (
                  <button key={q} className="suggestion" onClick={() => ask(q)} disabled={busy || closed}>
                    {q}
                  </button>
                ))}
              </div>
            ))}
            <p className="fine">
              매수·매도 판단은 답하지 않습니다. 질문은 사용량을 세기 위해 저장됩니다.
            </p>
          </div>
        )}
        {turns.map((turn, i) => (
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
                  </span>
                  {turn.result.graph.nodes.length > 0 && turn.result !== shown && (
                    <button onClick={() => onShow(turn.result!)}>그래프에 보기</button>
                  )}
                </div>
                {turn.result.unverified_citations.length > 0 && (
                  <div className="warn">조회 결과에 없는 접수번호가 답에 있습니다: {turn.result.unverified_citations.join(", ")}</div>
                )}
              </div>
            )}
            {!turn.result && !turn.error && <div className="a pending">공시 DB를 조회하는 중입니다…</div>}
          </div>
        ))}
        <div ref={bottom} />
      </div>
      <form
        className="chat-input"
        onSubmit={(event) => {
          event.preventDefault();
          ask(text);
        }}
      >
        <input
          value={text}
          onChange={(event) => setText(event.target.value)}
          maxLength={300}
          placeholder={closed ? "지금은 질문을 받지 않습니다" : HINTS[hint]}
          disabled={busy || closed}
        />
        <button disabled={busy || closed || text.trim().length < 2}>묻기</button>
      </form>
      <div className="chat-status">
        {status === null
          ? "Agent 상태를 확인하지 못했습니다"
          : !status.enabled
            ? "Agent가 아직 연결되지 않았습니다. '평가 문항' 탭에서 미리 돌려 둔 답을 볼 수 있습니다."
            : left === 0
              ? "오늘 물을 수 있는 횟수를 다 썼습니다. '평가 문항' 탭에서 미리 돌려 둔 답을 볼 수 있습니다."
              : `오늘 ${left}번 더 물을 수 있습니다 · ${status.model}`}
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

export type { GraphData };
