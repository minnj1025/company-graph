import { useEffect, useState } from "react";
import { fetchTaxonomy } from "./api";
import { ByType, Questions, VerdictBar, VerdictLegend, useEval, wilson } from "./Eval";
import { Credit } from "./Credit";
import type { Meta, Taxonomy } from "./types";

const REPO = "https://github.com/minnj1025/company-graph";

/** 검산 결과. 추출기를 다시 돌린 날(2026-10-08)의 값이며, 저장소 README와 같다. 가운데 두 칸은 맞은 수와 전체 수. */
const CHECKS: [string, number, number, string][] = [
  ["공급계약: 계약금액 ÷ 최근 매출액과 공시 기재 비율의 일치", 11739, 11853, "추출 정확도"],
  ["취득·처분 결정: 금액 ÷ 자기자본과 공시 기재 비율의 일치", 2901, 2923, "추출 정확도"],
  ["계열: 추출한 줄 수와 표에 기재된 회사 수의 일치", 6301, 6438, "추출 정확도"],
  ["시점 조회: 조회 시점 이후에 공개된 정보의 혼입 여부", 59130, 59130, "시점 규칙 준수"],
  ["지분: 보유 측 공시와 피보유 측 공시의 지분율 일치", 1939, 2307, "두 공시 간 정합성 (추출 정확도와는 다른 지표)"],
];

/** 이슈 종목의 묶음 수를, 등락률을 회사끼리 섞었을 때의 수와 견준 것 (hot.py check). 실제 / 섞었을 때 */
const HOT_CHECKS: [string, string, string][] = [
  ["연관 급등", "317건 / 무작위 22건 · 14.2배", "274건 / 무작위 13건 · 21.1배"],
  ["연관 급락", "152건 / 무작위 12건 · 13.2배", "99건 / 무작위 4건 · 25.5배"],
  ["약한 신호 (급등)", "584건 / 무작위 152건 · 3.8배", "376건 / 무작위 82건 · 4.6배"],
  ["일평균 연관 급등", "1.9건 · 미발생 32일", "1.2건 · 미발생 82일"],
];

/** 평가 3판(eval/v3)의 가장 최근 결과: 정답, 부분 정답, 오답 */
const V3: [string, number, number, number][] = [
  ["제품 (10문항)", 7, 3, 0],
  ["이슈 (3문항)", 2, 1, 0],
  ["회사 (6문항)", 6, 0, 0],
  ["이어지는 대화 (10쌍)", 10, 0, 0],
];

/** 긴 페이지 왼쪽의 목차. 지금 읽는 구역에 표시가 따라 내려온다. 작은 제목(sub)은 들여 쓴다 */
const TOC: { id: string; label: string; sub?: boolean }[] = [
  { id: "what", label: "데이터 구성" },
  { id: "relations", label: "1. 관계 추출" },
  { id: "products", label: "2. 제품 판독·분류" },
  { id: "taxonomy", label: "제품 분류 체계", sub: true },
  { id: "hot", label: "3. 이슈 종목 탐지" },
  { id: "news", label: "4. 기사 요약" },
  { id: "agent", label: "5. Agent 답변" },
  { id: "followups", label: "제품·연속 질문", sub: true },
  { id: "limits", label: "미포함 범위와 한계" },
];

function Toc() {
  const [active, setActive] = useState(TOC[0].id);
  const [present, setPresent] = useState<string[]>([]);
  useEffect(() => {
    const root = document.querySelector(".overlay");
    if (!root) return;
    // 화면 위쪽을 지나간 마지막 제목이 지금 읽는 구역이다. 늦게 불러오는 구역(분류, 평가)이 생기면 목차에 더한다
    const where = () => {
      const found = TOC.map((item) => document.getElementById(item.id)).filter((el): el is HTMLElement => Boolean(el));
      const line = root.getBoundingClientRect().top + 120;
      const passed = found.filter((el) => el.getBoundingClientRect().top <= line);
      const bottom = root.scrollTop + root.clientHeight >= root.scrollHeight - 4;   // 맨 아래에 닿으면 마지막 구역
      setActive((bottom ? found[found.length - 1] : passed[passed.length - 1] ?? found[0])?.id ?? TOC[0].id);
      setPresent((old) => (old.length === found.length ? old : found.map((el) => el.id)));
    };
    where();
    root.addEventListener("scroll", where, { passive: true });
    const changes = new MutationObserver(where);
    changes.observe(root, { childList: true, subtree: true });
    return () => {
      root.removeEventListener("scroll", where);
      changes.disconnect();
    };
  }, []);
  return (
    <nav className="toc" aria-label="이 페이지">
      <p>이 페이지</p>
      <ul>
        {TOC.filter((item) => present.includes(item.id)).map((item) => (
          <li key={item.id} className={`${item.sub ? "sub" : ""} ${active === item.id ? "on" : ""}`}>
            <a
              href={`#${item.id}`}
              onClick={(event) => {
                event.preventDefault();
                document.getElementById(item.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
              }}
            >
              {item.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function DataPage({ meta }: { meta: Meta }) {
  const relations = Object.entries(meta.relations).sort((a, b) => b[1] - a[1]);
  return (
    <div className="with-toc">
      <Toc />
      <div className="page">
      <h2 id="what">데이터 구성</h2>
      <p className="lead">
        금융감독원 전자공시(DART)에서 기업 간 관계를 추출하고, 관계마다 성립일·공시일·변경일을 함께 기록했습니다. 대상은 상장사 2,759곳이며, 관계는
        서식이 정해진 공시만 규칙으로 판독했습니다(LLM 미사용). 여기에 사업 내용과 제품 구성, 한국거래소 일별 시세로 계산한 이슈 종목을 더했습니다. 아래는
        데이터가 만들어지는 순서에 따른 단계별 검증 결과입니다.
      </p>
      <div className="tiles">
        <div className="tile">
          <b>{meta.companies.toLocaleString()}</b>
          <span>기업 (비상장 계열사 포함)</span>
        </div>
        <div className="tile">
          <b>{meta.documents.toLocaleString()}</b>
          <span>수집 공시</span>
        </div>
        <div className="tile">
          <b>{Object.values(meta.relations).reduce((a, b) => a + b, 0).toLocaleString()}</b>
          <span>관계 레코드</span>
        </div>
        <div className="tile">
          <b>{meta.last_date}</b>
          <span>최근 공시일</span>
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
        <p className="muted small">막대 길이는 건수의 제곱근에 비례합니다. 최대와 최소가 700배 차이여서 선형 축척으로는 작은 항목이 보이지 않습니다.</p>
      </div>

      <h2 id="relations">1. 관계 추출 정확도</h2>
      <p className="lead">
        지분, 계열, 공급계약, 취득·처분 결정을 정형 공시에서 규칙으로 추출했습니다. 수십만 건을 전수 검토할 수 없고 정답지도 없으므로, 공시 내부의 수치
        검산과 외부 기관 자료 대조로 정확도를 추정했습니다.
      </p>
      <div className="checks">
        {CHECKS.map(([name, hit, total, meaning]) => (
          <div key={name} className="check">
            <b className="pct">{((hit / total) * 100).toFixed(hit === total ? 0 : 1)}%</b>
            <div>
              <p>{name}</p>
              <span>
                {hit.toLocaleString()} / {total.toLocaleString()} · {meaning}
              </span>
            </div>
          </div>
        ))}
        <div className="check">
          <b className="pct">94.9%</b>
          <div>
            <p>계열: 공정거래위원회 지정 명단과의 일치</p>
            <span>정밀도 94.9% · 재현율 94.6% · 두 자료의 기준 시점이 달라 100%에 이를 수 없음</span>
          </div>
        </div>
      </div>

      <h2 id="products">2. 제품 표 판독과 분류 정확도</h2>
      <p className="lead">
        관계만으로는 특정 이슈와 관련된 기업을 찾을 수 없어, 사업보고서·반기보고서의 "사업의 내용"을 원문 절과 제품 표의 두 층으로 구축했습니다. 이후
        단계인 이슈 종목 탐지와 Agent 답변이 모두 이 제품 분류에 의존합니다.
      </p>
      <div className="tiles">
        <div className="tile">
          <b>{meta.business.sections.toLocaleString()}</b>
          <span>사업 내용 절 (보고서 {meta.business.reports.toLocaleString()}건)</span>
        </div>
        <div className="tile">
          <b>{meta.business.companies.toLocaleString()}</b>
          <span>사업 내용 보유 기업</span>
        </div>
        <div className="tile">
          <b>{meta.business.companies_with_products.toLocaleString()}</b>
          <span>제품 표 판독 기업</span>
        </div>
        <div className="tile">
          <b>{(meta.business.companies_with_product_section - meta.business.companies_with_products).toLocaleString()}</b>
          <span>제품 표 미판독 기업</span>
        </div>
      </div>
      <details className="more">
        <summary>구축 방식</summary>
        <ul className="notes">
          <li>
            <b>사업 내용</b> 소제목 단위로 분할만 하고 요약하거나 값을 추출하지 않습니다. Agent가 키워드로 검색한 뒤 매출 비중 표를 확인해, 해당 사업이
            주력인 기업과 언급에 그치는 기업을 구분합니다.
          </li>
          <li>
            <b>제품 표</b> "주요 제품 및 서비스"의 매출 비중 표에서 판독했으며, 비중 합계가 100에 가까운 표만 채택했습니다. 제품 절이 있는{" "}
            {meta.business.companies_with_product_section.toLocaleString()}곳 중 {meta.business.companies_with_products.toLocaleString()}곳을 판독했고,
            미판독 기업은 기업 상세에 표시합니다.
          </li>
          <li>
            <b>표준 이름</b> 같은 제품도 기업마다 표기가 다릅니다("분리막", "LiBS", "2차전지 분리막"). 원문 표기는 유지하고, 기업 간 비교를 위한 표준 이름을
            분야·제품군·제품의 3단계로 부여했습니다. 제품군은 "서로 경쟁하거나 대체 가능한가"를 기준으로 한 폐쇄형 목록이어서, 반도체 제조사와 반도체 장비사는
            서로 다른 제품군에 속합니다.
          </li>
          <li>
            <b>부여 방식</b> 개별 줄이 아니라 기업의 표 전체와 사업 개요를 함께 보고 Claude Opus가 부여했습니다. 전수 검수는 하지 않았으므로 기업 상세에
            원문 표기와 표준 이름을 병기하고, 추정이 포함된 줄은 별도로 표시합니다.
          </li>
        </ul>
      </details>
      <div className="checks">
        <div className="check">
          <b className="pct">94.5%</b>
          <div>
            <p>표준 이름·제품군 부여의 적정성 (무작위 200줄)</p>
            <span>적정 189 · 제품군 불일치 6 · 이름 불일치 2 · 판단 불가 3 · 다른 사업으로 분류 0</span>
          </div>
        </div>
        <div className="check">
          <b className="pct">45/48</b>
          <div>
            <p>규칙으로 판독하지 못해 모델이 옮긴 표의 원문 일치 (무작위 48곳)</p>
            <span>일치 45 · 경미한 차이 2 · 불일치 0 · 판단 불가 1</span>
          </div>
        </div>
      </div>
      <details className="more">
        <summary>검증 방법과 한계</summary>
        <ul className="notes">
          <li>
            <b>검수 주체</b> 이름을 부여한 쪽과 분리된 검토자(Claude)가 수행했습니다. 원문 보고서가 아니라 표의 표기와 기업에 대한 지식으로 판단했으며,
            사람이 검토한 것은 아닙니다.
          </li>
          <li>
            <b>추정 표시의 유효성</b> 추정 표시가 있는 50줄은 84%, 없는 150줄은 98%가 적정이었습니다. 불확실 표시가 실제로 정확도가 낮은 줄을 가려냅니다.
          </li>
          <li>
            <b>불일치 유형</b> 경계가 모호한 사례였습니다(증권사 자기매매 → "자산운용·투자", 위탁생산 연질캡슐 → "일반의약품"). 200줄 중 다른 사업으로
            분류된 사례가 없었다는 것은 드물다는 뜻이며, 없다는 보장은 아닙니다. 이름이 부여되지 않은 줄의 누락 여부는 검토하지 않았습니다.
          </li>
        </ul>
      </details>

      <TaxonomySection />

      <h2 id="hot">3. 이슈 종목 탐지 검증</h2>
      <p className="lead">
        이슈 종목은 사전에 정의한 테마 없이, 같은 날 급등·급락한 종목 중 동일 제품을 판매하거나 지분·계열·공급계약으로 연결된 종목을 하나의 종목군으로
        묶습니다. 이 묶음이 우연의 산물이 아닌지 확인하기 위해, 등락률 분포는 유지한 채 기업에만 무작위로 재배정한 결과와 비교했습니다.
      </p>
      <table className="grid">
        <thead>
          <tr>
            <th />
            <th className="num">규칙 설계 기간 (2026년, 164거래일)</th>
            <th className="num">미사용 검증 기간 (2025년, 222거래일)</th>
          </tr>
        </thead>
        <tbody>
          {HOT_CHECKS.map(([name, now, held]) => (
            <tr key={name}>
              <td>{name}</td>
              <td className="num">{now}</td>
              <td className="num">{held}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <details className="more">
        <summary>검증 방법과 한계</summary>
        <ul className="notes">
          <li>
            <b>규칙과 검증 기간</b> 탐지 규칙(3종목 이상, 세 번째로 크게 움직인 종목이 시장 대비 5%p 이상, 동일 연결 고리를 가진 종목의 절반 이상)은 2026년
            시세로 설계했습니다. 따라서 2026년 수치는 설계에 쓰인 데이터의 결과이고, 2025년이 미사용 기간의 결과입니다. 단, 2025년 주가에 2026년 보고서의
            제품 표를 적용했습니다.
          </li>
          <li>
            <b>신호 등급</b> 2종목 묶음은 무작위 결과와 차이가 없어(1.3배) 제외했습니다. 3종목뿐이거나 일부만 움직인 묶음은 "약한 신호"로 분리했으며, 약 4건
            중 1건은 우연으로 추정됩니다.
          </li>
          <li>
            <b>기사 대조(급등)</b> 무작위로 고른 거래일의 연관 급등 14건을 기사와 대조했습니다(Claude의 웹 검색). 해당 종목 또는 업종의 상승을 다룬 기사 확인
            11건, 개별 종목 사유 1건, 미확인 2건.
          </li>
          <li>
            <b>기사 대조(급락)</b> 급등보다 약합니다. 20건 중 종목군 또는 업종의 하락을 다룬 기사 확인 8건, 단일 종목 기사이거나 시장 전체 급락 기사뿐인 경우
            7건, 미확인 5건. 시장 전체가 크게 하락한 날에는 동반 하락이라기보다 전반적 하락이 종목군으로 포착됩니다.
          </li>
          <li>
            <b>연결 고리의 의미</b> 종목들의 공통점이며 주가 변동의 원인이 아닙니다. 예를 들어 원전 건설 기대로 상승한 건설주가 "주택 건설"로 묶입니다.
          </li>
        </ul>
      </details>

      <h2 id="news">4. 기사 요약 신뢰도</h2>
      <p className="lead">
        공시에는 당일의 사건이 담기지 않으므로, 강한 신호의 연관 급등·급락에 한해 Claude Haiku가 웹을 검색해 당일 기사를 찾고 한 줄로 요약합니다. 하루
        3~4건 수준이어서 비용이 방문자 수와 무관하게 일정합니다.
      </p>
      <div className="tiles">
        <div className="tile">
          <b>12 / 14</b>
          <span>기사 탐색 성공 (기사가 확인된 종목군 기준)</span>
        </div>
        <div className="tile">
          <b>17 / 17</b>
          <span>인용 기사의 날짜 일치</span>
        </div>
        <div className="tile">
          <b>11 / 17</b>
          <span>요약과 기사 내용의 일치 (세부 불일치 4, 불일치 2)</span>
        </div>
      </div>
      <details className="more">
        <summary>검증 방법과 한계</summary>
        <ul className="notes">
          <li>
            <b>생성 오류 방지</b> 인용한 기사 주소가 실제 검색 결과에 있어야 하고, 기사가 해당 거래일 전후의 것이어야 합니다. 둘 중 하나라도 어긋나면 요약을
            폐기하고 "기사 미확인"으로 처리합니다. 기사 본문은 저장하지 않으며 요약 한 줄과 제목, 주소만 보관합니다.
          </li>
          <li>
            <b>적용 범위</b> 2026년 9월 1일~10월 8일의 61건 중 종목군 기사 확인 33건(54%), 단일 종목 기사 6건, 미확인 22건. 미확인의 다수는 지분·계열로만
            연결된 종목군입니다. 기사는 "반도체주 강세"로 쓰고 "특정 기업과 그 출자사"로 쓰지 않기 때문입니다.
          </li>
          <li>
            <b>탐색 성능</b> 사전에 기사 유무를 확인해 둔 23건 중, 기사가 있던 14건에서 12건을 찾았습니다. 최초 측정은 9건이었으나 응답이 중간에 끊겨
            "미확인"으로 기록되던 결함이 원인이었고, 수정 후 재측정했습니다.
          </li>
          <li>
            <b>요약 정확도</b> 기사를 찾았다고 응답한 17건을 분리된 검토자(Claude)가 인용 기사를 열어 대조했습니다. 날짜는 17건 모두 일치했고, 요약은 일치
            11건, 대의는 맞으나 기사에 없는 세부 포함 4건, 불일치 2건이었습니다.
          </li>
          <li>
            <b>불일치 유형과 조치</b> 기사에 없는 사유를 덧붙인 경우였습니다(기사는 "선박 수주 기대", 요약은 "캐나다 잠수함 수주 기대"). 주소와 날짜는 코드로
            검증할 수 있으나 요약 내용은 그렇지 않습니다. 이후 "기사에 있는 내용만 쓰고, 기사에 없는 종목은 언급하지 않는다"는 지시를 추가했으며 재측정은 하지
            않았습니다. 이에 따라 화면에는 요약 대신 기사 제목을 우선 표시하고, 요약은 펼쳤을 때 "자동 요약"으로 구분해 보여 줍니다.
          </li>
          <li>
            <b>검색 엔진</b> 네이버 뉴스가 아닌 Claude의 웹 검색을 사용하며 날짜 필터가 정밀하지 않습니다. 화면의 "당일 기사 검색"에서는 찾을 수 있는 기사를
            놓치는 경우가 있습니다.
          </li>
        </ul>
      </details>

      <EvalSection />

      <h2 id="limits">미포함 범위와 한계</h2>
      <ul className="notes">
        <li>
          <b>공시 범위</b> 분기보고서, 반기보고서의 지분·계열 표(반기보고서는 사업 내용만 수집), 최대주주·특수관계인 외의 주주, 주요 고객, 합병·분할, 2024년
          1월 이전의 공급계약과 취득·처분 결정
        </li>
        <li>
          <b>시세</b> 장중 시세는 제공하지 않습니다. 한국거래소(KRX) 통계정보의 일별 값을 가공하며 거래일 다음 영업일 이후에 갱신됩니다. 종목별 시세는
          외부에 제공하지 않고 이슈 종목 계산에만 사용합니다.
        </li>
        <li>
          <b>개별 종목의 사유</b> 실적 발표, 거래 재개 등의 공시는 수집하지 않아 개별 급등·급락 종목에 연결되는 공시가 적습니다. 뉴스 본문도 저장하지
          않습니다.
        </li>
        <li>
          <b>투자 판단</b> 매수·매도 의견을 제공하지 않습니다. 공시에 기재된 사실과 과거의 주가 움직임만 보여 줍니다.
        </li>
      </ul>
      <p className="lead">
        설계 문서와 소스 코드는 <a href={REPO}>GitHub</a>에 공개되어 있습니다.
      </p>
      <Credit />
      </div>
    </div>
  );
}

/** 제품을 묶는 분류 전체를 펼쳐 볼 수 있게: 대분류 > 중분류(공식 분류) > 제품군 > 제품 */
function TaxonomySection() {
  const [data, setData] = useState<Taxonomy | null>(null);
  useEffect(() => {
    fetchTaxonomy()
      .then(setData)
      .catch(() => setData(null));
  }, []);
  if (!data) return null;
  const count = (families: { companies: number }[]) => families.reduce((sum, f) => sum + f.companies, 0);
  return (
    <>
      <h2 id="taxonomy">제품 분류 체계</h2>
      <p className="lead">
        상위 범주는 {data.source}의 대분류·중분류를 그대로 사용합니다. 하위 단계는 공식 분류가 포괄적이어서(제품 이름의 35%가 "그 외 기타 …" 항목에
        해당) 제품 기준으로 재구성한 제품군을 사용합니다. 제품군은 자체 정의한 분류이며 공식 분류가 아닙니다. 대신 제품마다 공식 세세분류 코드를 병기했고,
        물음표는 해당 항목이 불분명해 추정한 코드입니다.
      </p>
      <div className="taxonomy">
        {data.sections.map((section) => (
          <details key={section.code}>
            <summary>
              <code>{section.code}</code>
              <b>{section.name}</b>
              <span>중분류 {section.divisions.length}개</span>
            </summary>
            {section.divisions.map((division) => (
              <details key={division.code}>
                <summary>
                  <code>{division.code}</code>
                  {division.name}
                  <span>
                    제품군 {division.families.length}개 · 줄 {count(division.families).toLocaleString()}개
                  </span>
                </summary>
                {division.families.map((family) => (
                  <div key={family.name} className="family">
                    <h5>
                      {family.name}
                      <em>
                        기업 {family.companies}곳 · 제품 {family.products.length}가지
                      </em>
                    </h5>
                    <p className="names">
                      {family.products.map((product, i) => (
                        <span key={product.name} title={`${product.ksic} ${product.ksic_name} · 기업 ${product.companies}곳`}>
                          {i > 0 && ", "}
                          {product.name}{" "}
                          <i>
                            {product.ksic}
                            {product.unsure ? "?" : ""}
                          </i>
                        </span>
                      ))}
                    </p>
                  </div>
                ))}
              </details>
            ))}
          </details>
        ))}
      </div>
    </>
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

/** Agent 평가: 149문항 결과, 웹 검색만 쓴 Claude와의 비교. 문항은 "평가 문항" 탭에 있다. */
function EvalSection() {
  const data = useEval();
  if (!data) return null;
  const before = data.items.map((item) => item.agent);
  const sides = data.items.map((item) => item.after);
  const pct = (n: number, of: number) => Math.round((n / of) * 100);
  const tally = (list: { verdict: string }[], ...verdicts: string[]) => list.filter((s) => verdicts.includes(s.verdict)).length;
  const correct = tally(sides, "correct");
  const hit = tally(sides, "correct", "partial");
  const [low, high] = wilson(correct, sides.length);
  const invented = data.items.filter((item) => item.agent.unverified.length + item.after.unverified.length > 0).length;
  return (
    <>
      <h2 id="agent">5. Agent 답변 정확도</h2>
      <p className="lead">
        공시 원문에서 직접 작성한 {sides.length}문항으로 측정했습니다({data.agent_model}). 단일 값 조회로 끝나는 질문은 제외하고, 정정 공시 사이의 시점
        구분이나 복수 공시의 비교가 필요한 질문으로 구성했습니다. 문항과 정답은 추출기를 거치지 않은 원자료로 작성했고, 작성자와 분리된 검토자가 재확인했으며,
        채점자는 답의 출처를 모르는 상태에서 판정했습니다. 작성·검토·채점은 모두 Claude가 수행했으며 사람이 원문을 직접 확인한 것은 아닙니다.
      </p>
      <div className="stages">
        <div>
          <h4>
            최근 결과{" "}
            <span>
              정답 {pct(correct, sides.length)}% · 부분 정답까지 {pct(hit, sides.length)}%
            </span>
          </h4>
          <VerdictBar sides={sides} />
        </div>
      </div>
      <VerdictLegend />
      <details className="more">
        <summary>검증 방법과 한계</summary>
        <ul className="notes">
          <li>
            <b>결과</b> 정답 {pct(correct, sides.length)}% (95% 신뢰구간 {low}~{high}%), 부분 정답 포함 {pct(hit, sides.length)}%. 사실 오류{" "}
            {tally(sides, "wrong")}문항. 조회 결과에 없는 접수번호를 답에 인용한 문항은 {invented}개입니다.
          </li>
          <li>
            <b>해석상 유의점</b> 첫 실행(정답 {pct(tally(before, "correct"), before.length)}%)에서 드러난 결함을 고치고, 그 뒤 답의 형식과 도구(주가 움직임,
            화면 조작)를 바꾼 다음 다시 측정한 값입니다. 문항을 확인한 뒤 개선한 것이므로 새 문항에서 같은 수준이 재현된다는 보장은 없습니다.
          </li>
          <li>
            <b>남은 오답</b> 사실 오류 {tally(sides, "wrong")}문항 가운데 5문항은 주주 명단의 변동과 두 보고서 간 지분율 차이를 묻는 문항이며, 조회 도구가
            해당 자료를 반환하지 않아 생긴 것으로 확인했습니다.
          </li>
        </ul>
      </details>
      <ByType data={data} />

      <p className="lead">
        문항, 정답, Agent의 도구 호출과 답, 판정 사유는 "평가 문항" 탭에서 모두 확인할 수 있습니다. 도구 구성, 모델 간 비교, 웹 검색과의 비교, 오답의 원인
        분석 같은 개발 과정의 기록은{" "}
        <a href="https://github.com/minnj1025/company-graph/blob/main/eval/v2/results.md" target="_blank" rel="noreferrer">
          저장소의 평가 문서
        </a>
        에 있습니다.
      </p>

      <h2 id="followups">제품·연속 질문 평가</h2>
      <p className="lead">
        위 평가는 모두 관계 질문입니다. 제품 질문("라면을 만드는 상장사는?")과 앞선 답을 이어받는 연속 질문("그 회사의 최대주주는?")은 별도의 29문항으로
        측정했습니다. 문항 수가 적어 경향만 확인할 수 있습니다.
      </p>
      <table className="grid">
        <thead>
          <tr>
            <th />
            <th className="num">정답</th>
            <th className="num">부분 정답</th>
            <th className="num">오답</th>
          </tr>
        </thead>
        <tbody>
          {V3.map(([name, ...cells]) => (
            <tr key={name}>
              <td>{name}</td>
              {cells.map((cell, index) => (
                <td key={index} className="num">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <details className="more">
        <summary>검증 방법과 한계</summary>
        <ul className="notes">
          <li>
            <b>해석상 유의점</b> 오답 문항을 확인하고 개선한 뒤의 결과이므로 새 문항에서 재현된다는 보장이 없습니다. 개선 전에는 제품 문항 10개 중 2개가
            오답이었습니다.
          </li>
          <li>
            <b>오답 유형과 조치</b> 타사 제품을 매입해 판매하는 기업을 제조사로 답한 경우였습니다. 보고서가 "상품"으로 기재한 줄에 표시를 추가해 해소했습니다.
            남은 부분 정답은 데이터에서 비롯됩니다(지주회사 보고서에 자회사의 표가 그대로 실린 경우, 사업부문 내 비중을 전체 비중으로 판독한 표).
          </li>
          <li>
            <b>미지원 범위</b> 이슈 종목과 기사 요약에 관한 질문은 아직 Agent가 답하지 못합니다. 해당 데이터가 Agent의 도구에 연결되어 있지 않습니다.
          </li>
        </ul>
      </details>
    </>
  );
}

export function QuestionsPage() {
  const data = useEval();
  return (
    <div className="page">
      <h2>평가 문항</h2>
      <p className="lead">
        Agent 평가에 사용한 전체 문항입니다. 줄을 누르면 정답, 근거 공시, Agent의 도구 호출과 답, 판정 사유가 펼쳐집니다. 유형별 앞의 두 문항에는 웹 검색
        전용 Claude의 답도 함께 있습니다.
      </p>
      {data ? <Questions data={data} /> : <p className="muted">불러오는 중…</p>}
      <Credit />
    </div>
  );
}


