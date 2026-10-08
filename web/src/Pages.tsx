import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Meta } from "./types";

const DART = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=";
const REPO = "https://github.com/minnj1025/company-graph";

/** 검산 결과. 추출기를 다시 돌린 날(2026-10-08)의 값이며, 저장소 README와 같다. */
const CHECKS: [string, string, string][] = [
  ["공급계약: 계약금액 ÷ 최근 매출액이 공시에 적힌 비율과 맞는가", "11,739 / 11,853", "추출이 맞다는 근거"],
  ["취득·처분 결정: 금액 ÷ 자기자본이 적힌 비율과 맞는가", "2,901 / 2,923", "추출이 맞다는 근거"],
  ["계열: 읽은 줄 수가 표에 적힌 회사 수와 같은가", "6,301 / 6,438", "추출이 맞다는 근거"],
  ["과거 시점으로 조회했을 때 그 뒤에 공개된 정보가 섞이는가", "59,130개 중 0개", "시점 규칙이 지켜진다는 근거"],
  ["계열: 공정위 지정 명단(이듬해 5월)과 겹치는 정도", "정밀도 94.9% · 재현율 94.6%", "두 자료가 서로 맞는 정도 (시점이 다르다)"],
  ["지분: 가진 쪽 공시와 내준 쪽 공시의 지분율이 서로 맞는가", "1,939 / 2,307", "두 공시가 서로 맞는 정도"],
];

const EXAM: [string, number, string][] = [
  ["정답", 24, "#6fd08c"],
  ["부분 정답", 4, "#ffb454"],
  ["답하지 못함", 2, "#8d99ab"],
  ["틀린 사실을 답함", 0, "#ff6b6b"],
];

export function DataPage({ meta }: { meta: Meta }) {
  const relations = Object.entries(meta.relations).sort((a, b) => b[1] - a[1]);
  return (
    <div className="page">
      <h2>무엇이 들어 있나</h2>
      <p className="lead">
        금융감독원 전자공시(DART)에서 기업과 기업의 관계를 뽑아, 언제 성립했고 언제 공개됐고 언제 바뀌었는지를 같이 저장했습니다.
        상장사 2,759곳의 공시를 읽었고, 양식이 정해진 공시만 규칙으로 읽었습니다. 추출에 LLM은 쓰지 않았습니다.
      </p>
      <div className="tiles">
        <div className="tile">
          <b>{meta.companies.toLocaleString()}</b>
          <span>기업 (비상장 계열사 포함)</span>
        </div>
        <div className="tile">
          <b>{meta.documents.toLocaleString()}</b>
          <span>읽은 공시</span>
        </div>
        <div className="tile">
          <b>{Object.values(meta.relations).reduce((a, b) => a + b, 0).toLocaleString()}</b>
          <span>관계 줄</span>
        </div>
        <div className="tile">
          <b>{meta.last_date}</b>
          <span>가장 최근 공시일</span>
        </div>
      </div>
      <table className="grid">
        <thead>
          <tr>
            <th>관계</th>
            <th className="num">줄 수</th>
            <th>출처</th>
          </tr>
        </thead>
        <tbody>
          {relations.map(([label, count]) => (
            <tr key={label}>
              <td>{label}</td>
              <td className="num">{count.toLocaleString()}</td>
              <td className="muted">{sourceOf(meta, label)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>맞는지 어떻게 확인했나</h2>
      <p className="lead">
        수십만 줄을 사람이 다 볼 수 없고 정답지도 없어서, 공시 안에 적힌 숫자끼리 검산하고 다른 기관의 자료와 대조했습니다.
        아래 두 줄은 추출 정확도가 아니라 두 자료가 서로 맞는 정도입니다.
      </p>
      <table className="grid">
        <thead>
          <tr>
            <th>검사</th>
            <th className="num">결과</th>
            <th>뜻</th>
          </tr>
        </thead>
        <tbody>
          {CHECKS.map(([name, result, meaning]) => (
            <tr key={name}>
              <td>{name}</td>
              <td className="num">{result}</td>
              <td className="muted">{meaning}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Agent 평가</h2>
      <p className="lead">
        기업과 공시를 무작위로 뽑은 30문항을, 도구를 다 만든 뒤 한 번 돌렸습니다(claude-haiku-5-5). 정답은 추출기를 거치지 않은 원자료와
        Claude가 세 번 대조했고, 세 번째는 별개의 검토자에게 맡겼습니다. 사람이 원문을 직접 본 것은 아닙니다.
      </p>
      <div className="bar">
        {EXAM.filter(([, n]) => n > 0).map(([label, n, color]) => (
          <div key={label} style={{ flex: n, background: color }} title={`${label} ${n}`}>
            {n}
          </div>
        ))}
      </div>
      <div className="bar-legend">
        {EXAM.map(([label, n, color]) => (
          <span key={label}>
            <i className="dot" style={{ background: color }} />
            {label} {n}
          </span>
        ))}
      </div>
      <ul className="notes">
        <li>정답률 80% (95% 구간 63~91%). 30문항이라 구간이 넓습니다. 지어낸 접수번호는 0건입니다.</li>
        <li>문항당 7~9초, 약 23,000토큰. 웹 검색만 쓴 Claude는 같은 종류의 질문에 76초, 68,700토큰이 걸렸습니다.</li>
        <li>
          이 시험이 DB의 결함을 찾았습니다. 해지된 계약이 유효한 것으로 조회되던 문제(94줄), 이름을 고친 정정 공시가 이어지지 않던 문제 등입니다.
          고쳤지만 점수는 고치기 전 것으로 두었습니다.
        </li>
      </ul>

      <h2>없는 것</h2>
      <ul className="notes">
        <li>반기·분기보고서, 최대주주와 특수관계인이 아닌 주주, 주요 고객, 합병·분할, 뉴스, 주가</li>
        <li>2024년 1월 이전의 공급계약과 취득·처분 결정</li>
        <li>매수·매도 판단. 이 서비스는 공시에 적힌 사실만 보여 줍니다.</li>
      </ul>
      <p className="lead">
        설계 기록과 코드는 <a href={REPO}>GitHub</a>에 있습니다.
      </p>
    </div>
  );
}

function sourceOf(meta: Meta, label: string): string {
  const key: Record<string, string> = {
    지분: "equity",
    계열: "affiliate",
    공급계약: "supply_contract",
    "공급계약 해지": "supply_termination",
    "지분 취득 결정": "stake_acquisition",
    "지분 처분 결정": "stake_disposal",
  };
  return meta.coverage.sources[key[label]] ?? "";
}

interface Example {
  id: string;
  kind: string;
  why: string;
  question: string;
  as_of: string;
  answer: string;
  model: string;
  seconds: number;
  tokens: number;
  tools: { name: string; input: Record<string, unknown>; total: number | null; error: string | null }[];
}

/** 답에 나온 14자리 접수번호를 DART 원문 링크로 바꾼다. */
const linkReceipts = (text: string) => text.replace(/(?<![\d/=])(20\d{12})(?!\d)/g, `[$1](${DART}$1)`);

export function AgentPage() {
  const [examples, setExamples] = useState<Example[]>([]);
  const [selected, setSelected] = useState(0);
  useEffect(() => {
    fetch("/agent-examples.json")
      .then((r) => r.json())
      .then(setExamples)
      .catch(() => setExamples([]));
  }, []);
  const example = examples[selected];
  return (
    <div className="page">
      <h2>Agent 예시</h2>
      <p className="lead">
        평가 때 실제로 주고받은 질문과 답입니다. 지금은 미리 돌려 둔 결과를 보여 줍니다. Agent는 SQL을 쓰지 않고 정해진 조회 함수만 부르며,
        답에 적은 접수번호가 실제 조회 결과에 있었는지 끝에서 확인합니다. 잘한 것만 고르지 않고 답하지 못한 문항도 넣었습니다.
      </p>
      <div className="examples">
        <div className="example-list">
          {examples.map((e, i) => (
            <button key={e.id} className={i === selected ? "example on" : "example"} onClick={() => setSelected(i)}>
              <span>{e.why}</span>
              {e.question}
            </button>
          ))}
        </div>
        {example && (
          <div className="example-body">
            <div className="question">
              <span>
                {example.kind} {example.id} · 조회 시점 {example.as_of}
              </span>
              {example.question}
            </div>
            <div className="tools">
              {example.tools.map((tool, i) => (
                <div key={i} className="tool">
                  <b>{tool.name}</b>
                  <code>{JSON.stringify(tool.input)}</code>
                  {tool.error ? <em>{tool.error}</em> : tool.total !== null && <em>{tool.total}건</em>}
                </div>
              ))}
              {example.tools.length === 0 && <div className="tool muted">도구를 부르지 않고 답했습니다</div>}
            </div>
            <div className="answer">
              <Markdown remarkPlugins={[remarkGfm]} components={{ a: (props) => <a {...props} target="_blank" rel="noreferrer" /> }}>
                {linkReceipts(example.answer)}
              </Markdown>
            </div>
            <div className="meta">
              {example.model} · {example.seconds}초 · {example.tokens.toLocaleString()}토큰
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
