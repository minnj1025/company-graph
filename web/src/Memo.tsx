import { useCallback, useEffect, useRef, useState } from "react";
import { fetchDailyMemo, fetchDailyMemos, memoStream, type DailyMemoItem, type DailyPicked, type MemoEvent, type MemoResult } from "./api";
import { TOOL_LABELS } from "./Chat";
import { Followups } from "./MemoFollowups";
import { PriceMoves, type PriceCompany } from "./MemoPrices";
import type { GraphNode } from "./types";

/** 뉴스 분석 Agent: 뉴스 한 건을 받아, 무엇을 조회할지 스스로 정해 공시를 찾아보고, 근거를 코드가 검사한 메모를 남긴다.
 * 조회하는 동안 찾은 기업이 그래프에서 차례로 켜진다. 미리 돌려 둔 예시는 비용 없이 같은 과정을 다시 보여 준다. */

type Step =
  | { kind: "say"; text: string }
  | { kind: "tool"; name: string; input: Record<string, unknown>; done: boolean; total?: number | null; receipts?: number; error?: string | null; found?: number }
  | { kind: "check"; problems: string[] }
  | { kind: "memo"; left: number; retried: boolean }
  | { kind: "plan"; lookFor: string; plan: { do: string; tool: string }[]; changed: string | null };

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
  /** 미리 돌려 둔 예시나 매일 메모를 다시 보여 주는 것 (Agent를 부르지 않는다) */
  replay?: boolean;
  /** Agent가 매일 아침 스스로 고른 뉴스면 고른 까닭 */
  picked?: DailyPicked;
  /** 바로 여는 주소(?analysis=)에 쓰는 이름: 매일 분석은 번호, 예시는 ex1·ex2… (직접 넣은 뉴스는 없음) */
  link?: string;
}

/** 다시 보여 줄 때: 주소에 쓸 이름, 과정을 기다리지 않고 바로 결과를 보일지 */
interface ReplayOptions {
  link?: string;
  instant?: boolean;
}

/** 미리 돌려 둔 예시: 그때 서버가 보낸 사건과 걸린 시각(밀리초), 그리고 결과 */
interface Example {
  news: News;
  events: { at: number; event: MemoEvent }[];
  result: MemoResult;
  picked?: DailyPicked;
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
    case "plan":
      return [...steps, { kind: "plan", lookFor: event.look_for, plan: event.steps ?? [], changed: event.changed }];
    default:
      return steps;
  }
}

/** 계획의 단계마다 그 뒤(다음 계획 전까지)에 그 도구의 결과가 왔는지. 한 번 부른 것은 한 단계에만 센다 */
function planDone(steps: Step[], at: number): boolean[] {
  const plan = steps[at] as Extract<Step, { kind: "plan" }>;
  const next = steps.findIndex((step, i) => i > at && step.kind === "plan");
  const called = steps.slice(at + 1, next < 0 ? undefined : next).flatMap((step) => (step.kind === "tool" && step.done ? [step.name] : []));
  return plan.plan.map((item) => {
    const i = called.indexOf(item.tool);
    if (i < 0) return false;
    called.splice(i, 1);
    return true;
  });
}

function PlanCard({ steps, at, live }: { steps: Step[]; at: number; live: boolean }) {
  const step = steps[at] as Extract<Step, { kind: "plan" }>;
  const done = planDone(steps, at);
  const now = live ? done.indexOf(false) : -1;
  return (
    <li className={step.changed ? "t-plan t-replan" : "t-plan"}>
      <b>{step.changed ? "계획을 바꿈" : "조회 계획"}</b>
      {step.changed && <span className="t-changed">{step.changed}</span>}
      {step.lookFor && <span>{step.lookFor}</span>}
      <ol>
        {step.plan.map((item, i) => (
          <li key={i} className={done[i] ? "p-done" : i === now ? "p-now" : undefined}>
            <i aria-hidden>{done[i] ? "✓" : "○"}</i>
            <span>{item.do}</span>
            <small>{TOOL_LABELS[item.tool] ?? item.tool}</small>
          </li>
        ))}
      </ol>
    </li>
  );
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
  const skipRef = useRef(false);
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
    async (example: Example, options: ReplayOptions = {}) => {
      if (busy) return;
      const id = newId();
      setBusy(true);
      setRuns((all) => [...all.filter((run) => !run.replay), { id, news: example.news, steps: [], replay: true, picked: example.picked, link: options.link }]);
      setCurrent(id);
      skipRef.current = options.instant ?? false;
      const hear = listener(id, example.news.title);
      let clock = 0;
      for (const { at, event } of example.events) {
        if (!skipRef.current) await new Promise((done) => setTimeout(done, Math.min(1400, Math.max(250, (at - clock) * 0.35))));
        clock = at;
        hear(event);
      }
      if (!skipRef.current) await new Promise((done) => setTimeout(done, 500));
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

  /** 다시 보여 주는 중이면 남은 과정을 기다리지 않고 바로 메모를 보인다 */
  const skip = useCallback(() => {
    skipRef.current = true;
  }, []);

  /** 매일 분석 한 건을 받아 다시 보여 준다. 받은 뒤 ready 를 부른다 (화면을 분석 한 건으로 바꾸게) */
  const openDaily = useCallback(
    (memoId: number, ready: () => void, instant = false) =>
      fetchDailyMemo(memoId).then((memo) => {
        ready();
        return replay({ news: memo.news, events: memo.events, result: memo.result, picked: memo.picked }, { link: String(memoId), instant });
      }),
    [replay],
  );

  return { runs, current: runs.find((run) => run.id === current) ?? null, busy, left, start, replay, show, skip, openDaily };
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
  const asOf = run.result?.as_of;
  return (
    <ol className="trace">
      {run.picked && (
        <li className="t-start">
          <b>Agent가 고름</b>
          <span>
            전날 뉴스 {run.picked.titles.toLocaleString()}건 가운데 핵심 {run.picked.core}건, 그중 중요도 {run.picked.imp.toFixed(2)} · {run.picked.kind}
          </span>
        </li>
      )}
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
        ) : step.kind === "plan" ? (
          <PlanCard key={i} steps={run.steps} at={i} live={working && !run.steps.some((s, j) => j > i && s.kind === "plan")} />
        ) : step.kind === "check" ? (
          <li key={i} className="t-check">
            <b>근거 검사에서 걸림 {step.problems.length}개</b>
            <span>분석에 적은 회사·접수번호·금액·날짜가 조회 결과에 있었는지, 그 공시를 낸 곳이 맞는지 코드가 확인했습니다. Agent에게 다시 조회하거나 빼고 고쳐 쓰게 했습니다.</span>
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
                ? `고쳐 쓴 분석에도 확인되지 않는 것 ${step.left}개 → 근거 없는 종목은 코드가 빼고, 숫자는 표시함`
                : step.retried
                  ? "고쳐 쓴 분석이 근거 검사를 통과"
                  : "분석이 근거 검사를 통과"}
            </b>
          </li>
        ),
      )}
      {working && (
        <li className="t-wait">
          {run.steps.length === 0 ? "뉴스를 읽고 조회 계획을 세우는 중" : "조회한 것을 읽는 중"} · {seconds}초
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

/** 근거 검사를 거친 뒤에도 조회 결과에서 찾지 못한 숫자 */
function Unchecked({ numbers }: { numbers?: string[] }) {
  if (!numbers || numbers.length === 0) return null;
  return <span className="unchecked" title="Agent가 고쳐 쓴 뒤에도 이 숫자를 조회 결과에서 찾지 못했습니다. 원문으로 확인해 주세요">조회 결과에서 확인 안 된 숫자: {numbers.join(", ")}</span>;
}

function MemoCard({ result, newsTitle, onCompany }: { result: MemoResult; newsTitle: string; onCompany: (id: number) => void }) {
  const memo = result.memo;
  const rowRefs = useRef(new Map<number, HTMLDivElement>());
  if (!memo) return <p className="empty drawer-note">분석을 만들지 못했습니다. 다른 제목으로 다시 해 보세요.</p>;
  const near = memo.related.filter((row) => row.closeness === "직접").length;
  const jump = (id: number) => rowRefs.current.get(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  const priced: PriceCompany[] = [
    ...memo.subjects.map((s) => ({ id: s.company_id, role: "주인공" as const })),
    ...memo.related.map((r) => ({ id: r.company_id, role: r.closeness === "직접" ? ("직접" as const) : ("간접" as const) })),
  ];
  return (
    <article className="memo-card">
      <p className="memo-summary">{memo.summary}</p>

      <section className="memo-sec">
        <h4>뉴스에서 공시로 이어진 길</h4>
        <ol className="memo-flow">
          <li className="f-news">
            <em>뉴스</em>
            <span>{newsTitle}</span>
          </li>
          {memo.subjects.length > 0 && (
            <li className="f-subject">
              <em>주인공</em>
              <span>
                {memo.subjects.map((s) => (
                  <button key={s.company_id} className="co" onClick={() => onCompany(s.company_id)} title="그래프에서 이 기업 보기">
                    {s.name}
                  </button>
                ))}
                {memo.topics.length > 0 && <small> · 닿는 제품·사업: {memo.topics.join(", ")}</small>}
              </span>
            </li>
          )}
          {memo.related.length === 0 && (
            <li className="f-none">
              <em>이어진 종목</em>
              <span className="muted">공시로 이어진 상장사를 찾지 못했습니다</span>
            </li>
          )}
          {memo.related.map((row) => (
            <li key={row.company_id} className={row.closeness === "직접" ? "f-rel f-near" : "f-rel"}>
              <em>{row.link}</em>
              <span>
                <button className={`jump jump-${row.closeness === "직접" ? "near" : "far"}`} onClick={() => jump(row.company_id)} title="눌러서 근거 공시 보기">
                  {row.name}
                </button>
                <small className={row.closeness === "직접" ? "near" : undefined}> {row.closeness}</small>
                <span className="f-why">{row.why}</span>
              </span>
            </li>
          ))}
        </ol>
      </section>

      {result.as_of && (
        <Followups after={result.as_of} title={newsTitle} subjects={memo.subjects.map((s) => ({ id: s.company_id, name: s.name ?? "주인공" }))} />
      )}

      {priced.length > 0 && <PriceMoves day={result.news.date} companies={priced} onCompany={onCompany} />}

      <section className="memo-sec">
        <h4>
          근거 공시{memo.related.length > 0 && <span className="muted"> · 이어진 종목 {memo.related.length}곳{near > 0 && `(직접 ${near}곳)`}</span>}
        </h4>
        {memo.subject_facts.map((f, i) => (
          <div key={i} className="memo-row row-subject">
            <div className="memo-row-head">
              <span className="tag tag-far">주인공의 관련 공시</span>
            </div>
            <p className="fact">{f.fact}</p>
            <Unchecked numbers={f.unchecked_numbers} />
            <Receipts numbers={f.rcept_nos} result={result} />
          </div>
        ))}
        {memo.related.map((row) => (
          <div key={row.company_id} className={`memo-row ${row.closeness === "직접" ? "row-near" : "row-far"}`}
               ref={(el) => { if (el) rowRefs.current.set(row.company_id, el); else rowRefs.current.delete(row.company_id); }}>
            <div className="memo-row-head">
              <button className="co" onClick={() => onCompany(row.company_id)} title="그래프에서 이 기업 보기">
                {row.name}
              </button>
              <span className={`tag tag-${row.closeness === "직접" ? "near" : "far"}`}>
                {row.link} · {row.closeness}
              </span>
            </div>
            <p className="fact">{row.fact}</p>
            <Unchecked numbers={row.unchecked_numbers} />
            <Receipts numbers={row.rcept_nos} result={result} />
          </div>
        ))}
      </section>
      {memo.unknown.length > 0 && (
        <details className="memo-more">
          <summary>공시로 확인하지 못한 것 {memo.unknown.length}개</summary>
          <ul className="memo-list">{memo.unknown.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </details>
      )}
      {memo.next.length > 0 && (
        <details className="memo-more">
          <summary>이어서 볼 것 {memo.next.length}개</summary>
          <ul className="memo-list">{memo.next.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </details>
      )}
      {result.dropped.length > 0 && (
        <p className="muted small">근거가 조회 결과로 확인되지 않아 뺀 종목 {result.dropped.length}곳: {result.dropped.map((d) => d.name ?? "?").join(", ")}</p>
      )}
      <p className="muted small">주가 전망이나 매매 판단이 아니라 공시로 확인되는 관계를 모은 것입니다. 답하는 모델 {result.model}.</p>
    </article>
  );
}

/** Agent가 매일 아침 스스로 고른 전날 뉴스와 메모. 누르면 그때의 과정을 다시 보여 준다(Agent를 다시 부르지 않는다) */
function Daily({ agent, onOpen }: { agent: MemoAgent; onOpen: () => void }) {
  const [items, setItems] = useState<DailyMemoItem[] | null>(null);
  const [opening, setOpening] = useState<number | null>(null);
  useEffect(() => {
    fetchDailyMemos().then(setItems).catch(() => setItems([]));
  }, []);
  if (!items || items.length === 0) return null;
  const days = [...new Set(items.map((item) => item.day))];
  const open = (item: DailyMemoItem) => {
    setOpening(item.id);
    agent
      .openDaily(item.id, onOpen)
      .catch(() => undefined)
      .finally(() => setOpening(null));
  };
  return (
    <div className="memo-daily">
      <em>Agent가 매일 아침 스스로 고른 뉴스</em>
      {days.map((day, i) => {
        const today = items.filter((item) => item.day === day);
        const p = today[0].picked;
        const list = (
          <>
            <p className="muted small">
              시가총액 상위 {p.companies}곳의 {day.slice(5)} 뉴스 제목 {p.titles.toLocaleString()}건을 분류해 핵심 {p.core}건을 고르고, 중요도 순으로 {today.length}건을 분석했습니다.
            </p>
            {today.map((item) => (
              <button key={item.id} disabled={agent.busy || opening !== null} onClick={() => open(item)}>
                <span>
                  <b>{item.company}</b> {item.title}
                </span>
                <small>
                  중요도 {item.picked.imp.toFixed(2)} · {item.picked.kind} · 이어진 종목 {item.related}곳
                </small>
                {item.glance && <Glance glance={item.glance} />}
              </button>
            ))}
          </>
        );
        return i === 0 ? (
          <div key={day} className="memo-day">{list}</div>
        ) : (
          <details key={day} className="memo-day">
            <summary>{day}</summary>
            {list}
          </details>
        );
      })}
    </div>
  );
}

/** 목록 한 줄: 주인공의 뉴스 뒤 누적 %와 뉴스 뒤 주인공의 공시 */
function Glance({ glance }: { glance: NonNullable<DailyMemoItem["glance"]> }) {
  const signed = (v: number, unit: string) => `${v > 0 ? "+" : ""}${v.toFixed(1)}${unit}`;
  return (
    <span className="memo-glance">
      <span>
        {glance.change === null
          ? "주가: 아직 시세 없음"
          : `주인공 ${signed(glance.change, "%")}${glance.vs_median !== null ? ` (보통 종목 대비 ${signed(glance.vs_median, "%p")})` : ""} · ${glance.days}거래일`}
      </span>
      <span className={glance.same_amount > 0 ? "g-hit" : undefined}>
        {glance.count === 0
          ? "뒤 공시 없음"
          : `뒤 공시 ${glance.count}건${glance.contracts > 0 ? `(공급계약 ${glance.contracts})` : ""}${glance.same_amount > 0 ? " · ✓ 금액 같음" : ""}`}
      </span>
    </span>
  );
}

export function MemoPane({ agent, nameOf, onCompany, closed, linked, onLinkUsed }: {
  agent: MemoAgent;
  nameOf: (id: number) => string | undefined;
  onCompany: (id: number) => void;
  /** 오늘 질문을 더 받을 수 없으면 (채팅과 같은 한도를 쓴다) */
  closed: boolean;
  /** 주소(?analysis=)로 들어와서 바로 열 분석: 매일 분석 번호나 예시 ex1… */
  linked?: string | null;
  onLinkUsed?: () => void;
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
  // 목록(입력 칸, 매일 메모, 예시)과 메모 한 건을 나눠 보인다. 메모를 고르면 메모만 보인다
  const [view, setView] = useState<"list" | "run">("list");
  const paneRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    paneRef.current?.scrollTo({ top: 0 });
  }, [view, run?.id]);
  const openRun = () => setView("run");

  // 주소로 들어오면 그 분석을 과정 없이 바로 연다. 예시는 목록을 받은 뒤에
  useEffect(() => {
    if (!linked || agent.busy) return;
    const example = /^ex(\d+)$/.exec(linked);
    if (example) {
      if (examples.length === 0) return;
      onLinkUsed?.();
      const found = examples[Number(example[1]) - 1];
      if (!found) return;
      openRun();
      void agent.replay(found, { link: linked, instant: true });
      return;
    }
    onLinkUsed?.();
    if (/^\d+$/.test(linked)) void agent.openDaily(Number(linked), openRun, true).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linked, examples]);

  // 열린 분석의 주소를 주소창에 둔다 (복사해 나누면 그 분석이 바로 열린다)
  const link = view === "run" ? run?.link : undefined;
  useEffect(() => {
    const put = (value?: string) => {
      const params = new URLSearchParams(window.location.search);
      if (value) params.set("analysis", value);
      else params.delete("analysis");
      const query = params.toString();
      window.history.replaceState(window.history.state, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
    };
    put(link);
    return () => put(undefined);
  }, [link]);
  const [copied, setCopied] = useState(false);
  const copyLink = () => {
    if (!link) return;
    void navigator.clipboard?.writeText(`${window.location.origin}/?analysis=${link}`).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };
  const submit = () => {
    if (title.trim().length < 5) return;
    openRun();
    void agent.start({ title: title.trim(), text: text.trim() || undefined, day });
  };
  const past = agent.runs.filter((r) => r.result && !r.replay && r.id !== run?.id).reverse();
  if (view === "run" && run) {
    const tools = run.steps.filter((step) => step.kind === "tool").length;
    const plans = run.steps.filter((step) => step.kind === "plan").length;
    const fixed = run.steps.some((step) => step.kind === "check");
    const newsDay = run.news.day ?? run.result?.news.date;
    return (
      <div className="memo-pane" ref={paneRef}>
        <div className="memo-top">
          <button className="memo-back" onClick={() => setView("list")}>
            ← 목록
          </button>
          {working(run) && run.replay && (
            <button className="memo-skip" onClick={agent.skip} title="저장해 둔 과정을 기다리지 않고 결과를 바로 봅니다">
              분석 바로 보기
            </button>
          )}
          {link && run.result && (
            <button className="memo-link" onClick={copyLink} title="이 분석이 바로 열리는 주소를 복사합니다">
              {copied ? "복사함 ✓" : "링크 복사"}
            </button>
          )}
        </div>
        <header className="memo-head">
          <span className="memo-kicker">
            {newsDay && `${newsDay} 뉴스 · `}
            {run.picked ? `Agent가 고름 · 중요도 ${run.picked.imp.toFixed(2)} · ${run.picked.kind}` : run.replay ? "미리 돌려 둔 예시" : "직접 넣은 뉴스"}
          </span>
          <h3>{run.news.title}</h3>
          {run.result && (
            <span className="memo-meta">
              조회 {tools}번 · {Math.round(run.result.seconds)}초 · 근거 검사 {fixed ? "뒤 고쳐 씀" : "통과"}
              {run.result.as_of && ` · ${run.result.as_of}까지 나온 공시만 봄`}
            </span>
          )}
        </header>
        {run.result ? (
          <>
            <MemoCard result={run.result} newsTitle={run.news.title} onCompany={onCompany} />
            <details className="memo-how">
              <summary>
                Agent가 조사한 과정 보기{plans > 0 && (plans > 1 ? " · 조회 계획(중간에 바꿈)" : " · 조회 계획")} · 조회 {tools}번
              </summary>
              <Trace run={run} nameOf={nameOf} />
            </details>
          </>
        ) : (
          <Trace run={run} nameOf={nameOf} />
        )}
      </div>
    );
  }
  return (
    <div className="memo-pane" ref={paneRef}>
      <div className="memo-intro">
        <b>뉴스 분석 Agent</b>
        <p>
          뉴스를 넣으면 Agent가 조회 계획을 세워 공시를 찾아보고, 공시로 이어진 상장사와 그 까닭, 뉴스 뒤 주가 변화를 한 장에 모읍니다. 분석에 적은 회사·접수번호·금액은 조회 결과에 있었는지
          코드가 검사합니다.
        </p>
      </div>
      {run && (
        <button className="memo-resume" onClick={openRun}>
          <small>{working(run) ? "조사하는 중 · 이어 보기" : "보던 분석"}</small>
          <span>{run.news.title}</span>
        </button>
      )}
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
                    placeholder="붙여 넣으면 Agent가 자료로만 읽습니다. 분석에 기사 문장을 옮기지 않습니다." />
        </details>
        <div className="memo-form-row">
          <label>
            뉴스 날짜 <input type="date" value={day} max={today()} min="2025-01-02" onChange={(e) => setDay(e.target.value)} />
          </label>
          <button type="submit" disabled={agent.busy || closed || title.trim().length < 5}>
            {agent.busy ? "조사하는 중…" : "분석하기"}
          </button>
        </div>
        {closed && <p className="muted small">오늘 받을 수 있는 횟수를 다 썼습니다. 아래 예시는 비용 없이 볼 수 있습니다.</p>}
        {agent.left !== null && !closed && <p className="muted small">오늘 남은 횟수 {agent.left}번 (채팅과 함께 셉니다)</p>}
      </form>
      <Daily agent={agent} onOpen={openRun} />
      {examples.length > 0 && (
        <div className="memo-examples">
          <em>미리 돌려 둔 예시 (Agent를 다시 부르지 않고 그때의 과정을 보여 줍니다)</em>
          {examples.map((example, i) => (
            <button key={example.news.title} disabled={agent.busy} onClick={() => { openRun(); void agent.replay(example, { link: `ex${i + 1}` }); }}>
              {example.news.day} · {example.news.title}
            </button>
          ))}
        </div>
      )}
      {past.length > 0 && (
        <div className="memo-past">
          <em>이 브라우저에서 만든 분석</em>
          {past.map((r) => (
            <button key={r.id} onClick={() => { agent.show(r.id); openRun(); }}>
              {r.news.day ?? r.result?.news.date} · {r.news.title}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const working = (run: MemoRun) => !run.result && !run.error;
