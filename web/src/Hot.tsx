import { Fragment, useEffect, useState } from "react";
import { fetchHot, fetchHotGraph } from "./api";
import { Credit } from "./Pages";
import type { GraphData, HotDay, HotGroup } from "./types";

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

function Bar({ value }: { value: number }) {
  return (
    <span className="hot-bar">
      <i style={{ width: `${Math.min(100, (Math.abs(value) / LIMIT) * 100)}%` }} />
    </span>
  );
}

/** 거래일 60개의 띠: 막대 높이는 그날 잡힌 종목군의 수. 눌러서 그날로 간다 */
function Days({ days, day, onPick }: { days: HotDay["days"]; day: string; onPick: (day: string) => void }) {
  // 고른 날이 가장 최근 60거래일 밖이면 그날을 가운데에 둔 60일을 보인다
  const at = days.findIndex((each) => each.day === day);
  const from = Math.max(0, Math.min(at - 30, days.length - 60));
  const recent = days.slice(at < 60 ? 0 : from, (at < 60 ? 0 : from) + 60).reverse();
  const top = Math.max(1, ...recent.map((each) => each.strong));
  return (
    <div className="hot-days" role="group" aria-label="최근 60거래일">
      {recent.map((each) => (
        <button
          key={each.day}
          className={each.day === day ? "on" : ""}
          title={`${each.day} · 강한 신호 ${each.strong}개 · 시장 ${signed(each.market)}`}
          onClick={() => onPick(each.day)}
        >
          <i style={{ height: `${Math.max(6, (each.strong / top) * 100)}%` }} className={each.strong ? "" : "none"} />
        </button>
      ))}
    </div>
  );
}

function Detail({ group, day, onGraph }: { group: HotGroup; day: string; onGraph: () => void }) {
  return (
    <div className="hot-detail">
      <table>
        <tbody>
          {group.members.map((member) => (
            <tr key={member.id}>
              <td>{member.name}</td>
              <td className="up num">{signed(member.change)}</td>
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
            ? `${ties(group).join(", ")} 관계로 이어진 상장사 ${group.of}곳 가운데 ${group.n}곳이 같은 날 급등했습니다.`
            : `${title(group)}의 매출 비중이 10% 이상인 상장사 ${group.of}곳 가운데 ${group.n}곳이 같은 날 급등했습니다.`}
          {(group.streak ?? 1) > 1 && ` 앞 거래일에 이어 ${group.streak}거래일째입니다.`}
        </p>
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
  const all = data.groups.map((group, index) => ({ group, index, average: mean(group.members.map((member) => member.change)) }));
  const strong = all.filter(({ group }) => group.grade === "뚜렷함");
  const rows = (weak ? all : strong).sort((a, b) => Number(b.group.grade === "뚜렷함") - Number(a.group.grade === "뚜렷함") || b.average - a.average);
  const show = (group: HotGroup, index: number) => fetchHotGraph(data.day, index).then((graph) => onShow(`${data.day} · ${title(group)}`, graph));

  return (
    <div className="page hot">
      <h2>동반 상승 종목군</h2>
      <p className="lead">
        같은 날 함께 급등한 종목들이 공시상 무엇으로 연결되어 있는지를 찾습니다. 테마를 미리 정해 두지 않고, 사업보고서의 제품 구성과 지분·계열·공급계약
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
          <dt>급등 종목</dt>
          <dd>{data.hot}종목</dd>
        </div>
        <div>
          <dt>동반 상승 종목군</dt>
          <dd>
            {strong.length}개 <small>약한 신호 {all.length - strong.length}개</small>
          </dd>
        </div>
      </dl>

      <div className="hot-bar-head">
        <h3>종목군</h3>
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
              <th className="num">상승 종목</th>
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
                  </td>
                  <td className="up num">
                    {signed(average)}
                    <Bar value={average} />
                  </td>
                  <td className="num">
                    {group.n}
                    <span className="muted"> / {group.of}</span>
                  </td>
                  <td className="hot-lead">
                    {group.members.slice(0, 2).map((member) => (
                      <span key={member.id}>
                        {member.name} <em className="up">{signed(member.change, 1)}</em>
                      </span>
                    ))}
                  </td>
                  <td className="num">{(group.streak ?? 1) > 1 ? `${group.streak}일째` : <span className="muted">–</span>}</td>
                </tr>
                {open === index && (
                  <tr className="hot-open">
                    <td />
                    <td colSpan={5}>
                      <Detail group={group} day={data.day} onGraph={() => show(group, index)} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="hot-none">이 거래일에는 기준을 넘는 종목군이 없습니다.</p>
      )}
      <p className="hot-foot muted">
        상승 종목은 "급등한 곳 / 같은 연결 고리를 가진 상장사 전체"입니다. 연결 고리는 종목들의 공통점이며 상승의 원인을 뜻하지 않습니다. 약한 신호는 세
        종목뿐이거나 일부만 오른 경우로, 과거 기간에 견주면 넷에 하나꼴로 우연히도 나타납니다.
      </p>

      <div className="hot-bar-head">
        <h3>개별 급등 종목</h3>
        <span className="muted">
          종목군에 속하지 않고 시장 대비 10%p 이상 상승
          {data.alone_total > data.alone.length && ` · 상위 ${data.alone.length} / 전체 ${data.alone_total}`}
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
          {data.alone.map((item, order) => (
            <tr key={item.id}>
              <td className="num muted">{order + 1}</td>
              <td>
                <b>{item.name}</b>
              </td>
              <td className="up num">
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
      <p className="hot-foot muted">
        공시는 이 사이트가 수집하는 종류(정기보고서, 공급계약, 지분 변동)만 표시됩니다. 실적 발표나 거래 재개 공시는 포함되지 않습니다.
      </p>

      <p className="hot-foot muted">
        한국거래소(KRX) 통계정보의 일별 시세를 가공했습니다. 장중 시세가 아니며 거래일 다음 영업일 이후 갱신됩니다. 20거래일 평균 거래대금 1억 원 미만 종목과
        스팩은 제외했습니다. 급등 종목은 시장 중앙값 대비 3%p 이상 오르고 당일 상위 8%에 든 종목입니다. 이 화면은 과거의 주가 움직임을 정리한 것으로 투자
        권유가 아닙니다.
      </p>
      <Credit />
    </div>
  );
}
