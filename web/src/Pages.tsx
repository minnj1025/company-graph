import { useEffect, useState } from "react";
import { fetchTaxonomy } from "./api";
import { ByType, Compare, Questions, VerdictBar, VerdictLegend, useEval, wilson } from "./Eval";
import type { Meta, Taxonomy } from "./types";

const REPO = "https://github.com/minnj1025/company-graph";

/** 검산 결과. 추출기를 다시 돌린 날(2026-10-08)의 값이며, 저장소 README와 같다. 가운데 두 칸은 맞은 수와 전체 수. */
const CHECKS: [string, number, number, string][] = [
  ["공급계약: 계약금액 ÷ 최근 매출액이 공시에 적힌 비율과 맞는가", 11739, 11853, "추출이 맞다는 근거"],
  ["취득·처분 결정: 금액 ÷ 자기자본이 적힌 비율과 맞는가", 2901, 2923, "추출이 맞다는 근거"],
  ["계열: 읽은 줄 수가 표에 적힌 회사 수와 같은가", 6301, 6438, "추출이 맞다는 근거"],
  ["과거 시점으로 조회했을 때 그 뒤에 공개된 정보가 섞이지 않는가", 59130, 59130, "시점 규칙이 지켜진다는 근거"],
  ["지분: 가진 쪽 공시와 내준 쪽 공시의 지분율이 서로 맞는가", 1939, 2307, "두 공시가 서로 맞는 정도 (추출 정확도가 아니다)"],
];

/** 이슈 종목의 묶음 수를, 등락률을 회사끼리 섞었을 때의 수와 견준 것 (hot.py check). 실제 / 섞었을 때 */
const HOT_CHECKS: [string, string, string][] = [
  ["연관 급등", "317건 / 22건 · 14.2배", "274건 / 13건 · 21.1배"],
  ["연관 급락", "152건 / 12건 · 13.2배", "99건 / 4건 · 25.5배"],
  ["약한 신호 (급등)", "584건 / 152건 · 3.8배", "376건 / 82건 · 4.6배"],
  ["하루에 잡히는 연관 급등", "평균 1.9건 · 없는 날 32일", "평균 1.2건 · 없는 날 82일"],
];

/** 평가 3판(eval/v3): 정답 / 부분 정답 / 틀림 */
const V3: [string, string, string, string][] = [
  ["제품 (10문항)", "7 / 1 / 2", "8 / 1 / 1", "7 / 3 / 0"],
  ["이슈 (3문항)", "2 / 1 / 0", "2 / 1 / 0", "2 / 1 / 0"],
  ["회사 (6문항)", "6 / 0 / 0", "5 / 1 / 0", "6 / 0 / 0"],
  ["이어지는 대화 (10쌍)", "8 / 0 / 2", "10 / 0 / 0", "–"],
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

/** 긴 페이지 왼쪽의 목차. 지금 읽는 구역에 표시가 따라 내려온다. 작은 제목(sub)은 들여 쓴다 */
const TOC: { id: string; label: string; sub?: boolean }[] = [
  { id: "what", label: "무엇이 들어 있나" },
  { id: "relations", label: "1. 관계" },
  { id: "products", label: "2. 제품" },
  { id: "taxonomy", label: "제품을 묶는 분류", sub: true },
  { id: "hot", label: "3. 이슈 종목" },
  { id: "news", label: "4. 기사 배경" },
  { id: "agent", label: "5. Agent" },
  { id: "baseline", label: "웹 검색과 견주면", sub: true },
  { id: "followups", label: "제품·이어지는 대화", sub: true },
  { id: "limits", label: "없는 것" },
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
      <h2 id="what">무엇이 들어 있나</h2>
      <p className="lead">
        금융감독원 전자공시(DART)에서 기업과 기업의 관계를 뽑아, 언제 성립했고 언제 공개됐고 언제 바뀌었는지를 같이 저장했습니다.
        상장사 2,759곳의 공시를 읽었고, 양식이 정해진 공시만 규칙으로 읽었습니다. 관계 추출에 LLM은 쓰지 않았습니다. 관계 말고 기업이 무엇을
        하는지(사업 내용과 제품)도 담았고, 한국거래소의 일별 시세로 그날 크게 움직인 종목이 무엇으로 이어져 있는지도 날마다 계산합니다. 아래는 데이터가
        만들어지는 순서대로, 단계마다 무엇을 어떻게 확인했는지입니다.
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

      <h2 id="relations">1. 공시에서 관계를 맞게 뽑았나</h2>
      <p className="lead">
        지분, 계열, 공급계약, 취득·처분 결정을 공시의 정해진 양식에서 규칙으로 읽었습니다. 수십만 줄을 사람이 다 볼 수 없고 정답지도 없어서, 공시 안에 적힌
        숫자끼리 검산하고 다른 기관의 자료와 대조했습니다.
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

      <h2 id="products">2. 제품을 맞게 읽고 묶었나</h2>
      <p className="lead">
        관계 표만으로는 "이 이슈와 닿는 회사"를 찾을 수 없어서, 사업보고서와 반기보고서의 "사업의 내용"을 두 층으로 담았습니다. 뒤의 이슈 종목과 Agent의
        답이 모두 이 제품 이름 위에 서 있습니다.
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
          이름과 붙인 이름을 나란히 보여 주고 짐작이 섞인 줄은 그렇게 표시합니다.
        </li>
        <li>
          탐색 화면의 보기 설정에서 "제품"을 켜면 기업, 제품, 제품군이 차례로 이어져 나타나고, 선의 굵기는 그 제품이 매출에서 차지하는 비중입니다.
        </li>
      </ul>
      <div className="checks">
        <div className="check">
          <div className="ring" style={{ background: "conic-gradient(#6fd08c 340deg, rgba(255,255,255,0.08) 0)" }}>
            <b>94.5%</b>
          </div>
          <div>
            <p>붙인 이름과 제품군이 맞는가 (무작위 200줄)</p>
            <span>맞음 189 · 제품군이 어긋남 6 · 이름이 어긋남 2 · 판단 불가 3 · 다른 사업으로 붙은 것 0</span>
          </div>
        </div>
        <div className="check">
          <div className="ring" style={{ background: "conic-gradient(#6fd08c 338deg, rgba(255,255,255,0.08) 0)" }}>
            <b>45/48</b>
          </div>
          <div>
            <p>규칙으로 못 읽어 모델이 글을 읽고 옮긴 표가 원문과 맞는가 (무작위 48곳)</p>
            <span>맞음 45 · 작은 차이 2 · 틀림 0 · 판단 불가 1</span>
          </div>
        </div>
      </div>
      <ul className="notes">
        <li>
          이름 검수는 이름을 붙인 쪽과 다른 검토자(Claude)가 했습니다. 원문 보고서가 아니라 표에 적힌 글자와 회사에 대한 지식으로 판단했고, 사람이 본 것은
          아닙니다. 짐작 표시가 붙은 50줄은 84%, 표시가 없는 150줄은 98%가 맞아서, 불확실하다는 표시가 실제로 덜 맞는 줄을 가리킵니다.
        </li>
        <li>
          어긋난 것은 경계가 애매한 경우였습니다(증권사의 자기매매를 "자산운용·투자"로, 위탁생산 연질캡슐을 "일반의약품"으로). 200줄에서 다른 사업으로 붙은
          것이 없었다는 것은 드물다는 뜻이지 없다는 뜻은 아닙니다. 이름이 붙지 않은 줄이 붙었어야 했는지는 보지 않았습니다.
        </li>
      </ul>

      <TaxonomySection />

      <h2 id="hot">3. 이슈 종목은 우연이 아닌가</h2>
      <p className="lead">
        "이슈 종목" 탭은 테마를 미리 정해 두지 않고, 그날 급등하거나 급락한 종목들이 같은 제품을 팔거나 지분·계열·공급계약으로 이어져 있으면 하나로
        묶습니다. 아무 종목이나 묶어도 이런 묶음이 나오는 것은 아닌지를, 등락률은 그대로 두고 어느 회사의 것인지만 섞은 결과와 견줘 확인했습니다.
      </p>
      <table className="grid">
        <thead>
          <tr>
            <th />
            <th className="num">규칙을 정한 기간 (2026년, 164거래일)</th>
            <th className="num">규칙을 정할 때 보지 않은 기간 (2025년, 222거래일)</th>
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
      <ul className="notes">
        <li>
          규칙(세 곳 이상, 셋째로 많이 움직인 곳이 시장 대비 5%p 이상, 같은 연결 고리를 가진 곳의 절반 이상)은 2026년 시세를 보며 정했습니다. 그래서
          2026년의 숫자는 규칙을 고른 데이터에서 나온 것이고, 2025년 쪽이 처음 보는 기간의 결과입니다. 다만 2025년의 주가에 2026년 보고서의 제품 표를
          댔습니다.
        </li>
        <li>
          두 곳짜리 묶음은 섞은 결과와 차이가 없어(1.3배) 버렸고, 세 곳뿐이거나 일부만 움직인 묶음은 "약한 신호"로 따로 둡니다. 약한 신호는 넷에 하나꼴로
          우연히도 나옵니다.
        </li>
        <li>
          실제 이슈였는지는 무작위로 고른 날의 묶음을 기사와 대조해 봤습니다(검토는 Claude가 웹 검색으로 했습니다). 연관 급등 14건 가운데 그 종목들이나
          그 업종의 상승을 다룬 기사가 있던 것이 11건, 한 종목의 개별 사정이던 것이 1건, 기사를 찾지 못한 것이 2건이었습니다.
        </li>
        <li>
          연관 급락은 그보다 약합니다. 20건 가운데 종목군이나 업종의 하락을 다룬 기사가 8건, 한 종목 기사이거나 시장 전체가 급락한 날의 기사뿐인 것이
          7건, 찾지 못한 것이 5건이었습니다. 시장이 크게 빠진 날에는 함께 내렸다기보다 다 같이 내린 것이 묶음으로 잡힙니다.
        </li>
        <li>
          묶음의 이름(연결 고리)은 종목들의 공통점이지 움직인 원인이 아닙니다. 원전 건설 기대로 오른 건설주가 "주택 건설"이라는 이름으로 묶이는 식입니다.
        </li>
      </ul>

      <h2 id="news">4. 기사가 전한 배경은 믿을 만한가</h2>
      <p className="lead">
        공시에는 그날 무슨 일이 있었는지가 없어서, 뚜렷한 연관 급등·급락마다 Claude Haiku가 웹을 검색해 기사가 전한 배경을 한 줄로 적습니다. 하루에 서너
        건이라 방문자 수와 상관없이 드는 값이 같습니다.
      </p>
      <ul className="notes">
        <li>
          지어내지 않게 두 가지를 코드로 막았습니다. 근거로 댄 기사의 주소가 실제 검색 결과에 있어야 하고, 그 기사가 해당 거래일 무렵의 것이어야 합니다.
          어긋나면 적은 내용을 버리고 "기사를 찾지 못함"으로 둡니다. 기사 본문은 저장하지 않고 한 줄 요약과 제목, 주소만 남깁니다.
        </li>
        <li>
          2026년 9월 1일부터 10월 8일까지의 61건 가운데 종목군을 다룬 기사를 찾은 것이 33건(54%), 한 종목만 다룬 기사가 6건, 찾지 못한 것이 22건입니다.
          찾지 못한 것의 다수는 지분·계열로만 이어진 묶음으로, 기사는 "반도체주 강세"로 쓰지 "어느 회사와 그 출자사들"로 쓰지 않습니다.
        </li>
        <li>
          기사를 찾는 힘: 미리 기사를 확인해 둔 23건 가운데, 기사가 있던 14건 중 12건을 찾았습니다. 처음 쟀을 때는 9건이었는데, 답이 중간에 끊겨 "찾지
          못함"으로 남던 결함 때문이었고 고친 뒤 다시 쟀습니다.
        </li>
        <li>
          요약이 기사와 맞는가: 모델이 기사를 찾았다고 한 17건을 다른 검토자(Claude)가 근거로 든 기사를 열어 대조했습니다. 기사의 날짜는 17건 모두
          맞았습니다. 요약이 기사 내용 그대로인 것이 11건, 큰 줄기는 맞지만 기사에 없는 세부가 섞인 것이 4건, 기사가 그렇게 말하지 않은 것이 2건이었습니다.
        </li>
        <li>
          어긋난 것은 기사에 없는 까닭을 보탠 경우였습니다(기사는 선박 수주 기대라고 썼는데 요약은 "캐나다 잠수함 수주 기대"라고 적음). 주소와 날짜는 코드로
          막을 수 있지만 요약의 내용은 막지 못합니다. 이 결과를 보고 "기사에 나온 내용만, 기사에 이름이 없는 종목은 적지 않는다"는 지시를 더했고, 그 뒤로는
          다시 재지 않았습니다. 그래서 화면의 표에는 요약이 아니라 사람이 쓴 기사 제목을 보이고, 자동 요약은 줄을 폈을 때 "자동 요약"이라고 밝혀 그 아래에 둡니다.
        </li>
        <li>검색은 네이버 뉴스가 아니라 Claude의 웹 검색이고 날짜로 정확히 거를 수 없어서, 화면의 "당일 기사 검색"으로는 나오는 기사를 놓치기도 합니다.</li>
      </ul>

      <EvalSection />

      <h2 id="limits">없는 것</h2>
      <ul className="notes">
        <li>분기보고서, 반기보고서의 지분·계열 표(반기보고서는 사업 내용만 읽었습니다), 최대주주와 특수관계인이 아닌 주주, 주요 고객, 합병·분할</li>
        <li>
          장중 시세. 주가는 한국거래소(KRX) 통계정보의 일별 값을 가공한 것으로, 거래일 다음 영업일 이후에 갱신됩니다. 종목별 시세 자체는 내보내지 않고
          이슈 종목의 계산에만 씁니다.
        </li>
        <li>실적 발표, 거래 재개 같은 공시. 그래서 개별 급등·급락 종목에 붙는 공시가 적습니다. 뉴스 본문도 저장하지 않습니다.</li>
        <li>2024년 1월 이전의 공급계약과 취득·처분 결정</li>
        <li>매수·매도 판단. 이 서비스는 공시에 적힌 사실만 보여 줍니다.</li>
      </ul>
      <p className="lead">
        설계 기록과 코드는 <a href={REPO}>GitHub</a>에 있습니다.
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
      <h2 id="taxonomy">제품을 묶는 분류</h2>
      <p className="lead">
        큰 범주는 {data.source}의 대분류와 중분류를 그대로 씁니다. 그 아래는 공식 분류가 뭉뚱그려지는 곳이 많아서(제품 이름의 35%가 "그 외 기타 …" 항목에
        들어갑니다) 제품 말로 다시 묶은 제품군을 씁니다. 제품군은 이 서비스가 만든 묶음이고 공식 분류가 아닙니다. 대신 제품마다 공식 분류의 세세분류
        코드를 붙여 두었습니다. 아래에서 펼쳐 볼 수 있고, 물음표는 맞는 항목이 분명치 않아 짐작으로 고른 코드입니다.
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
  const shared = data.items.filter((item) => item.baseline);
  const invented = data.items.filter((item) => item.agent.unverified.length + item.after.unverified.length > 0).length;
  return (
    <>
      <h2 id="agent">5. Agent는 얼마나 맞게 답하나</h2>
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

      <h2 id="baseline">웹 검색만 쓰는 Claude와 견주면</h2>
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

      <p className="lead">문항과 정답, Agent가 부른 도구와 답, 판정 사유는 위의 "평가 문항" 탭에서 모두 볼 수 있습니다.</p>

      <h2 id="followups">제품과 이어지는 대화를 묻는 질문</h2>
      <p className="lead">
        위의 문항은 모두 관계를 묻습니다. "라면을 만드는 상장사는?" 같은 제품 질문과, 앞의 답을 받아 "그 회사의 최대주주는?"처럼 이어 묻는 대화는 따로
        29문항을 만들어 쟀습니다. 문항이 적어 방향만 보입니다.
      </p>
      <table className="grid">
        <thead>
          <tr>
            <th />
            <th className="num">처음</th>
            <th className="num">제품 표를 넓힌 뒤</th>
            <th className="num">문항을 보고 고친 뒤</th>
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
      <ul className="notes">
        <li>칸은 정답 / 부분 정답 / 틀림입니다. "고친 뒤"는 틀린 문항을 보고 고친 것이라 새 문항에서도 같으리라는 보장이 없습니다.</li>
        <li>
          처음 틀린 것은 다른 회사가 만든 것을 받아 파는 회사를 "만드는 회사"로 답한 경우였습니다. 보고서가 "상품"이라고 적은 줄에 표시를 붙여 고쳤습니다.
          남은 부분 정답은 데이터에서 옵니다(지주회사의 글에 자회사의 표가 그대로 실린 경우, 사업부문 안쪽의 비중을 전체 비중으로 읽은 표).
        </li>
        <li>이슈 종목과 기사 배경을 묻는 질문은 아직 Agent가 답하지 못합니다. 그 데이터는 Agent의 도구에 이어져 있지 않습니다.</li>
      </ul>
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
