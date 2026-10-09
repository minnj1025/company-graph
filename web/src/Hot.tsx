import { useEffect, useState } from "react";
import { fetchHot, fetchHotGraph } from "./api";
import { Credit } from "./Pages";
import type { GraphData, HotDay, HotGroup } from "./types";

const DART = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=";
const signed = (value: number) => `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;

/** 무리를 묶은 공통점을 읽는 말로. 제품 이름은 그대로, 관계는 "계열로 이어짐"처럼 */
function reason(group: HotGroup): string {
  const relations = group.why.filter((why) => ["계열", "지분", "공급계약"].includes(why));
  const products = group.why.filter((why) => !relations.includes(why) && !why.startsWith("stake_") && !why.startsWith("supply_"));
  // "지분으로", "계열로": 받침이 없으면 "로"
  const last = relations.length ? relations[relations.length - 1] : "";
  const particle = last && (last.charCodeAt(last.length - 1) - 0xac00) % 28 === 0 ? "로" : "으로";
  const linked = relations.length ? `${relations.join("·")}${particle} 이어진 회사` : "";
  return [products.join(" · "), linked].filter(Boolean).join(" + ") || "서로 이어진 회사";
}

/** 그날과 다음 날의 기사를 찾는 검색 주소. 기사는 저장하지 않고 검색으로 넘긴다 */
function newsLink(group: HotGroup, day: string): string {
  const next = new Date(day);
  next.setDate(next.getDate() + 1);
  const dots = (date: string) => date.replaceAll("-", ".");
  const words = group.members.slice(0, 2).map((member) => member.name);
  const query = new URLSearchParams({ where: "news", query: words.join(" "), pd: "3", ds: dots(day), de: dots(next.toISOString().slice(0, 10)) });
  return `https://search.naver.com/search.naver?${query}`;
}

function Group({ group, day, onGraph }: { group: HotGroup; day: string; onGraph: () => void }) {
  const [open, setOpen] = useState(false);
  const shown = open ? group.members : group.members.slice(0, 6);
  return (
    <li className="hot-group">
      <div className="hot-head">
        <b>{reason(group)}</b>
        <span className="hot-count">
          {group.of}곳 중 {group.n}곳이 올랐습니다
        </span>
      </div>
      <ul className="hot-members">
        {shown.map((member) => (
          <li key={member.id}>
            {member.name} <em>{signed(member.change)}</em>
          </li>
        ))}
        {group.members.length > 6 && (
          <li>
            <button className="text" onClick={() => setOpen(!open)}>
              {open ? "접기" : `${group.members.length - 6}곳 더`}
            </button>
          </li>
        )}
      </ul>
      <div className="hot-actions">
        <button className="text" onClick={onGraph}>
          그래프에서 보기
        </button>
        <a href={newsLink(group, day)} target="_blank" rel="noreferrer">
          그날의 기사 찾기 ↗
        </a>
      </div>
    </li>
  );
}

export function HotPage({ onShow }: { onShow: (title: string, graph: GraphData) => void }) {
  const [data, setData] = useState<HotDay | null>(null);
  const [day, setDay] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    fetchHot(day ?? undefined)
      .then((result) => alive && setData(result))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [day]);

  if (failed) return <div className="page"><p className="muted">불러오지 못했습니다.</p></div>;
  if (!data) return <div className="page"><p className="muted">불러오는 중…</p></div>;

  const at = data.days.indexOf(data.day);
  const clear = data.groups.map((group, index) => ({ group, index })).filter(({ group }) => group.grade === "뚜렷함");
  const fair = data.groups.map((group, index) => ({ group, index })).filter(({ group }) => group.grade !== "뚜렷함");
  const show = (group: HotGroup, index: number) =>
    fetchHotGraph(data.day, index).then((graph) => onShow(`${data.day} · ${reason(group)}`, graph));
  const list = (items: typeof clear) => (
    <ul className="hot-list">
      {items.map(({ group, index }) => (
        <Group key={index} group={group} day={data.day} onGraph={() => show(group, index)} />
      ))}
    </ul>
  );

  return (
    <div className="page hot">
      <h2>함께 오른 무리</h2>
      <p className="lead">
        테마를 미리 정해 두지 않았습니다. 그날 시장보다 뚜렷이 오른 종목들이 공시에서 무엇으로 이어져 있는지를 거꾸로 찾습니다. 같은 제품을 파는
        회사들일 수도, 지분이나 계열로 이어진 회사들일 수도 있습니다. 왜 올랐는지는 공시에 없으므로 기사 검색으로 넘깁니다.
      </p>

      <div className="hot-day">
        <button disabled={at >= data.days.length - 1} onClick={() => setDay(data.days[at + 1])} aria-label="앞 거래일">
          ←
        </button>
        <select value={data.day} onChange={(event) => setDay(event.target.value)}>
          {data.days.map((each) => (
            <option key={each}>{each}</option>
          ))}
        </select>
        <button disabled={at <= 0} onClick={() => setDay(data.days[at - 1])} aria-label="다음 거래일">
          →
        </button>
        <span className="muted">
          장 마감 기준 · 시장 중앙값 {signed(data.market)} · 살펴본 {data.watched.toLocaleString()}종목 가운데 {data.hot}곳이 뚜렷이 올랐습니다
        </span>
      </div>

      {clear.length ? list(clear) : <p className="hot-none">이날은 뚜렷한 무리가 없습니다.</p>}

      {fair.length > 0 && (
        <details className="hot-fair">
          <summary>참고로만 볼 무리 {fair.length}개</summary>
          <p className="muted">
            세 곳뿐이거나 그 공통점을 가진 회사의 일부만 오른 무리입니다. 지난 기간에 견줘 보면 넷에 하나꼴로 우연히도 나옵니다.
          </p>
          {list(fair)}
        </details>
      )}

      <h2>혼자 오른 곳</h2>
      <p className="lead">
        어느 무리에도 들지 않고 시장보다 10%p 넘게 오른 종목입니다. 이 사이트가 모은 공시(정기보고서, 공급계약, 지분 변동) 가운데 그날까지 나흘 사이에 나온 것이 있으면 붙였습니다. 실적 발표나 거래 재개 같은 다른 공시는 모으지 않아 여기에 없습니다.
        {data.alone_total > data.alone.length && ` 많이 오른 ${data.alone.length}곳만 보입니다(모두 ${data.alone_total}곳).`}
      </p>
      <table className="grid hot-alone">
        <tbody>
          {data.alone.map((item) => (
            <tr key={item.id}>
              <td>{item.name}</td>
              <td className="num">{signed(item.change)}</td>
              <td>
                {item.filings.length
                  ? item.filings.map((filing) => (
                      <a key={filing.rcept_no} href={DART + filing.rcept_no} target="_blank" rel="noreferrer">
                        {filing.title} <span className="muted">{filing.date.slice(5)}</span>
                      </a>
                    ))
                  : <span className="muted">–</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="hot-note muted">
        한국거래소(KRX) 통계정보의 일별 시세를 가공했습니다. 장중 값이 아니고 거래일 다음 날 이후에 갱신됩니다. 거래가 적은 종목(20거래일 평균 거래대금 1억 원
        미만)과 스팩은 뺐습니다. 함께 올랐다는 사실만 보여 주며, 앞으로의 주가나 매수·매도에 대한 의견이 아닙니다.
      </p>
      <Credit />
    </div>
  );
}
