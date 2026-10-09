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

/** 데이터 출처. 모든 화면의 맨 아래에 둔다. */
export function Credit() {
  return (
    <footer className="credit">
      <b>데이터 출처</b> 금융감독원 전자공시시스템(
      <a href="https://dart.fss.or.kr" target="_blank" rel="noreferrer">
        DART
      </a>
      )과{" "}
      <a href="https://opendart.fss.or.kr" target="_blank" rel="noreferrer">
        OpenDART
      </a>{" "}
      API의 공시 원문 · 공정거래위원회의 대규모기업집단 소속회사 현황(
      <a href="https://www.data.go.kr" target="_blank" rel="noreferrer">
        공공데이터포털
      </a>
      ). 공시에 적힌 사실을 정리한 것이며 투자 권유가 아닙니다.
    </footer>
  );
}

export function DataPage({ meta }: { meta: Meta }) {
  const relations = Object.entries(meta.relations).sort((a, b) => b[1] - a[1]);
  return (
    <div className="page">
      <h2>무엇이 들어 있나</h2>
      <p className="lead">
        금융감독원 전자공시(DART)에서 기업과 기업의 관계를 뽑아, 언제 성립했고 언제 공개됐고 언제 바뀌었는지를 같이 저장했습니다.
        상장사 2,759곳의 공시를 읽었고, 양식이 정해진 공시만 규칙으로 읽었습니다. 관계 추출에 LLM은 쓰지 않았습니다. 관계 말고 기업이 무엇을
        하는지도 담았습니다.
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

      <h2>사업 내용과 제품</h2>
      <p className="lead">
        관계 표만으로는 "이 이슈와 닿는 회사"를 찾을 수 없어서, 사업보고서와 반기보고서의 "사업의 내용"을 두 층으로 담았습니다.
      </p>
      <div className="tiles">
        <div className="tile">
          <b>{meta.business.sections.toLocaleString()}</b>
          <span>사업 내용의 절 (보고서 {meta.business.reports.toLocaleString()}건)</span>
        </div>
        <div className="tile">
          <b>{meta.business.companies.toLocaleString()}</b>
          <span>사업 내용이 있는 기업</span>
        </div>
        <div className="tile">
          <b>{meta.business.companies_with_products.toLocaleString()}</b>
          <span>제품 표를 읽은 기업</span>
        </div>
        <div className="tile">
          <b>{(meta.business.companies_with_product_section - meta.business.companies_with_products).toLocaleString()}</b>
          <span>제품 표를 읽지 못한 기업</span>
        </div>
      </div>
      <ul className="notes">
        <li>
          <b>사업 내용</b>은 소제목 단위로 자르기만 하고 요약하거나 값을 뽑지 않습니다. "라면 만드는 회사는?" 같은 질문이 오면 Agent가 낱말로 찾고, 매출
          비중 표를 읽어 그 사업이 주력인 회사와 언급만 있는 회사를 나눠 답합니다. 수혜를 볼지, 주가가 오를지는 말하지 않습니다.
        </li>
        <li>
          <b>제품</b>은 "주요 제품 및 서비스"의 매출 비중 표에서 읽었습니다. 비중의 합이 100에 가까운 표만 믿습니다. 제품 절이 있는{" "}
          {meta.business.companies_with_product_section.toLocaleString()}곳 가운데 {meta.business.companies_with_products.toLocaleString()}곳을 읽었고,
          읽지 못한 회사는 기업 상세에 그렇게 표시합니다.
        </li>
        <li>
          같은 제품을 회사마다 다르게 적습니다("분리막", "LiBS", "2차전지 분리막"). 표에 적힌 이름은 그대로 두고, 여러 회사를 묶는 이름을 분야, 제품군,
          제품의 세 층으로 따로 붙였습니다. 제품군은 "같은 제품군의 회사끼리 경쟁하거나 대체할 수 있는가"로 나눈 닫힌 목록이라, 반도체를 만드는 회사와
          반도체 장비 회사는 다른 제품군입니다.
        </li>
        <li>
          이름은 줄 하나가 아니라 회사의 표 전체와 사업 개요를 같이 보고 Claude Opus가 붙였습니다. 사람이 전부 확인한 것은 아니어서, 기업 상세에는 표의
          이름과 붙인 이름을 나란히 보여 주고 짐작이 섞인 줄은 그렇게 표시합니다. 무작위 100줄을 다시 본 결과와 방법은 저장소 README에 적었습니다.
        </li>
        <li>
          탐색 화면의 보기 설정에서 "제품"을 켜면 기업, 제품, 제품군이 차례로 이어져 나타나고, 선의 굵기는 그 제품이 매출에서 차지하는 비중입니다.
        </li>
        <li>이슈형 질문의 답이 얼마나 맞는지는 아직 재지 않았습니다. 아래 평가는 모두 관계를 묻는 질문입니다.</li>
      </ul>

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
        <li>분기보고서, 반기보고서의 지분·계열 표(반기보고서는 사업 내용만 읽었습니다), 최대주주와 특수관계인이 아닌 주주, 주요 고객, 합병·분할, 뉴스, 주가</li>
        <li>2024년 1월 이전의 공급계약과 취득·처분 결정</li>
        <li>매수·매도 판단. 이 서비스는 공시에 적힌 사실만 보여 줍니다.</li>
      </ul>
      <p className="lead">
        설계 기록과 코드는 <a href={REPO}>GitHub</a>에 있습니다.
      </p>
      <Credit />
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
  // "2024-01" 같은 날짜가 줄 끝에서 "2024-" 와 "01" 로 갈리지 않게, 숫자 사이의 붙임표를 줄이 바뀌지 않는 것으로 바꾼다
  return (meta.coverage.sources[key[label]] ?? "").replace(/(\d)-(?=\d)/g, "$1\u2011");
}

/** Agent 평가: 149문항 결과, 웹 검색만 쓴 Claude와의 비교, 문항 열어보기. */
function EvalSection() {
  const data = useEval();
  const [open, setOpen] = useState(false);
  if (!data) return null;
  const before = data.items.map((item) => item.agent);
  const sides = data.items.map((item) => item.after);
  const pct = (n: number, of: number) => Math.round((n / of) * 100);
  const tally = (list: { verdict: string }[], ...verdicts: string[]) => list.filter((s) => verdicts.includes(s.verdict)).length;
  const correct = tally(sides, "correct");
  const hit = tally(sides, "correct", "partial");
  const [low, high] = wilson(correct, sides.length);
  const shared = data.items.filter((item) => item.baseline);
  const invented = data.items.filter((item) => item.agent.unverified.length + item.after.unverified.length > 0).length;
  return (
    <>
      <h2>Agent는 얼마나 맞게 답하나</h2>
      <p className="lead">
        공시 원문에서 직접 만든 {sides.length}문항으로 쟀습니다({data.agent_model}). 한 칸을 읽으면 끝나는 질문은 빼고, 정정 사이의 시점을 가리거나
        여러 공시를 견주어야 답할 수 있는 질문만 썼습니다. 문항과 정답은 추출기를 거치지 않은 원자료만 보고 썼고, 쓴 쪽과 다른 검토자가 다시 확인했고,
        채점은 어느 쪽 답인지 모르는 채점자가 했습니다. 문항 작성, 검토, 채점은 모두 Claude가 했고 사람이 원문을 직접 본 것은 아닙니다.
      </p>
      <div className="stages">
        <div>
          <h4>
            처음 돌렸을 때{" "}
            <span>
              정답 {pct(tally(before, "correct"), before.length)}% · 부분 정답까지 {pct(tally(before, "correct", "partial"), before.length)}%
            </span>
          </h4>
          <VerdictBar sides={before} />
        </div>
        <div>
          <h4>
            시험이 찾은 결함을 고친 뒤{" "}
            <span>
              정답 {pct(correct, sides.length)}% · 부분 정답까지 {pct(hit, sides.length)}%
            </span>
          </h4>
          <VerdictBar sides={sides} />
        </div>
      </div>
      <VerdictLegend />
      <ul className="notes">
        <li>
          고친 뒤 정답 {pct(correct, sides.length)}% (95% 구간 {low}~{high}%), 부분 정답까지 넣으면 {pct(hit, sides.length)}%입니다. 조회 결과에 없는
          접수번호를 답에 적은 문항은 두 번 모두 {invented}개입니다.
        </li>
        <li>
          처음에는 부분 정답이 55개였습니다. 대부분 DB에는 있는데 조회 도구가 내주지 않은 값(자기자본 대비 비율, 취득 예정일자, 해지 사유) 때문이었고,
          도구가 그 값을 내주게 고치자 37개가 정답이 됐습니다.
        </li>
        <li>
          고친 뒤의 점수는 문항을 알고 고친 것이라 새 문항에서도 같으리라는 보장이 없습니다. 그래서 처음 점수를 지우지 않고 나란히 둡니다. 틀린 사실을 답한
          문항은 고친 뒤에도 {tally(sides, "wrong")}개입니다(처음 틀린 10개 중 6개는 고쳐졌고, 숫자 자릿수를 잘못 옮기는 등 다른 6개가 새로 틀렸습니다).
        </li>
      </ul>
      <ByType data={data} />

      <h2>웹 검색만 쓰는 Claude와 견주면</h2>
      <p className="lead">
        같은 문항 {shared.length}개를 이 DB 없이 웹 검색과 페이지 읽기만 쓸 수 있는 Claude({data.baseline_model})에게 풀게 했습니다.
        Agent는 가장 작은 모델({data.agent_model})이고, 견준 쪽은 그보다 훨씬 큰 모델입니다. 아래는 Agent를 처음 돌렸을 때의 답으로 견준 것입니다. 정답이거나 부분 정답인 문항은 Agent{" "}
        {pct(tally(shared.map((i) => i.agent), "correct", "partial"), shared.length)}%, 웹 검색{" "}
        {pct(tally(shared.map((i) => i.baseline!), "correct", "partial"), shared.length)}%입니다. 결함을 고친 뒤의 Agent는 같은 {shared.length}문항에서 정답이{" "}
        {tally(shared.map((i) => i.after), "correct")}개입니다. 문항은 종류마다 5~7개씩 고르게 뽑았습니다. 시간과 토큰의 차이는 분명합니다.
      </p>
      <Compare data={data} />
      <ul className="notes">
        <li>
          웹 검색이 더 잘한 문항도 있습니다. 널리 보도된 공시는 기사에 값이 실려 있어서, 처음 돌린 Agent가 도구에서 받지 못한 값(예정일자, 자기자본 대비
          비율 등)을 웹 검색이 맞힌 문항이 14개입니다. 그 값을 내주게 고친 뒤에는 대부분 Agent도 맞혔습니다.
        </li>
        <li>웹 검색이 틀린 15문항은 조회 시점 뒤의 정정을 놓치거나, 기사에 없는 값을 추정하거나, 여러 건 가운데 일부만 찾은 경우였습니다.</li>
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
      <Credit />
    </div>
  );
}
