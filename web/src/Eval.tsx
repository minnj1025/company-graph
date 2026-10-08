import { useEffect, useMemo, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

const DART = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=";

export type Verdict = "correct" | "partial" | "unanswered" | "wrong";

interface Side {
  verdict: Verdict;
  reason: string;
  answer: string;
  seconds: number;
  tokens: number;
}

interface AgentSide extends Side {
  tools: { name: string; input: Record<string, unknown>; total: number | null; error: string | null }[];
  unverified: string[];
}

interface BaselineSide extends Side {
  confidence: string;
  sources: string[];
  searches: number;
  fetches: number;
  fetch_failures: number;
  opened_original: boolean;
  tool_uses: number;
}

export interface EvalItem {
  id: string;
  type: string;
  question: string;
  as_of: string;
  gold: string;
  evidence: string[];
  why_hard: string;
  agent: AgentSide;
  /** 이 시험으로 찾은 결함을 고친 뒤 다시 돌린 답 */
  after: AgentSide;
  baseline?: BaselineSide;
}

export interface EvalData {
  agent_model: string;
  baseline_model: string;
  types: Record<string, string>;
  items: EvalItem[];
}

export const VERDICTS: [Verdict, string, string][] = [
  ["correct", "정답", "#6fd08c"],
  ["partial", "부분 정답", "#ffb454"],
  ["unanswered", "답하지 못함", "#8d99ab"],
  ["wrong", "틀린 사실을 답함", "#ff6b6b"],
];
const LABEL = Object.fromEntries(VERDICTS.map(([key, label]) => [key, label])) as Record<Verdict, string>;
const COLOR = Object.fromEntries(VERDICTS.map(([key, , color]) => [key, color])) as Record<Verdict, string>;

export function useEval(): EvalData | null {
  const [data, setData] = useState<EvalData | null>(null);
  useEffect(() => {
    fetch("/eval-v2.json")
      .then((r) => r.json())
      .then(setData)
      .catch(() => setData(null));
  }, []);
  return data;
}

const count = (sides: Side[]) => VERDICTS.map(([key]) => sides.filter((s) => s.verdict === key).length);

export const median = (values: number[]) => {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};

/** 정답률의 95% 구간 (Wilson). 문항 수가 적을 때 구간이 얼마나 넓은지 보이려고 쓴다. */
export function wilson(hit: number, n: number): [number, number] {
  const z = 1.96;
  const p = hit / n;
  const mid = (p + (z * z) / (2 * n)) / (1 + (z * z) / n);
  const half = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / (1 + (z * z) / n);
  return [Math.round((mid - half) * 100), Math.round((mid + half) * 100)];
}

/** 판정 네 가지를 한 줄 막대로. */
export function VerdictBar({ sides, slim }: { sides: Side[]; slim?: boolean }) {
  const counts = count(sides);
  return (
    <div className={slim ? "bar slim" : "bar"}>
      {VERDICTS.map(([key, label, color], i) =>
        counts[i] > 0 ? (
          <div key={key} style={{ flex: counts[i], background: color }} title={`${label} ${counts[i]}`}>
            {counts[i]}
          </div>
        ) : null,
      )}
    </div>
  );
}

export function VerdictLegend({ sides }: { sides?: Side[] }) {
  const counts = sides ? count(sides) : null;
  return (
    <div className="bar-legend">
      {VERDICTS.map(([key, label, color], i) => (
        <span key={key}>
          <i className="dot" style={{ background: color }} />
          {label} {counts?.[i]}
        </span>
      ))}
    </div>
  );
}

/** 종류별 결과. 왼쪽 막대는 Agent(그 종류의 모든 문항), 오른쪽 네모는 웹 검색 Claude(종류마다 두 문항). */
export function ByType({ data }: { data: EvalData }) {
  return (
    <div className="bytype">
      <div className="bytype-head">
        <span>문항 종류</span>
        <span>Agent (고친 뒤)</span>
        <span>웹 검색</span>
      </div>
      {Object.entries(data.types).map(([type, label]) => {
        const items = data.items.filter((item) => item.type === type);
        return (
          <div key={type} className="bytype-row">
            <span>
              <em>{type}</em> {label}
            </span>
            <VerdictBar sides={items.map((item) => item.after)} slim />
            <span className="squares">
              {items
                .filter((item) => item.baseline)
                .map((item) => (
                  <i key={item.id} style={{ background: COLOR[item.baseline!.verdict] }} title={`${item.id} ${LABEL[item.baseline!.verdict]}`} />
                ))}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** 문항마다 걸린 시간을 점으로 찍는다. 가로축은 로그 눈금이다. */
function Strip({ rows }: { rows: [string, number[], string][] }) {
  const ticks = [3, 10, 30, 100, 300];
  const x = (seconds: number) => 110 + ((Math.log10(Math.max(seconds, 2)) - Math.log10(2)) / (Math.log10(400) - Math.log10(2))) * 560;
  return (
    <svg className="strip" viewBox="0 0 690 118" role="img" aria-label="문항마다 걸린 시간">
      {ticks.map((tick) => (
        <g key={tick}>
          <line x1={x(tick)} x2={x(tick)} y1={8} y2={88} />
          <text x={x(tick)} y={106} textAnchor="middle">
            {tick}초
          </text>
        </g>
      ))}
      {rows.map(([label, values, color], row) => (
        <g key={label}>
          <text x={0} y={34 + row * 40} className="strip-label">
            {label}
          </text>
          {values.map((value, i) => (
            <circle key={i} cx={x(value)} cy={30 + row * 40 + ((i % 5) - 2) * 3} r={4.5} fill={color} />
          ))}
          <rect x={x(median(values)) - 1} y={14 + row * 40} width={2} height={32} fill="#1b2433" />
        </g>
      ))}
    </svg>
  );
}

function Versus({ title, ours, theirs, unit, note }: { title: string; ours: number; theirs: number; unit: string; note: string }) {
  const top = Math.max(ours, theirs);
  const show = (value: number) => (value >= 1000 ? Math.round(value).toLocaleString() : String(Math.round(value * 10) / 10));
  return (
    <div className="versus">
      <h4>{title}</h4>
      {(
        [
          ["Agent", ours, "#6fb7ff"],
          ["웹 검색", theirs, "#8d99ab"],
        ] as const
      ).map(([label, value, color]) => (
        <div key={label} className="versus-row">
          <span>{label}</span>
          <div className="meter">
            <div style={{ width: `${Math.max((value / top) * 100, 1.5)}%`, background: color }} />
          </div>
          <b>
            {show(value)}
            {unit}
          </b>
        </div>
      ))}
      <p>{note}</p>
    </div>
  );
}

/** 같은 문항을 푼 두 쪽을 견준다. */
export function Compare({ data }: { data: EvalData }) {
  const shared = data.items.filter((item) => item.baseline);
  const ours = shared.map((item) => item.agent);
  const theirs = shared.map((item) => item.baseline!);
  const seconds: [number, number] = [median(ours.map((s) => s.seconds)), median(theirs.map((s) => s.seconds))];
  const tokens: [number, number] = [median(ours.map((s) => s.tokens)), median(theirs.map((s) => s.tokens))];
  const cited = ours.filter((s) => /20\d{12}/.test(s.answer)).length;
  const opened = theirs.filter((s) => s.opened_original).length;
  const failed = theirs.reduce((sum, s) => sum + s.fetch_failures, 0);
  const fetched = theirs.reduce((sum, s) => sum + s.fetches, 0);
  return (
    <>
      <div className="duel">
        <div>
          <h4>
            이 DB를 쓰는 Agent <span>{data.agent_model}</span>
          </h4>
          <VerdictBar sides={ours} />
        </div>
        <div>
          <h4>
            웹 검색만 쓰는 Claude <span>{data.baseline_model}</span>
          </h4>
          <VerdictBar sides={theirs} />
        </div>
      </div>
      <VerdictLegend />
      <div className="versus-grid">
        <Versus title="문항당 걸린 시간 (중앙값)" ours={seconds[0]} theirs={seconds[1]} unit="초" note={`${(seconds[1] / seconds[0]).toFixed(0)}배 차이. 웹 검색은 검색과 페이지 읽기를 여러 번 되풀이합니다.`} />
        <Versus title="문항당 쓴 토큰 (중앙값)" ours={tokens[0]} theirs={tokens[1]} unit="" note="입력과 출력, 캐시에서 읽은 것을 모두 더했습니다. 웹 검색 쪽은 실행 도구의 기본 지시문이 포함된 값입니다." />
        <Versus title="근거 공시를 직접 확인한 문항" ours={cited} theirs={opened} unit={`/${shared.length}`} note={`Agent는 답에 접수번호를 적고, 그 번호가 조회 결과에 있었는지 검사합니다. 웹 검색은 공시 원문을 ${opened}번 열었고, 페이지 읽기 ${fetched}번 중 ${failed}번이 실패했습니다.`} />
      </div>
      <Strip
        rows={[
          ["Agent", ours.map((s) => s.seconds), "#6fb7ff"],
          ["웹 검색", theirs.map((s) => s.seconds), "#8d99ab"],
        ]}
      />
    </>
  );
}

/** 답에 나온 14자리 접수번호를 DART 원문 링크로 바꾼다. */
const linkReceipts = (text: string) => text.replace(/(?<![\d/=])(20\d{12})(?!\d)/g, `[$1](${DART}$1)`);

const Answer = ({ text }: { text: string }) => (
  <div className="answer">
    <Markdown remarkPlugins={[remarkGfm]} components={{ a: (props) => <a {...props} target="_blank" rel="noreferrer" /> }}>
      {linkReceipts(text)}
    </Markdown>
  </div>
);

const Chip = ({ verdict, who }: { verdict: Verdict; who?: string }) => (
  <span className="chip" style={{ background: COLOR[verdict], borderColor: COLOR[verdict], color: "#10151f" }}>
    {who && <em>{who}</em>}
    {LABEL[verdict]}
  </span>
);

function Question({ item, data }: { item: EvalItem; data: EvalData }) {
  const [open, setOpen] = useState(false);
  return (
    <details className="q" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <span className="q-id">{item.id}</span>
        <span className="q-text">{item.question}</span>
        <Chip verdict={item.agent.verdict} who="전" />
        <Chip verdict={item.after.verdict} who="뒤" />
        {item.baseline && <Chip verdict={item.baseline.verdict} who="웹" />}
      </summary>
      {open && (
        <div className="q-body">
          <dl>
            <dt>조회 시점</dt>
            <dd>{item.as_of}</dd>
            <dt>정답</dt>
            <dd>{item.gold}</dd>
            <dt>틀리기 쉬운 곳</dt>
            <dd>{item.why_hard}</dd>
            {item.evidence.length > 0 && (
              <>
                <dt>근거 공시</dt>
                <dd>
                  {item.evidence.map((no) => (
                    <a key={no} href={DART + no} target="_blank" rel="noreferrer">
                      {no}
                    </a>
                  ))}
                </dd>
              </>
            )}
          </dl>
          {(
            [
              ["Agent의 답 (처음)", item.agent],
              ["Agent의 답 (고친 뒤)", item.after],
            ] as const
          ).map(([label, side]) => (
            <div key={label}>
              <h5>
                {label} <Chip verdict={side.verdict} />
                <span>
                  {data.agent_model} · {side.seconds}초 · {side.tokens.toLocaleString()}토큰
                </span>
              </h5>
              <p className="reason">{side.reason}</p>
              <div className="tools">
                {side.tools.map((tool, i) => (
                  <div key={i} className="tool">
                    <b>{tool.name}</b>
                    <code>{JSON.stringify(tool.input)}</code>
                    {tool.error ? <em>{tool.error}</em> : tool.total !== null && <em>{tool.total}건</em>}
                  </div>
                ))}
                {side.tools.length === 0 && <div className="tool muted">도구를 부르지 않고 답했습니다</div>}
              </div>
              <Answer text={side.answer} />
            </div>
          ))}
          {item.baseline && (
            <>
              <h5>
                웹 검색만 쓴 Claude의 답 <Chip verdict={item.baseline.verdict} />
                <span>
                  {data.baseline_model} · {Math.round(item.baseline.seconds)}초 · {item.baseline.tokens.toLocaleString()}토큰 · 검색 {item.baseline.searches}번, 페이지 읽기{" "}
                  {item.baseline.fetches}번(실패 {item.baseline.fetch_failures})
                </span>
              </h5>
              <p className="reason">{item.baseline.reason}</p>
              <Answer text={item.baseline.answer} />
            </>
          )}
        </div>
      )}
    </details>
  );
}

/** 문항 전부. 종류와 판정으로 거를 수 있고, 줄을 누르면 정답과 두 쪽의 답이 열린다. */
export function Questions({ data }: { data: EvalData }) {
  const [type, setType] = useState("");
  const [verdict, setVerdict] = useState<Verdict | "">("");
  const [sharedOnly, setSharedOnly] = useState(false);
  const items = useMemo(
    () => data.items.filter((item) => (!type || item.type === type) && (!verdict || item.after.verdict === verdict) && (!sharedOnly || item.baseline)),
    [data, type, verdict, sharedOnly],
  );
  return (
    <div className="questions">
      <div className="q-filter">
        <select value={type} onChange={(event) => setType(event.target.value)}>
          <option value="">모든 종류</option>
          {Object.entries(data.types).map(([key, label]) => (
            <option key={key} value={key}>
              {key} {label}
            </option>
          ))}
        </select>
        <select value={verdict} onChange={(event) => setVerdict(event.target.value as Verdict | "")}>
          <option value="">모든 판정 (고친 뒤)</option>
          {VERDICTS.map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        <label>
          <input type="checkbox" checked={sharedOnly} onChange={(event) => setSharedOnly(event.target.checked)} />
          웹 검색과 견준 문항만
        </label>
        <span>{items.length}문항</span>
      </div>
      {items.map((item) => (
        <Question key={item.id} item={item} data={data} />
      ))}
    </div>
  );
}
