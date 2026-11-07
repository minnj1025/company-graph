import { useEffect, useMemo, useRef, useState } from "react";
import { fetchMemoPrices, type MemoPrices } from "./api";

/** 뉴스 뒤에 메모의 회사들이 실제로 어떻게 움직였는지: 뉴스 전 거래일 종가를 0으로 둔 누적 수익률.
 * 한 회사만 강조하고 나머지는 흐리게 둔다. 비교 기준은 보통 종목(수익률 중앙값, 점선)과 시장 전체(시가총액 가중, 잔 점선). 사후 기록이지 관계가 주가를 움직였다는 뜻이 아니다. */

export interface PriceCompany {
  id: number;
  role: "주인공" | "직접" | "간접";
}

const PAD = { left: 38, right: 10, top: 10, bottom: 22 };

const signed = (v: number | null | undefined, unit = "%") => (v === null || v === undefined ? "–" : `${v > 0 ? "+" : ""}${v.toFixed(1)}${unit}`);

function ticks(lo: number, hi: number): number[] {
  const span = hi - lo;
  const step = [1, 2, 5, 10, 20, 25, 50].find((s) => span / s <= 4) ?? 100;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(v);
  return out;
}

export function PriceMoves({ day, companies, onCompany }: { day: string; companies: PriceCompany[]; onCompany: (id: number) => void }) {
  const [data, setData] = useState<MemoPrices | null>(null);
  const [failed, setFailed] = useState(false);
  const [focus, setFocus] = useState<number | null>(null);
  const [at, setAt] = useState<number | null>(null);   // 가리킨 거래일(0 = 뉴스 전 거래일)
  // 칸의 실제 너비로 그린다 (글자가 늘어나거나 줄지 않게)
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [W, setW] = useState(360);
  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const watch = new ResizeObserver(([entry]) => setW(Math.max(260, Math.round(entry.contentRect.width))));
    watch.observe(box);
    return () => watch.disconnect();
  }, [data]);
  const H = Math.round(Math.min(230, Math.max(160, W * 0.42)));
  const ids = companies.map((c) => c.id);
  const key = `${day}:${ids.join(",")}`;
  useEffect(() => {
    setData(null);
    setFailed(false);
    setAt(null);
    fetchMemoPrices(day, ids)
      .then(setData)
      .catch(() => setFailed(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const view = useMemo(() => {
    if (!data || data.days.length === 0) return null;
    const lines = data.companies.map((c) => ({ ...c, values: [0, ...c.series] }));
    const market = [0, ...data.market];
    const median = [0, ...(data.median ?? data.market.map(() => null))];
    const all = [...market, ...median, ...lines.flatMap((l) => l.values)].filter((v): v is number => v !== null);
    const lo = Math.min(0, ...all);
    const hi = Math.max(0, ...all);
    const pad = Math.max(1, (hi - lo) * 0.08);
    const y0 = lo - pad;
    const y1 = hi + pad;
    const n = market.length;
    const x = (i: number) => PAD.left + (i / (n - 1)) * (W - PAD.left - PAD.right);
    const y = (v: number) => PAD.top + ((y1 - v) / (y1 - y0)) * (H - PAD.top - PAD.bottom);
    const path = (values: (number | null)[]) =>
      values.reduce((d, v, i) => (v === null ? d : `${d}${d === "" || values[i - 1] === null ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`), "");
    return { lines, market, median, n, x, y, path, grid: ticks(y0, y1) };
  }, [data, W, H]);

  if (failed) return null;
  if (!data) return <p className="muted small">뉴스 뒤 주가 변화를 불러오는 중…</p>;
  if (!view) return <p className="muted small">뉴스 뒤 주가 변화: {data.note ?? "시세가 없습니다"}</p>;

  const roleOf = new Map(companies.map((c) => [c.id, c.role]));
  const shown = focus ?? view.lines[0]?.company_id ?? null;
  const i = at ?? view.n - 1;
  const dayLabel = (k: number) => (k === 0 ? `${data.base_day!.slice(5)} 전일` : k === 1 ? `${data.days[0].slice(5)} 뉴스 날` : `${data.days[k - 1].slice(5)} · ${k - 1}거래일 뒤`);
  const mkt = view.market[i];
  const mid = view.median[i];
  const subjects = view.lines.filter((l) => roleOf.get(l.company_id) === "주인공");
  const related = view.lines.filter((l) => roleOf.get(l.company_id) !== "주인공");
  const avg = (rows: typeof view.lines) => {
    const vals = rows.map((r) => r.values[view.n - 1]).filter((v): v is number => v !== null);
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  };
  const last = view.n - 2;   // 뉴스 날 뒤 거래일 수

  const move = (event: React.PointerEvent<SVGRectElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - box.left) / box.width;
    const px = PAD.left + ratio * (W - PAD.left - PAD.right);
    let best = 0;
    for (let k = 1; k < view.n; k++) if (Math.abs(view.x(k) - px) < Math.abs(view.x(best) - px)) best = k;
    setAt(best);
  };

  return (
    <section className="memo-sec price-moves">
      <h4>뉴스 뒤 실제 주가 변화</h4>
      <p className="price-sum">
        뉴스 날부터 {last > 0 ? `${last}거래일 뒤까지` : "하루"}{" "}
        {subjects.length > 0 && <>주인공 {subjects.map((s) => `${s.name} ${signed(s.values[view.n - 1])}`).join(", ")}, </>}
        {related.length > 0 && <>이어진 종목 {related.length}곳 평균 {signed(avg(related))}, </>}
        보통 종목 {signed(view.median[view.n - 1])}(시장 전체 {signed(view.market[view.n - 1])}).
      </p>
      <div ref={boxRef}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="price-chart" role="img"
           aria-label={`뉴스 전 거래일 종가 대비 누적 수익률. ${dayLabel(view.n - 1)} 기준 보통 종목 ${signed(view.median[view.n - 1])}`}>
        {view.grid.map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={W - PAD.right} y1={view.y(v)} y2={view.y(v)} className={v === 0 ? "pc-zero" : "pc-grid"} />
            <text x={PAD.left - 5} y={view.y(v) + 3.5} className="pc-tick" textAnchor="end">
              {v > 0 ? `+${v}` : v}%
            </text>
          </g>
        ))}
        <line x1={view.x(1)} x2={view.x(1)} y1={PAD.top} y2={H - PAD.bottom} className="pc-news" />
        <text x={view.x(1) + 3} y={PAD.top + 8} className="pc-tick">뉴스 날</text>
        <text x={view.x(0)} y={H - 6} className="pc-tick" textAnchor="start">{data.base_day!.slice(5)}</text>
        <text x={view.x(view.n - 1)} y={H - 6} className="pc-tick" textAnchor="end">{data.days[data.days.length - 1].slice(5)}</text>
        {view.lines.filter((l) => l.company_id !== shown).map((l) => (
          <path key={l.company_id} d={view.path(l.values)} className="pc-other" />
        ))}
        <path d={view.path(view.market)} className="pc-market" />
        <path d={view.path(view.median)} className="pc-median" />
        {view.lines.filter((l) => l.company_id === shown).map((l) => (
          <g key={l.company_id}>
            <path d={view.path(l.values)} className="pc-focus" />
            {l.values[i] !== null && <circle cx={view.x(i)} cy={view.y(l.values[i]!)} r={4} className="pc-dot" />}
          </g>
        ))}
        {mid !== null && <circle cx={view.x(i)} cy={view.y(mid)} r={3} className="pc-mdot" />}
        {at !== null && <line x1={view.x(i)} x2={view.x(i)} y1={PAD.top} y2={H - PAD.bottom} className="pc-cross" />}
        <rect x={PAD.left} y={PAD.top} width={W - PAD.left - PAD.right} height={H - PAD.top - PAD.bottom} fill="transparent"
              onPointerMove={move} onPointerLeave={() => setAt(null)} />
      </svg>
      </div>
      <table className="price-table">
        <thead>
          <tr>
            <th>{dayLabel(i)}</th>
            <th>누적</th>
            <th title="같은 날 모든 상장 종목 수익률의 중앙값과의 차이">보통 종목 대비</th>
          </tr>
        </thead>
        <tbody>
          {view.lines.map((l) => (
            <tr key={l.company_id} className={l.company_id === shown ? "on" : undefined} onClick={() => setFocus(l.company_id)}
                title="눌러서 이 회사의 선을 강조합니다">
              <td>
                <i className={l.company_id === shown ? "sw sw-focus" : "sw"} aria-hidden />
                {l.name} <small>{roleOf.get(l.company_id)}</small>
                {l.company_id === shown && (
                  <button className="pc-open" onClick={(e) => { e.stopPropagation(); onCompany(l.company_id); }}>그래프</button>
                )}
              </td>
              <td>{signed(l.values[i])}</td>
              <td>{l.values[i] === null || mid === null ? "–" : signed(l.values[i]! - mid, "%p")}</td>
            </tr>
          ))}
          <tr className="mkt">
            <td>
              <i className="sw sw-median" aria-hidden />
              보통 종목 <small>수익률 중앙값</small>
            </td>
            <td>{signed(mid)}</td>
            <td />
          </tr>
          <tr className="mkt">
            <td>
              <i className="sw sw-market" aria-hidden />
              시장 전체 <small>시가총액 가중</small>
            </td>
            <td>{signed(mkt)}</td>
            <td>{mkt === null || mid === null ? "–" : signed(mkt - mid, "%p")}</td>
          </tr>
        </tbody>
      </table>
      <p className="muted small">
        뉴스 전 거래일({data.base_day!.slice(5)}) 종가를 0으로 둔 누적 수익률입니다. 보통 종목은 모든 상장 종목 수익률의 중앙값, 시장 전체는 시가총액 가중 평균입니다(삼성전자·SK하이닉스의 비중이 커서 둘이 크게 움직이면 보통 종목과 벌어집니다).
        뉴스가 난 뒤의 기록일 뿐, 공시 관계가 주가를 움직였다는 뜻이 아닙니다. 984건을 미리 정한 기준으로 시험했을 때 공시로 고른 종목이 키워드로 고른 종목보다
        낫지 않았습니다. 출처 {data.source}.
      </p>
    </section>
  );
}
