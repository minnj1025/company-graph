import { useState } from "react";
import { ByType, Compare, Questions, VerdictBar, VerdictLegend, useEval, wilson } from "./Eval";
import type { Meta } from "./types";

const REPO = "https://github.com/minnj1025/company-graph";

/** 검산 결과. 추출기를 다시 돌린 날(2026-10-08)의 값이며, 저장소 README와 같다. 가운데 두 칸은 맞은 수와 전체 수. */
const CHECKS: [string, number, number, string][] = [
  ["공급계약: 계약금액 ÷ 최근 매출액이 공시에 적힌 비율과 맞는가", 11739, 11853, "추출이 맞다는 근거"],
  ["취득·처분 결정: 금액 ÷ 자기자본이 적힌 비율과 맞는가", 2901, 2923, "추출이 맞다는 근거"],
  ["계열: 읽은 줄 수가 표에 적힌 회사 수와 같은가", 6301, 6438, "추출이 맞다는 근거"],
  ["과거 시점으로 조회했을 때 그 뒤에 공개된 정보가 섞이지 않는가", 59130, 59130, "시점 규칙이 지켜진다는 근거"],
  ["지분: 가진 쪽 공시와 내준 쪽 공시의 지분율이 서로 맞는가", 1939, 2307, "두 공시가 서로 맞는 정도 (추출 정확도가 아니다)"],
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
      <div className="rels">
        {relations.map(([label, count]) => (
          <div key={label} className="rel">
            <span>{label}</span>
            <div className="meter">
              <div style={{ width: `${Math.max(Math.sqrt(count / relations[0][1]) * 100, 1)}%` }} />
            </div>
            <b>{count.toLocaleString()}</b>
            <em>{sourceOf(meta, label)}</em>
          </div>
        ))}
        <p className="muted small">막대 길이는 줄 수의 제곱근에 비례합니다. 가장 많은 것과 적은 것이 700배 차이라 그대로 그리면 작은 것이 보이지 않습니다.</p>
      </div>

      <h2>맞는지 어떻게 확인했나</h2>
      <p className="lead">
        수십만 줄을 사람이 다 볼 수 없고 정답지도 없어서, 공시 안에 적힌 숫자끼리 검산하고 다른 기관의 자료와 대조했습니다.
      </p>
      <div className="checks">
        {CHECKS.map(([name, hit, total, meaning]) => (
          <div key={name} className="check">
            <div className="ring" style={{ background: `conic-gradient(#6fd08c ${(hit / total) * 360}deg, rgba(255,255,255,0.08) 0)` }}>
              <b>{((hit / total) * 100).toFixed(hit === total ? 0 : 1)}%</b>
            </div>
            <div>
              <p>{name}</p>
              <span>
                {hit.toLocaleString()} / {total.toLocaleString()} · {meaning}
              </span>
            </div>
          </div>
        ))}
        <div className="check">
          <div className="ring" style={{ background: "conic-gradient(#7aa2ff 341deg, rgba(255,255,255,0.08) 0)" }}>
            <b>94.9%</b>
          </div>
          <div>
            <p>계열: 공정거래위원회 지정 명단과 겹치는 정도</p>
            <span>정밀도 94.9% · 재현율 94.6% · 두 자료의 기준 시점이 달라 100%가 될 수 없다</span>
          </div>
        </div>
      </div>

      <EvalSection />

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

/** Agent 평가: 149문항 결과, 웹 검색만 쓴 Claude와의 비교, 문항 열어보기. */
function EvalSection() {
  const data = useEval();
  const [open, setOpen] = useState(false);
  if (!data) return null;
  const sides = data.items.map((item) => item.agent);
  const correct = sides.filter((s) => s.verdict === "correct").length;
  const partial = sides.filter((s) => s.verdict === "partial").length;
  const [low, high] = wilson(correct + partial, sides.length);
  const pct = (n: number, of: number) => Math.round((n / of) * 100);
  const hit = (list: { verdict: string }[]) => list.filter((s) => s.verdict === "correct" || s.verdict === "partial").length;
  const shared = data.items.filter((item) => item.baseline);
  const invented = data.items.filter((item) => item.agent.unverified.length > 0).length;
  return (
    <>
      <h2>Agent는 얼마나 맞게 답하나</h2>
      <p className="lead">
        공시 원문에서 직접 만든 {sides.length}문항을, 도구를 다 만든 뒤 한 번 돌렸습니다({data.agent_model}). 한 칸을 읽으면 끝나는 질문은 빼고,
        정정 사이의 시점을 가리거나 여러 공시를 견주어야 답할 수 있는 질문만 썼습니다. 문항과 정답은 추출기를 거치지 않은 원자료만 보고 썼고,
        쓴 쪽과 다른 검토자가 다시 확인했고, 채점은 어느 쪽 답인지 모르는 채점자가 했습니다. 문항 작성, 검토, 채점은 모두 Claude가 했고 사람이 원문을 직접 본 것은 아닙니다.
      </p>
      <VerdictBar sides={sides} />
      <VerdictLegend sides={sides} />
      <ul className="notes">
        <li>
          정답이거나 부분 정답인 문항이 {pct(correct + partial, sides.length)}%입니다(95% 구간 {low}~{high}%). 이 가운데 물은 값을 모두 맞힌 정답이{" "}
          {pct(correct, sides.length)}%, 일부만 맞힌 부분 정답이 {pct(partial, sides.length)}%입니다. 조회 결과에 없는 접수번호를 답에 적은 문항은 {invented}개입니다.
        </li>
        <li>
          부분 정답 55개의 대부분은 값이 틀린 것이 아니라 물은 값 가운데 하나를 답하지 못한 것입니다. 자기자본 대비 비율(10문항)과 취득 예정일자(7문항)처럼
          DB에는 있는데 조회 도구가 내주지 않은 값이 가장 많았습니다.
        </li>
        <li>
          틀린 사실을 답한 10문항은 정정 공시의 머리 표와 본문 값이 다른 경우, 해지 공시를 다른 계약에 이은 경우 등 DB 쪽 결함이 원인이었습니다.
          점수는 고치기 전 것으로 두었습니다. 문항마다 판정 사유를 아래에서 볼 수 있습니다.
        </li>
      </ul>
      <ByType data={data} />

      <h2>웹 검색만 쓰는 Claude와 견주면</h2>
      <p className="lead">
        같은 문항 {shared.length}개(종류마다 앞의 두 문항)를 이 DB 없이 웹 검색과 페이지 읽기만 쓸 수 있는 Claude({data.baseline_model})에게 풀게 했습니다.
        Agent는 가장 작은 모델({data.agent_model})이고, 견준 쪽은 그보다 훨씬 큰 모델입니다. 정답이거나 부분 정답인 문항은 Agent {pct(hit(shared.map((i) => i.agent)), shared.length)}%, 웹 검색{" "}
        {pct(hit(shared.map((i) => i.baseline!)), shared.length)}%입니다. {shared.length}문항이라 이 차이는 크게만 읽어야 하고, 시간과 토큰의 차이는 분명합니다.
      </p>
      <Compare data={data} />
      <ul className="notes">
        <li>
          웹 검색이 더 잘한 문항도 있습니다. 널리 보도된 공시는 기사에 값이 실려 있어서, Agent가 도구에서 받지 못한 값(예정일자, 자기자본 대비 비율)을
          웹 검색이 맞힌 문항이 6개입니다.
        </li>
        <li>웹 검색이 틀린 6문항은 조회 시점 뒤의 정정을 놓치거나, 기사에 없는 값을 추정하거나, 여러 건 가운데 일부만 찾은 경우였습니다.</li>
      </ul>

      <h2>평가 문항</h2>
      <p className="lead">문항, 정답, Agent가 부른 도구와 답, 판정 사유를 모두 볼 수 있습니다. 틀린 문항도 그대로 두었습니다.</p>
      <button className="reveal" onClick={() => setOpen(!open)}>
        {open ? "접기" : `${sides.length}문항 열어보기`}
      </button>
      {open && <Questions data={data} />}
    </>
  );
}

export function QuestionsPage() {
  const data = useEval();
  return (
    <div className="page">
      <h2>평가 문항</h2>
      <p className="lead">
        Agent 평가에 쓴 문항 전부입니다. 줄을 누르면 정답, 근거 공시, Agent가 부른 도구와 답, 판정 사유가 열립니다. 종류마다 앞의 두 문항에는 웹 검색만 쓴
        Claude의 답도 있습니다.
      </p>
      {data ? <Questions data={data} /> : <p className="muted">불러오는 중…</p>}
    </div>
  );
}
