import { useCallback, useEffect, useRef, useState } from "react";
import { memoStream, type MemoEvent, type MemoResult } from "./api";
import { TOOL_LABELS } from "./Chat";
import type { GraphNode } from "./types";

/** 뉴스 메모 Agent: 뉴스 한 건을 받아, 무엇을 조회할지 스스로 정해 공시를 찾아보고, 근거를 코드가 검사한 메모를 남긴다.
 * 조회하는 동안 찾은 기업이 그래프에서 차례로 켜진다. 미리 돌려 둔 예시는 비용 없이 같은 과정을 다시 보여 준다. */

type Step =
  | { kind: "say"; text: string }
  | { kind: "tool"; name: string; input: Record<string, unknown>; done: boolean; total?: number | null; receipts?: number; error?: string | null; found?: number }
  | { kind: "check"; problems: string[] }
  | { kind: "memo"; left: number; retried: boolean };

interface News {
  title: string;
  text?: string;
  day?: string;
}

export interface MemoRun {
  id: string;
  news: News;
  steps: Step[];
  result?: MemoResult;
  error?: string;
  /** 미리 돌려 둔 예시를 다시 보여 주는 것 (Agent를 부르지 않는다) */
  replay?: boolean;
}

/** 미리 돌려 둔 예시: 그때 서버가 보낸 사건과 걸린 시각(밀리초), 그리고 결과 */
interface Example {
  news: News;
  events: { at: number; event: MemoEvent }[];
  result: MemoResult;
}

const REL: Record<string, string> = { equity: "지분", supply_contract: "공급계약", affiliate: "계열", stake_acquisition: "지분 취득", stake_disposal: "지분 처분" };
const KEY = "memos";
const KEEP = 12;
const newId = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
const today = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);   // 한국 날짜

/** 그래프 라이브러리가 점과 선에 붙이는 값을 빼고 적어 둔다 */
function plain(result: MemoResult): MemoResult {
  const end = (side: unknown) => (typeof side === "object" && side !== null ? (side as GraphNode).id : side);
  return {
    ...result,
    graph: {
      nodes: result.graph.nodes.map(({ id, name, legal_name, stock_code, listed, group, stage, sector, market, in_scope, kind, degree, focus, mentioned }) =>
        ({ id, name, legal_name, stock_code, listed, group, stage, sector, market, in_scope, kind, degree, focus, mentioned })),
      links: result.graph.links.map(({ source, target, type, count, value, label }) => ({ source: end(source), target: end(target), type, count, value, label })),
    } as MemoResult["graph"],
  };
}

function load(): MemoRun[] {
  try {
    return (JSON.parse(localStorage.getItem(KEY) ?? "[]") as MemoRun[]).filter((run) => run.result);
  } catch {
    return [];
  }
}

function save(runs: MemoRun[]) {
  try {
    const kept = runs.filter((run) => run.result && !run.replay).slice(-KEEP).map((run) => ({ ...run, result: plain(run.result!) }));
    localStorage.setItem(KEY, JSON.stringify(kept));
  } catch {
    /* 적어 두지 못해도 지금 화면에는 남는다 */
  }
}

/** 사건 하나를 단계 목록에 더한다 */
function advance(steps: Step[], event: MemoEvent): Step[] {
  switch (event.kind) {
    case "say":
      return [...steps, { kind: "say", text: event.text }];
    case "tool":
      return [...steps, { kind: "tool", name: event.name, input: event.input, done: false }];
    case "result": {
      const at = steps.findIndex((step) => step.kind === "tool" && !step.done && step.name === event.name);
      if (at < 0) return steps;
      const step = steps[at] as Extract<Step, { kind: "tool" }>;
      const done = { ...step, done: true, total: event.total, receipts: event.receipts, error: event.error, found: event.companies.length };
      return steps.map((s, i) => (i === at ? done : s));
    }
    case "check":
      return [...steps, { kind: "check", problems: event.problems }];
    case "memo":
      return [...steps, { kind: "memo", left: event.left, retried: event.retried }];
  }
}

/** 화면 바깥(그래프)에 알리는 것: 조회하면서 찾은 기업, 끝난 메모 */
export interface MemoScreen {
  onFound: (title: string, highlight: number[], also: number[], first: boolean) => void;
  onDone: (title: string, result: MemoResult) => void;
}

export function useMemoAgent(screen: MemoScreen) {
  const [runs, setRuns] = useState<MemoRun[]>(load);
  const [current, setCurrent] = useState<string | null>(() => load().at(-1)?.id ?? null);
  const [busy, setBusy] = useState(false);
  const [left, setLeft] = useState<number | null>(null);
  const screenRef = useRef(screen);
  screenRef.current = screen;
  useEffect(() => save(runs), [runs]);

  /** 사건을 받는 쪽. 조회 결과의 기업을 모아 그래프에 알린다 (기업 찾기로 하나만 나온 곳은 주인공 후보라 크게) */
  const listener = (id: string, title: string) => {
    const strong = new Set<number>();
    const also = new Set<number>();
    let first = true;
    return (event: MemoEvent) => {
      setRuns((all) => all.map((run) => (run.id === id ? { ...run, steps: advance(run.steps, event) } : run)));
      if (event.kind === "result" && event.companies.length > 0) {
        if (event.name === "find_company" && event.total === 1) event.companies.forEach((c) => strong.add(c));
        else event.companies.slice(0, 30).forEach((c) => also.add(c));
        screenRef.current.onFound(title, [...strong], [...also].filter((c) => !strong.has(c)).slice(0, 60), first);
        first = false;
      }
    };
  };

  const finish = (id: string, title: string, result: MemoResult) => {
    setRuns((all) => all.map((run) => (run.id === id ? { ...run, result } : run)));
    if (result.graph.nodes.length > 0) screenRef.current.onDone(title, result);
  };

  const start = useCallback(
    async (news: News) => {
      if (busy) return;
      const id = newId();
      setBusy(true);
      setRuns((all) => [...all, { id, news, steps: [] }]);
      setCurrent(id);
      try {
        const result = await memoStream(news, listener(id, news.title));
        finish(id, news.title, result);
        setLeft(result.left_for_you);
      } catch (error) {
        setRuns((all) => all.map((run) => (run.id === id ? { ...run, error: (error as Error).message } : run)));
      } finally {
        setBusy(false);
      }
    },
    [busy],
  );

  /** 예시를 그때의 순서대로 다시 보여 준다. 기다린 시간은 줄여서 */
  const replay = useCallback(
    async (example: Example) => {
      if (busy) return;
      const id = newId();
      setBusy(true);
      setRuns((all) => [...all.filter((run) => !run.replay), { id, news: example.news, steps: [], replay: true }]);
      setCurrent(id);
      const hear = listener(id, example.news.title);
      let clock = 0;
      for (const { at, event } of example.events) {
        await new Promise((done) => setTimeout(done, Math.min(1400, Math.max(250, (at - clock) * 0.35))));
        clock = at;
        hear(event);
      }
      await new Promise((done) => setTimeout(done, 500));
      finish(id, example.news.title, structuredClone(example.result));
      setBusy(false);
    },
    [busy],
  );

  const show = useCallback((id: string) => {
    setCurrent(id);
    const run = runs.find((r) => r.id === id);
    if (run?.result && run.result.graph.nodes.length > 0) screenRef.current.onDone(run.news.title, structuredClone(run.result));
  }, [runs]);

  return { runs, current: runs.find((run) => run.id === current) ?? null, busy, left, start, replay, show };
}

export type MemoAgent = ReturnType<typeof useMemoAgent>;

/** 도구 입력을 사람이 읽을 말로 ("공급계약 · 한화오션", "제품 표에서 찾기: LNG선") */
function detail(input: Record<string, unknown>, nameOf: (id: number) => string | undefined): string {
  const parts: string[] = [];
  if (typeof input.name === "string") parts.push(input.name);
  if (Array.isArray(input.keywords)) parts.push((input.keywords as string[]).join(", "));
  for (const key of ["company_id", "from_id", "to_id"]) {
    const id = input[key];
    if (typeof id === "number") parts.push(nameOf(id) ?? `기업 ${id}`);
  }
  if (typeof input.rel_type === "string") parts.push(REL[input.rel_type] ?? input.rel_type);
  if (typeof input.doc_type === "string") parts.push(REL[input.doc_type] ?? input.doc_type);
  return parts.join(" · ");
}

function Trace({ run, nameOf }: { run: MemoRun; nameOf: (id: number) => string | undefined }) {
  const [seconds, setSeconds] = useState(0);
  const working = !run.result && !run.error;
  useEffect(() => {
    if (!working) return;
    setSeconds(0);
    const timer = setInterval(() => setSeconds((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [working, run.id]);
  const tools = run.steps.filter((step) => step.kind === "tool").length;
  const asOf = run.result?.as_of;
  return (
    <ol className="trace">
      <li className="t-start">
        <b>뉴스를 받음</b>
        <span>{asOf ? `조회 시점 ${asOf} (뉴스 전날까지 나온 공시만)` : "뉴스 전날까지 나온 공시만 봅니다"}</span>
      </li>
      {run.steps.map((step, i) =>
        step.kind === "say" ? (
          <li key={i} className="t-say">
            <span>{step.text}</span>
          </li>
        ) : step.kind === "tool" ? (
          <li key={i} className={step.done ? (step.error ? "t-tool t-error" : "t-tool") : "t-tool t-now"}>
            <b>{TOOL_LABELS[step.name] ?? step.name}</b>
            <span className="t-input">{detail(step.input, nameOf)}</span>
            {step.done && (
              <em>
                {step.error
                  ? "결과 없음"
                  : [step.total !== null && step.total !== undefined ? `${step.total}건` : null, step.receipts ? `공시 ${step.receipts}개` : null]
                      .filter(Boolean)
                      .join(" · ") || "읽음"}
              </em>
            )}
          </li>
        ) : step.kind === "check" ? (
          <li key={i} className="t-check">
            <b>근거 검사에서 걸림 {step.problems.length}개</b>
            <span>메모의 회사와 접수번호가 조회 결과에 있었는지, 그 공시를 낸 곳이 맞는지 코드가 확인했습니다. Agent에게 다시 조회하거나 빼고 고쳐 쓰게 했습니다.</span>
            <ul>
              {step.problems.slice(0, 4).map((problem, j) => (
                <li key={j}>{problem}</li>
              ))}
              {step.problems.length > 4 && <li>그 밖에 {step.problems.length - 4}개</li>}
            </ul>
          </li>
        ) : (
          <li key={i} className={step.left > 0 ? "t-memo t-warn" : "t-memo"}>
            <b>
              {step.left > 0
                ? `고쳐 쓴 메모에도 확인되지 않는 것 ${step.left}개 → 코드가 뺌`
                : step.retried
                  ? "고쳐 쓴 메모가 근거 검사를 통과"
                  : "메모가 근거 검사를 통과"}
            </b>
          </li>
        ),
      )}
      {working && (
        <li className="t-wait">
          {run.steps.length === 0 ? "뉴스를 읽고 무엇을 조회할지 정하는 중" : "조회한 것을 읽는 중"} · {seconds}초
        </li>
      )}
      {run.result && (
        <li className="t-end">
          <b>메모 완성</b>
          <span>
            조회 {tools}번 · {Math.round(run.result.seconds)}초{run.replay ? " · 미리 돌려 둔 예시" : ""}
          </span>
        </li>
      )}
      {run.error && <li className="t-error-line">{run.error}</li>}
    </ol>
  );
}

/** 보고서 이름을 칩에 맞게 줄인다 ("[기재정정]단일판매ㆍ공급계약체결" → "공급계약 정정") */
function reportName(report: string): string {
  const fixed = /정정/.test(report) ? " 정정" : "";
  const plain = report.replace(/\[[^\]]*\]/g, "").replace(/\s+/g, "");
  const known: [RegExp, string][] = [[/사업보고서/, "사업보고서"], [/반기보고서/, "반기보고서"], [/분기보고서/, "분기보고서"], [/공급계약/, "공급계약"],
    [/주식등의대량보유/, "5% 보고"], [/타법인주식/, "지분 취득·처분"], [/최대주주/, "최대주주 변경"]];
  const hit = known.find(([pattern]) => pattern.test(plain));
  return (hit ? hit[1] : plain.slice(0, 10)) + fixed;
}

function Receipts({ numbers, result }: { numbers: string[]; result: MemoResult }) {
  return (
    <span className="receipts">
      {numbers.map((no) => {
        const source = result.sources[no];
        return (
          <a key={no} href={source?.url ?? `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${no}`} target="_blank" rel="noreferrer"
             title={source ? `${source.company ?? ""} · ${source.report} · ${source.filed} (${no})` : no}>
            {source ? `${reportName(source.report)} · ${source.filed.slice(2)}` : `공시 ${no.slice(2, 4)}-${no.slice(4, 6)}-${no.slice(6, 8)}`}
          </a>
        );
      })}
    </span>
  );
}

function MemoCard({ result, onCompany }: { result: MemoResult; onCompany: (id: number) => void }) {
  const memo = result.memo;
  if (!memo) return <p className="empty drawer-note">메모를 만들지 못했습니다. 다른 제목으로 다시 해 보세요.</p>;
  return (
    <article className="memo-card">
      <p className="memo-summary">{memo.summary}</p>
      {memo.subjects.length > 0 && (
        <div className="memo-line">
          <em>주인공</em>
          {memo.subjects.map((s) => (
            <button key={s.company_id} className="co" onClick={() => onCompany(s.company_id)} title={s.role}>
              {s.name}
            </button>
          ))}
        </div>
      )}
      {memo.topics.length > 0 && (
        <div className="memo-line">
          <em>닿는 제품·사업</em>
          {memo.topics.map((t) => (
            <span key={t} className="topic">
              {t}
            </span>
          ))}
        </div>
      )}
      {memo.subject_facts.map((f, i) => (
        <p key={i} className="memo-fact">
          주인공의 관련 공시: {f.fact} <Receipts numbers={f.rcept_nos} result={result} />
        </p>
      ))}
      <h4>공시로 이어진 종목 {memo.related.length}곳</h4>
      {memo.related.length === 0 && <p className="muted">공시로 이어진 상장사를 찾지 못했습니다.</p>}
      {memo.related.map((row) => (
        <div key={row.company_id} className="memo-row">
          <div className="memo-row-head">
            <button className="co" onClick={() => onCompany(row.company_id)}>
              {row.name}
            </button>
            <span className={`tag tag-${row.closeness === "직접" ? "near" : "far"}`}>
              {row.link} · {row.closeness}
            </span>
          </div>
          <p>{row.fact}</p>
          <p className="why">{row.why}</p>
          <Receipts numbers={row.rcept_nos} result={result} />
        </div>
      ))}
      {memo.unknown.length > 0 && (
        <>
          <h4>공시로 확인하지 못한 것</h4>
          <ul className="memo-list">{memo.unknown.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </>
      )}
      {memo.next.length > 0 && (
        <>
          <h4>이어서 볼 것</h4>
          <ul className="memo-list">{memo.next.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </>
      )}
      {result.dropped.length > 0 && (
        <p className="muted small">근거가 조회 결과로 확인되지 않아 뺀 종목 {result.dropped.length}곳: {result.dropped.map((d) => d.name ?? "?").join(", ")}</p>
      )}
      <p className="muted small">주가 전망이나 매매 판단이 아니라 공시로 확인되는 관계를 모은 것입니다. 답하는 모델 {result.model}.</p>
    </article>
  );
}

export function MemoPane({ agent, nameOf, onCompany, closed }: {
  agent: MemoAgent;
  nameOf: (id: number) => string | undefined;
  onCompany: (id: number) => void;
  /** 오늘 질문을 더 받을 수 없으면 (채팅과 같은 한도를 쓴다) */
  closed: boolean;
}) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [day, setDay] = useState(today);
  const [examples, setExamples] = useState<Example[]>([]);
  useEffect(() => {
    fetch("/memo-examples.json")
      .then((r) => (r.ok ? r.json() : []))
      .then(setExamples)
      .catch(() => setExamples([]));
  }, []);
  const run = agent.current;
  const submit = () => {
    if (title.trim().length < 5) return;
    void agent.start({ title: title.trim(), text: text.trim() || undefined, day });
  };
  const past = agent.runs.filter((r) => r.result && !r.replay && r.id !== run?.id).reverse();
  return (
    <div className="memo-pane">
      <div className="memo-intro">
        <b>뉴스 메모 Agent</b>
        <p>
          뉴스를 넣으면 Agent가 무엇을 조회할지 스스로 정해 공시를 찾아보고, 공시로 이어진 상장사를 메모로 남깁니다. 메모의 회사와 접수번호는 조회 결과에
          있었는지 코드가 검사하고, 걸리면 Agent가 고쳐 씁니다.
        </p>
      </div>
      <form
        className="memo-form"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="뉴스 제목 (예: ○○, 2조 원 규모 LNG선 수주)" maxLength={200} />
        <details>
          <summary>기사 앞부분 붙여 넣기 (선택)</summary>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} maxLength={4000}
                    placeholder="붙여 넣으면 Agent가 자료로만 읽습니다. 메모에 기사 문장을 옮기지 않습니다." />
        </details>
        <div className="memo-form-row">
          <label>
            뉴스 날짜 <input type="date" value={day} max={today()} min="2025-01-02" onChange={(e) => setDay(e.target.value)} />
          </label>
          <button type="submit" disabled={agent.busy || closed || title.trim().length < 5}>
            {agent.busy ? "조사하는 중…" : "메모 만들기"}
          </button>
        </div>
        {closed && <p className="muted small">오늘 받을 수 있는 횟수를 다 썼습니다. 아래 예시는 비용 없이 볼 수 있습니다.</p>}
        {agent.left !== null && !closed && <p className="muted small">오늘 남은 횟수 {agent.left}번 (채팅과 함께 셉니다)</p>}
      </form>
      {examples.length > 0 && (
        <div className="memo-examples">
          <em>미리 돌려 둔 예시 (Agent를 다시 부르지 않고 그때의 과정을 보여 줍니다)</em>
          {examples.map((example) => (
            <button key={example.news.title} disabled={agent.busy} onClick={() => void agent.replay(example)}>
              {example.news.day} · {example.news.title}
            </button>
          ))}
        </div>
      )}
      {run && (
        <section className="memo-run">
          <h3>{run.news.title}</h3>
          <Trace run={run} nameOf={nameOf} />
          {run.result && <MemoCard result={run.result} onCompany={onCompany} />}
        </section>
      )}
      {past.length > 0 && (
        <div className="memo-past">
          <em>이 브라우저에서 만든 메모</em>
          {past.map((r) => (
            <button key={r.id} onClick={() => agent.show(r.id)}>
              {r.news.day ?? r.result?.news.date} · {r.news.title}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
