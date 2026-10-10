import { Fragment, useEffect, useState } from "react";
import type React from "react";
import { fetchHot, fetchHotGraph } from "./api";
import { Credit } from "./Pages";
import type { GraphData, HotDay, HotGroup } from "./types";

type Side = "up" | "down";
const WORD = { up: "급등", down: "급락" };

const DART = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=";
const LIMIT = 30; // 하루 가격 제한폭. 막대의 끝
const RELATIONS = ["계열", "지분", "공급계약"];
const signed = (value: number, digits = 2) => `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
const mean = (values: number[]) => values.reduce((sum, value) => sum + value, 0) / values.length;

/** 종목군의 이름: 묶어 준 제품 이름. 관계로만 이어졌으면 가장 많이 오른 곳의 이름을 앞세운다 */
function title(group: HotGroup): string {
  const products = group.why.filter((why) => !RELATIONS.includes(why) && !why.includes("_"));
  return products.length ? products.slice(0, 2).join(" · ") : `${group.members[0].name} 외 ${group.n - 1}`;
}

/** 무엇으로 이어졌는지: 제품, 지분·계열, 공급계약 */
function ties(group: HotGroup): string[] {
  const out = group.kind === "관계" ? [] : ["제품"];
  const relations = group.why.filter((why) => RELATIONS.includes(why));
  if (relations.some((why) => why !== "공급계약")) out.push(relations.filter((why) => why !== "공급계약").join("·"));
  if (relations.includes("공급계약")) out.push("공급계약");
  return out;
}

/** 그날과 다음 날의 기사를 찾는 검색 주소. 기사는 저장하지 않고 검색으로 넘긴다 */
function newsLink(names: string[], day: string): string {
  const next = new Date(day);
  next.setDate(next.getDate() + 1);
  const dots = (date: string) => date.replaceAll("-", ".");
  const query = new URLSearchParams({ where: "news", query: names.join(" "), pd: "3", ds: dots(day), de: dots(next.toISOString().slice(0, 10)) });
  return `https://search.naver.com/search.naver?${query}`;
}

/** 제목에 매체 이름이 없을 때 주소로 알아보는 매체 */
const OUTLETS: Record<string, string> = {
  "newspim.com": "뉴스핌", "mt.co.kr": "머니투데이", "hankyung.com": "한국경제", "sedaily.com": "서울경제", "etoday.co.kr": "이투데이",
  "fnnews.com": "파이낸셜뉴스", "edaily.co.kr": "이데일리", "mk.co.kr": "매일경제", "sbs.co.kr": "SBS Biz", "asiae.co.kr": "아시아경제",
  "heraldcorp.com": "헤럴드경제", "yna.co.kr": "연합뉴스", "newsis.com": "뉴시스", "cbci.co.kr": "CBC뉴스", "widedaily.com": "와이드경제",
  "thebell.co.kr": "더벨", "businesspost.co.kr": "비즈니스포스트", "chosun.com": "조선비즈", "donga.com": "동아일보", "joongang.co.kr": "중앙일보", "news1.kr": "뉴스1",
};
const outletOf = (url: string) => {
  const host = new URL(url).hostname.replace(/^(www|m|biz|markets|news)\./, "");
  return OUTLETS[host] ?? OUTLETS[host.split(".").slice(-3).join(".")] ?? OUTLETS[host.split(".").slice(-2).join(".")] ?? host;
};
/** 표에 앞세울 기사: 제목다운 제목을 가진 첫 근거. 종목 이름만 적힌 시세 페이지 같은 것은 건너뛴다 */
const lead = (sources: { title: string; url: string }[]) => sources.find((source) => headline(source).title.length >= 12);

/** 검색 결과의 제목에서 기사 제목과 매체를 가른다. "제목 < 증권 < 기사본문 - 매체", "제목 - 매체" 꼴을 다듬는다 */
function headline(source: { title: string; url: string }): { title: string; outlet: string } {
  const [head, ...tail] = source.title.split(" < ");
  const rest = tail.join(" < ");
  const cut = (text: string) => {
    const at = text.lastIndexOf(" - ");
    return at > 0 && text.length - at - 3 <= 16 ? [text.slice(0, at), text.slice(at + 3)] : [text, ""];
  };
  const [title, fromHead] = cut(head.trim());
  const outlet = fromHead || cut(rest)[1] || outletOf(source.url);
  return { title: title.replace(/\s*·\s*시장 영향은\?$/, ""), outlet: outlet.replace(/\(.*$/, "").trim() };
}

/** 종목 이름 옆의 시장 표시 */
function Market({ name }: { name?: string | null }) {
  return name ? <span className="hot-market">{name}</span> : null;
}

function Bar({ value }: { value: number }) {
  return (
    <span className={`hot-bar ${value < 0 ? "fall" : ""}`}>
      <i style={{ width: `${Math.min(100, (Math.abs(value) / LIMIT) * 100)}%` }} />
    </span>
  );
}

/** 거래일 60개의 띠: 위로는 그날의 연관 급등, 아래로는 연관 급락의 수. 눌러서 그날로 간다 */
function Days({ days, day, onPick }: { days: HotDay["days"]; day: string; onPick: (day: string) => void }) {
  // 고른 날이 가장 최근 60거래일 밖이면 그날을 가운데에 둔 60일을 보인다
  const at = days.findIndex((each) => each.day === day);
  const from = Math.max(0, Math.min(at - 30, days.length - 60));
  const recent = days.slice(at < 60 ? 0 : from, (at < 60 ? 0 : from) + 60).reverse();
  const top = Math.max(1, ...recent.map((each) => Math.max(each.up, each.down)));
  return (
    <div className="hot-days" role="group" aria-label="최근 60거래일">
      {recent.map((each) => (
        <button
          key={each.day}
          className={each.day === day ? "on" : ""}
          title={`${each.day} · 연관 급등 ${each.up}건 · 연관 급락 ${each.down}건 · 시장 ${signed(each.market)}`}
          onClick={() => onPick(each.day)}
        >
          <span>
            <i className="up" style={{ height: `${(each.up / top) * 100}%` }} />
          </span>
          <span>
            <i className="down" style={{ height: `${(each.down / top) * 100}%` }} />
          </span>
        </button>
      ))}
    </div>
  );
}

function Detail({ group, day, side, onGraph }: { group: HotGroup; day: string; side: Side; onGraph: () => void }) {
  return (
    <div className="hot-detail">
      <table>
        <tbody>
          {group.members.map((member) => (
            <tr key={member.id}>
              <td>
                {member.name}
                <Market name={member.market} />
              </td>
              <td className={`${side} num`}>{signed(member.change)}</td>
              <td>
                <Bar value={member.change} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="hot-side">
        <p>
          {group.kind === "관계"
            ? `${ties(group).join(", ")} 관계로 이어진 상장사 ${group.of}곳 가운데 ${group.n}곳이 같은 날 ${WORD[side]}했습니다.`
            : `${title(group)}의 매출 비중이 10% 이상인 상장사 ${group.of}곳 가운데 ${group.n}곳이 같은 날 ${WORD[side]}했습니다.`}
          {(group.streak ?? 1) > 1 && ` 앞 거래일에 이어 ${group.streak}거래일째입니다.`}
        </p>
        {group.news && group.news.found !== "none" && (
          <div className="hot-news">
            <b>{group.news.found === "group" ? "당일 기사" : "한 종목을 다룬 당일 기사"}</b>
            {group.news.sources.map((source) => (
              <a key={source.url} href={source.url} target="_blank" rel="noreferrer">
                {headline(source).title} <span className="muted">{headline(source).outlet} ↗</span>
              </a>
            ))}
            <p>
              <span className="muted">자동 요약</span> {group.news.reason}
            </p>
          </div>
        )}
        {group.news?.found === "none" && <p className="muted">이 종목들을 함께 다룬 당일 기사는 찾지 못했습니다.</p>}
        <button onClick={onGraph}>관계 그래프로 보기</button>
        <a href={newsLink(group.members.slice(0, 2).map((member) => member.name), day)} target="_blank" rel="noreferrer">
          당일 기사 검색 ↗
        </a>
      </div>
    </div>
  );
}

export function HotPage({ onShow }: { onShow: (title: string, graph: GraphData) => void }) {
  const [data, setData] = useState<HotDay | null>(null);
  const [day, setDay] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [weak, setWeak] = useState(false);
  const [side, setSide] = useState<Side>("up");
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    fetchHot(day ?? undefined)
      .then((result) => {
        if (!alive) return;
        setData(result);
        setOpen(null);
      })
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [day]);

  if (failed) return <div className="page"><p className="muted">불러오지 못했습니다.</p></div>;
  if (!data) return <div className="page"><p className="muted">불러오는 중…</p></div>;

  const at = data.days.findIndex((each) => each.day === data.day);
  const part = side === "up" ? data : data.down;
  const word = WORD[side];
  const all = part.groups.map((group, index) => ({ group, index, average: mean(group.members.map((member) => member.change)) }));
  const strong = all.filter(({ group }) => group.grade === "뚜렷함");
  const rows = (weak ? all : strong).sort((a, b) => Number(b.group.grade === "뚜렷함") - Number(a.group.grade === "뚜렷함") || Math.abs(b.average) - Math.abs(a.average));
  const show = (group: HotGroup, index: number) => fetchHotGraph(data.day, index, side).then((graph) => onShow(`${data.day} ${word} · ${title(group)}`, graph));

  return (
    <div className="page hot">
      <h2>이슈 종목</h2>
      <p className="lead">
        그날 크게 움직인 종목을, 공시상 무엇으로 이어져 있는지에 따라 묶었습니다. 테마를 미리 정해 두지 않고 사업보고서의 제품 구성과 지분·계열·공급계약
        관계에서 연결 고리를 찾습니다.
      </p>

      <div className="hot-top">
        <div className="hot-pick">
          <button disabled={at >= data.days.length - 1} onClick={() => setDay(data.days[at + 1].day)} aria-label="이전 거래일">
            ‹
          </button>
          <select value={data.day} onChange={(event) => setDay(event.target.value)}>
            {data.days.map((each) => (
              <option key={each.day}>{each.day}</option>
            ))}
          </select>
          <button disabled={at <= 0} onClick={() => setDay(data.days[at - 1].day)} aria-label="다음 거래일">
            ›
          </button>
        </div>
        <Days days={data.days} day={data.day} onPick={setDay} />
      </div>

      <dl className="hot-stats">
        <div>
          <dt>시장 등락률(중앙값)</dt>
          <dd className={data.market >= 0 ? "up" : "down"}>{signed(data.market)}</dd>
        </div>
        <div>
          <dt>분석 대상</dt>
          <dd>{data.watched.toLocaleString()}종목</dd>
        </div>
        <div>
          <dt>급등 / 급락 종목</dt>
          <dd>
            <span className="up">{data.hot}</span> <span className="muted">/</span> <span className="down">{data.down.hot}</span>
          </dd>
        </div>
        <div>
          <dt>연관 급등 / 급락</dt>
          <dd>
            <span className="up">{data.groups.filter((group) => group.grade === "뚜렷함").length}</span> <span className="muted">/</span>{" "}
            <span className="down">{data.down.groups.filter((group) => group.grade === "뚜렷함").length}</span>
            <small>건</small>
          </dd>
        </div>
      </dl>

      <div className="hot-sides" role="tablist">
        {(["up", "down"] as const).map((each) => (
          <button
            key={each}
            role="tab"
            aria-selected={side === each}
            className={side === each ? `on ${each}` : ""}
            onClick={() => {
              setSide(each);
              setOpen(null);
            }}
          >
            {each === "up" ? "상승" : "하락"}
          </button>
        ))}
      </div>

      <div className="hot-bar-head">
        <h3>연관 {word}</h3>
        <label>
          <input type="checkbox" checked={weak} onChange={(event) => setWeak(event.target.checked)} /> 약한 신호 포함
        </label>
      </div>
      {rows.length ? (
        <table className="hot-table">
          <thead>
            <tr>
              <th className="num">#</th>
              <th>연결 고리</th>
              <th className="num">평균 등락률</th>
              <th className="num">{word} 종목</th>
              <th>주도주</th>
              <th className="num">연속</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ group, index, average }, order) => (
              <Fragment key={index}>
                <tr className={`${open === index ? "open" : ""} ${group.grade === "뚜렷함" ? "" : "weak"}`} onClick={() => setOpen(open === index ? null : index)}>
                  <td className="num muted">{order + 1}</td>
                  <td>
                    <b>{title(group)}</b>
                    {ties(group).map((tie) => (
                      <span key={tie} className="hot-tie">
                        {tie}
                      </span>
                    ))}
                    {group.grade !== "뚜렷함" && <span className="hot-tie dim">약한 신호</span>}
                    {group.news?.found === "group" && lead(group.news.sources) && (
                      <p className="hot-why">
                        {/* 제목을 누르면 줄이 펴지지 않고 기사로 간다 */}
                        <a href={lead(group.news.sources)!.url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>
                          {headline(lead(group.news.sources)!).title} <span className="muted">· {headline(lead(group.news.sources)!).outlet} ↗</span>
                        </a>
                      </p>
                    )}
                  </td>
                  <td className={`${side} num heat`} style={{ "--heat": Math.min(1, Math.abs(average) / 20) } as React.CSSProperties}>
                    {signed(average)}
                  </td>
                  <td className="num">
                    {group.n}
                    <span className="muted"> / {group.of}</span>
                  </td>
                  <td className="hot-lead">
                    {group.members.slice(0, 2).map((member) => (
                      <span key={member.id}>
                        {member.name} <em className={side}>{signed(member.change, 1)}</em>
                      </span>
                    ))}
                  </td>
                  <td className="num">{(group.streak ?? 1) > 1 ? `${group.streak}일째` : <span className="muted">–</span>}</td>
                </tr>
                {open === index && (
                  <tr className="hot-open">
                    <td />
                    <td colSpan={5}>
                      <Detail group={group} day={data.day} side={side} onGraph={() => show(group, index)} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="hot-none">이 거래일에는 기준을 넘는 연관 {word}이 없습니다.</p>
      )}


      <div className="hot-bar-head">
        <h3>개별 {word}</h3>
        <span className="muted">
          연관 {word}에 묶이지 않고 시장 대비 10%p 이상 {side === "up" ? "상승" : "하락"}
          {part.alone_total > part.alone.length && ` · 상위 ${part.alone.length} / 전체 ${part.alone_total}`}
        </span>
      </div>
      <table className="hot-table alone">
        <thead>
          <tr>
            <th className="num">#</th>
            <th>종목</th>
            <th className="num">등락률</th>
            <th>최근 공시 (4일 이내)</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {part.alone.map((item, order) => (
            <tr key={item.id}>
              <td className="num muted">{order + 1}</td>
              <td>
                <b>{item.name}</b>
                <Market name={item.market} />
              </td>
              <td className={`${side} num`}>
                {signed(item.change)}
                <Bar value={item.change} />
              </td>
              <td>
                {item.filings.length ? (
                  item.filings.map((filing) => (
                    <a key={filing.rcept_no} href={DART + filing.rcept_no} target="_blank" rel="noreferrer">
                      {filing.title} <span className="muted">{filing.date.slice(5)}</span>
                    </a>
                  ))
                ) : (
                  <span className="muted">–</span>
                )}
              </td>
              <td className="num">
                <a href={newsLink([item.name], data.day)} target="_blank" rel="noreferrer" className="muted">
                  기사 ↗
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {part.alone.length === 0 && <p className="hot-none">해당하는 종목이 없습니다.</p>}
      <details className="more hot-notes">
        <summary>기준과 유의사항</summary>
      <p className="hot-foot muted">
        {word} 종목은 "{word}한 곳 / 같은 연결 고리를 가진 상장사 전체"입니다. 연결 고리는 종목들의 공통점이며 주가가 움직인 원인을 뜻하지 않습니다. 연결 고리 아래의 한 줄은 그날 이 종목들을 다룬 기사의 제목으로, 기사를 찾은 경우에만 있습니다. 줄을 펴면 나오는 자동 요약은 기사와 어긋날 수 있습니다.
        약한 신호는 세 종목뿐이거나 일부만 움직인 경우로, 과거 기간에 견주면 넷에 하나꼴로 우연히도 나타납니다.
      </p>
      <p className="hot-foot muted">
        공시는 이 사이트가 수집하는 종류(정기보고서, 공급계약, 지분 변동)만 표시됩니다. 실적 발표나 거래 재개 공시는 포함되지 않습니다.
      </p>

      <p className="hot-foot muted">
        한국거래소(KRX) 통계정보의 일별 시세를 가공했습니다. 장중 시세가 아니며 거래일 다음 영업일 이후 갱신됩니다. 20거래일 평균 거래대금 1억 원 미만 종목과
        스팩은 제외했습니다. 급등(급락) 종목은 시장 중앙값 대비 3%p 이상 오르고(내리고) 당일 상위(하위) 8%에 든 종목입니다. 이 화면은 과거의 주가 움직임을 정리한 것으로 투자
        권유가 아닙니다.
      </p>
      </details>
      <Credit />
    </div>
  );
}
