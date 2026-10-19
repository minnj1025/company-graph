import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { BarChart, CustomChart, GraphChart, SankeyChart, ScatterChart, TreemapChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import type { View } from "./types";

echarts.use([BarChart, CustomChart, GraphChart, SankeyChart, ScatterChart, TreemapChart, GridComponent, TooltipComponent, SVGRenderer]);

/**
 * Agent가 show_view 로 띄운 시각화 카드. 카드의 값은 서버가 DB에서 채운 것이고(company_graph/views.py),
 * 여기서는 그리기만 한다. 기업 이름을 누르면 오른쪽 그래프가 그 기업으로 바뀐다.
 */
const DART = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=";

type Who = { id: number | null; name: string; listed?: boolean };

/** 색은 화면의 색 변수를 그대로 쓴다. 바탕을 바꾸면 다시 그린다 */
function palette() {
  const css = getComputedStyle(document.documentElement);
  const get = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return {
    text: get("--text", "#e8edf5"),
    muted: get("--muted", "#8d99ab"),
    line: get("--line", "rgba(255,255,255,0.12)"),
    raised: get("--raised", "#24252a"),
    accent: get("--accent", "#7aa2ff"),
    up: get("--up", "#f0666b"),
    down: get("--down", "#5b9bf0"),
    good: get("--good", "#6fd08c"),
    info: get("--info", "#9ec1ff"),
    warn: get("--warn", "#f0b354"),
  };
}
type Palette = ReturnType<typeof palette>;

/** 원 → 읽기 쉬운 단위 */
export function won(value: number | null | undefined): string {
  if (value === null || value === undefined) return "–";
  const abs = Math.abs(value);
  if (abs >= 1e12) return `${(value / 1e12).toFixed(abs >= 1e13 ? 1 : 2)}조 원`;
  if (abs >= 1e8) return `${Math.round(value / 1e8).toLocaleString()}억 원`;
  if (abs >= 1e4) return `${Math.round(value / 1e4).toLocaleString()}만 원`;
  return `${value.toLocaleString()}원`;
}
const pct = (value: number | null | undefined, digits = 2) => (value === null || value === undefined ? "–" : `${Number(value).toFixed(digits).replace(/\.?0+$/, "")}%`);
const signed = (value: number | null | undefined) => (value === null || value === undefined ? "–" : `${value > 0 ? "+" : ""}${value.toFixed(2)}%`);

function useTheme(): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const watch = new MutationObserver(() => setTick((t) => t + 1));
    watch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => watch.disconnect();
  }, []);
  return tick;
}

/** echarts 를 그리는 칸. option 은 색표를 받아 만든다 */
function Chart({ height, make, onPick }: { height: number; make: (p: Palette) => echarts.EChartsCoreOption; onPick?: (data: unknown) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  useEffect(() => {
    if (!box.current) return;
    const chart = echarts.init(box.current, undefined, { renderer: "svg" });
    chart.setOption(make(palette()));
    if (onPick) chart.on("click", (event) => onPick(event.data));
    const sized = new ResizeObserver(() => chart.resize());
    sized.observe(box.current);
    return () => {
      sized.disconnect();
      chart.dispose();
    };
    // make 와 onPick 은 카드마다 한 번 정해지므로, 바탕이 바뀔 때만 다시 그린다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme]);
  return <div ref={box} className="view-chart" style={{ height }} />;
}

const tooltip = (p: Palette) => ({
  confine: true,
  backgroundColor: p.raised,
  borderColor: p.line,
  textStyle: { color: p.text, fontSize: 12 },
  extraCssText: "box-shadow:none;border-radius:6px;",
});

/* ── 1. 지분 구조도: 위로 주주(와 그 위), 가운데 이 회사, 아래로 출자 대상 ── */
function Ownership({ view, onCompany }: CardProps) {
  const holders = view.holders as (Who & { pct: number; role?: string; up: (Who & { pct: number })[] })[];
  const kids = view.investees as (Who & { pct: number; purpose?: string })[];
  const make = (p: Palette): echarts.EChartsCoreOption => {
    type Node = { id: string; name: string; x: number; y: number; company: number | null; kind: string };
    const nodes: Node[] = [];
    const links: { source: string; target: string; value: number }[] = [];
    const spread = (count: number, i: number) => (count <= 1 ? 50 : 6 + (88 * i) / (count - 1));
    const top = holders.flatMap((h) => h.up.map((u) => ({ ...u, below: h })));
    top.forEach((u, i) => nodes.push({ id: `u${i}`, name: u.name, x: spread(top.length, i), y: 0, company: u.id, kind: "up" }));
    holders.forEach((h, i) => {
      nodes.push({ id: `h${i}`, name: h.name, x: spread(holders.length, i), y: 1, company: h.id, kind: "holder" });
      links.push({ source: `h${i}`, target: "c", value: h.pct });
      top.forEach((u, j) => u.below === h && links.push({ source: `u${j}`, target: `h${i}`, value: u.pct }));
    });
    nodes.push({ id: "c", name: view.company.name, x: 50, y: 2, company: view.company.id, kind: "center" });
    const rows = top.length ? 3 : 2;
    return {
      tooltip: { ...tooltip(p), formatter: (e: { dataType: string; data: { name?: string; value?: number } }) => (e.dataType === "edge" ? `지분 ${pct(e.data.value)}` : e.data.name ?? "") },
      series: [
        {
          type: "graph",
          layout: "none",
          left: 24,
          right: 24,
          top: 28,
          bottom: 28,
          roam: false,
          data: nodes.map((n) => ({
            ...n,
            x: n.x,
            symbolSize: n.kind === "center" ? 18 : 10,
            y: (top.length ? n.y : n.y - 1) * (100 / (rows - 1)),
            itemStyle: { color: n.kind === "center" ? p.accent : n.company ? p.info : p.muted },
            label: {
              show: true,
              position: n.kind === "center" ? "bottom" : "top",
              color: n.kind === "center" ? p.text : p.muted,
              fontSize: n.kind === "center" ? 13 : 11,
              fontWeight: n.kind === "center" ? 600 : 400,
              width: 90,
              overflow: "truncate",
            },
          })),
          links: links.map((l) => ({ ...l, lineStyle: { width: 0.8 + Math.min(4, l.value / 15), color: p.muted, opacity: 0.55 }, label: { show: true, formatter: pct(l.value), color: p.muted, fontSize: 10 } })),
          edgeSymbol: ["none", "arrow"],
          edgeSymbolSize: 6,
          emphasis: { focus: "adjacency" },
        },
      ],
    };
  };
  return (
    <>
      {holders.length > 0 ? (
        <Chart height={holders.some((h) => h.up.length) ? 260 : 190} make={make} onPick={(d) => pick(d, onCompany)} />
      ) : (
        <p className="view-empty">사업보고서의 최대주주 현황에 주주가 없습니다.</p>
      )}
      <p className="view-legend">
        주주와 지분율(선) → {view.company.name}
        {(view.more_holders as number) > 0 && ` · 주주 ${view.more_holders}곳 더`}
      </p>
      {kids.length > 0 && (
        <>
          <p className="view-sub">출자 대상 (상장사 먼저, 지분율 큰 순)</p>
          <div className="view-bars">
            {kids.map((k) => (
              <button key={`${k.name}-${k.pct}`} className="view-bar" disabled={!k.id} onClick={() => k.id && onCompany(k.id)} title={k.purpose ?? undefined}>
                <span className="name">{k.name}</span>
                <span className="track">
                  <i style={{ width: `${Math.min(100, k.pct)}%` }} />
                </span>
                <span className="num">{pct(k.pct)}</span>
              </button>
            ))}
          </div>
          {(view.more_investees as number) > 0 && <p className="view-legend">출자 대상 {String(view.more_investees)}곳 더</p>}
        </>
      )}
    </>
  );
}

/* ── 2. 계열 지도: 상장 계열사는 시가총액 크기의 칸, 비상장은 이름 목록 ── */
function Group({ view, onCompany }: CardProps) {
  const members = view.members as (Who & { market_cap: number | null; self?: boolean })[];
  const listed = members.filter((m) => m.listed && m.market_cap);
  const others = members.filter((m) => !m.listed);
  const make = (p: Palette): echarts.EChartsCoreOption => ({
    tooltip: { ...tooltip(p), formatter: (e: { data: { name: string; value: number } }) => `${e.data.name}<br/>시가총액 ${won(e.data.value)}` },
    series: [
      {
        type: "treemap",
        roam: false,
        nodeClick: false,
        breadcrumb: { show: false },
        left: 0,
        right: 0,
        top: 0,
        bottom: 0,
        itemStyle: { borderColor: p.raised, borderWidth: 2, gapWidth: 2 },
        label: { color: "#fff", fontSize: 12, formatter: (e: { data: { name: string; value: number } }) => `${e.data.name}\n${won(e.data.value)}` },
        data: listed.map((m) => ({ name: m.name, value: m.market_cap, company: m.id, itemStyle: { color: m.self ? p.accent : p.info, opacity: m.self ? 1 : 0.75 } })),
      },
    ],
  });
  return (
    <>
      {listed.length > 0 && <Chart height={260} make={make} onPick={(d) => pick(d, onCompany)} />}
      {others.length > 0 && (
        <p className="view-list">
          <b>비상장 {others.length}곳</b> {others.slice(0, 18).map((m) => m.name).join(", ")}
          {others.length > 18 && ` 외 ${others.length - 18}곳`}
        </p>
      )}
    </>
  );
}

/* ── 3. 공급망 흐름: 판 쪽(왼쪽) → 산 쪽(오른쪽), 굵기는 계약금액의 합 ── */
function SupplyFlow({ view }: CardProps) {
  const flows = view.flows as { from: Who; to: Who; amount: number; count: number }[];
  if (flows.length === 0) return <p className="view-empty">이 기간에 공시한 공급계약이 없습니다.</p>;
  const make = (p: Palette): echarts.EChartsCoreOption => {
    // 같은 회사가 판 쪽과 산 쪽에 다 나오면 고리가 생기므로, 산 쪽 이름에는 보이지 않는 표시를 붙여 따로 둔다
    const right = (name: string) => `${name}​`;
    const names = new Set<string>();
    flows.forEach((f) => {
      names.add(f.from.name);
      names.add(right(f.to.name));
    });
    return {
      tooltip: { ...tooltip(p), formatter: (e: { dataType: string; data: { source?: string; target?: string; value: number; count?: number; name?: string } }) => (e.dataType === "edge" ? `${e.data.source} → ${e.data.target}<br/>${won(e.data.value)} · ${e.data.count}건` : e.data.name ?? "") },
      series: [
        {
          type: "sankey",
          left: 8,
          right: 160,
          top: 8,
          bottom: 8,
          nodeWidth: 10,
          nodeGap: 10,
          draggable: false,
          label: { color: p.text, fontSize: 11, width: 150, overflow: "truncate" },
          itemStyle: { color: p.info, borderWidth: 0 },
          lineStyle: { color: "source", opacity: 0.28 },
          data: [...names].map((name) => ({ name, itemStyle: { color: name === view.company.name || name === right(view.company.name) ? p.accent : p.info } })),
          links: flows.map((f) => ({ source: f.from.name, target: right(f.to.name), value: f.amount, count: f.count })),
        },
      ],
    };
  };
  return (
    <>
      <Chart height={Math.max(220, Math.min(460, 28 * flows.length))} make={make} />
      <p className="view-legend">
        {(view.period as { from: string }).from} 이후 · 왼쪽이 판 쪽, 오른쪽이 산 쪽{(view.more as number) > 0 && ` · ${view.more}쌍 더`}
      </p>
    </>
  );
}

/* ── 4. 연결 경로: 경로마다 한 줄 ── */
function Path({ view, onCompany }: CardProps) {
  const routes = view.routes as { from: Who; to: Who; type: string; value: number | null; unit: string | null; rcept: string | null }[][];
  if (routes.length === 0) return <p className="view-empty">세 단계 안에 공시로 이어지는 경로가 없습니다.</p>;
  const name = (who: Who) =>
    who.id ? (
      <button className="view-node" onClick={() => onCompany(who.id!)}>
        {who.name}
      </button>
    ) : (
      <span className="view-node plain">{who.name}</span>
    );
  return (
    <div className="view-paths">
      {routes.map((route, i) => {
        // 경로의 선은 방향이 섞여 있으므로, 앞 단계와 이어지는 쪽을 찾아 한 줄로 편다
        let at = view.company.id;
        return (
          <div key={i} className="view-path">
            {route.map((step, j) => {
              const forward = step.from.id === at;
              const here = forward ? step.from : step.to;
              const next = forward ? step.to : step.from;
              at = next.id ?? at;
              return (
                <span key={j} className="view-step">
                  {j === 0 && name(here)}
                  <a className="view-edge" href={step.rcept ? DART + step.rcept : undefined} target="_blank" rel="noreferrer">
                    {forward ? "→" : "←"} {step.type}
                    {step.value !== null && step.unit === "%" ? ` ${pct(step.value)}` : step.value !== null ? ` ${won(step.value)}` : ""}
                  </a>
                  {name(next)}
                </span>
              );
            })}
          </div>
        );
      })}
      {(view.total as number) > routes.length && <p className="view-legend">경로 {String(view.total)}개 가운데 {routes.length}개</p>}
    </div>
  );
}

/* ── 5. 공시 타임라인: 종류별 줄, 원의 크기는 금액 ── */
const LANES = ["공급계약", "정정", "해지", "지분 취득", "지분 처분"];
function Timeline({ view }: CardProps) {
  const events = view.events as { date: string; category: string; report: string; counterparty?: string; title?: string; amount: number | null; superseded?: boolean; rcept: string }[];
  if (events.length === 0) return <p className="view-empty">이 기간의 공시가 없습니다.</p>;
  const top = Math.max(1, ...events.map((e) => e.amount ?? 0));
  const lanes = LANES.filter((lane) => events.some((e) => e.category === lane));
  const make = (p: Palette): echarts.EChartsCoreOption => ({
    tooltip: {
      ...tooltip(p),
      formatter: (e: { data: { event: (typeof events)[number] } }) => {
        const v = e.data.event;
        return `${v.date} · ${v.category}<br/>${v.counterparty ?? ""}${v.title ? ` · ${v.title}` : ""}<br/>${v.amount ? won(v.amount) : ""}${v.superseded ? "<br/>(뒤에 정정·해지됨)" : ""}`;
      },
    },
    grid: { left: 70, right: 16, top: 12, bottom: 28 },
    xAxis: { type: "time", axisLabel: { color: p.muted, fontSize: 10 }, axisLine: { lineStyle: { color: p.line } }, splitLine: { show: false } },
    yAxis: { type: "category", data: lanes, axisLabel: { color: p.muted, fontSize: 11 }, axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: true, lineStyle: { color: p.line } } },
    series: [
      {
        type: "scatter",
        data: events
          .filter((e) => lanes.includes(e.category))
          .map((e) => ({
            value: [e.date, e.category],
            event: e,
            symbolSize: 6 + 22 * Math.sqrt((e.amount ?? 0) / top),
            itemStyle: { color: e.category === "해지" ? p.up : e.category === "정정" ? p.warn : e.category.startsWith("지분") ? p.good : p.info, opacity: e.superseded ? 0.3 : 0.8 },
          })),
      },
    ],
  });
  return (
    <>
      <Chart height={60 + 44 * lanes.length} make={make} onPick={(d) => window.open(DART + (d as { event: { rcept: string } }).event.rcept, "_blank")} />
      <p className="view-legend">원을 누르면 공시 원문이 열립니다 · 흐린 원은 뒤에 정정·해지된 공시</p>
    </>
  );
}

/* ── 6. 계약 기간: 계약마다 시작~종료 막대 ── */
function Contracts({ view }: CardProps) {
  const bars = view.bars as { side: string; counterparty: string; title?: string; start: string; end: string; amount: number | null; ratio_pct?: string; rcept: string }[];
  if (bars.length === 0) return <p className="view-empty">계약기간이 적힌 공급계약이 없습니다.</p>;
  const labels = bars.map((b) => `${b.counterparty}${b.title ? ` · ${b.title}` : ""}`);
  const make = (p: Palette): echarts.EChartsCoreOption => ({
    tooltip: {
      ...tooltip(p),
      formatter: (e: { dataIndex: number }) => {
        const b = bars[e.dataIndex];
        return `${b.side} · ${b.counterparty}<br/>${b.title ?? ""}<br/>${b.start} ~ ${b.end}<br/>${won(b.amount)}${b.ratio_pct ? ` (매출액 대비 ${b.ratio_pct}%)` : ""}`;
      },
    },
    grid: { left: 160, right: 16, top: 8, bottom: 28 },
    xAxis: { type: "time", axisLabel: { color: p.muted, fontSize: 10 }, axisLine: { lineStyle: { color: p.line } }, splitLine: { lineStyle: { color: p.line } } },
    yAxis: { type: "category", data: labels, inverse: true, axisLabel: { color: p.muted, fontSize: 11, width: 150, overflow: "truncate" }, axisLine: { show: false }, axisTick: { show: false } },
    series: [
      {
        type: "custom",
        renderItem: (params: { coordSys: unknown }, api: { value: (i: number) => number; coord: (v: [number, number]) => number[]; size: (v: [number, number]) => number[]; style: (s: object) => object }) => {
          const start = api.coord([api.value(0), api.value(2)]);
          const end = api.coord([api.value(1), api.value(2)]);
          const height = api.size([0, 1])[1] * 0.56;
          return { type: "rect", shape: { x: start[0], y: start[1] - height / 2, width: Math.max(2, end[0] - start[0]), height }, style: api.style({}) };
        },
        encode: { x: [0, 1], y: 2 },
        data: bars.map((b, i) => ({ value: [b.start, b.end, i], itemStyle: { color: b.side === "판매" ? p.info : p.good, opacity: 0.85 } })),
      },
    ],
  });
  return (
    <>
      <Chart height={44 + 26 * bars.length} make={make} onPick={(d) => window.open(DART + bars[(d as { value: number[] }).value[2]].rcept, "_blank")} />
      <p className="view-legend">파랑: 판 계약 · 초록: 산 계약 · 막대를 누르면 공시 원문{(view.more as number) > 0 && ` · ${view.more}건 더`}</p>
    </>
  );
}

/* ── 7. 매출 구성 ── */
function Revenue({ view }: CardProps) {
  const rows = (view.rows as { name: string; segment?: string; share_pct: number | null; family?: string }[]).filter((r) => r.share_pct !== null).sort((a, b) => (b.share_pct ?? 0) - (a.share_pct ?? 0));
  if (rows.length === 0) return <p className="view-empty">매출 비중이 적힌 제품 표가 없습니다.</p>;
  const make = (p: Palette): echarts.EChartsCoreOption => ({
    tooltip: { ...tooltip(p), formatter: (e: { dataIndex: number }) => `${rows[e.dataIndex].name}${rows[e.dataIndex].segment ? ` (${rows[e.dataIndex].segment})` : ""}<br/>${pct(rows[e.dataIndex].share_pct)}` },
    grid: { left: 130, right: 56, top: 8, bottom: 8 },
    xAxis: { type: "value", max: 100, show: false },
    yAxis: { type: "category", data: rows.map((r) => r.name), inverse: true, axisLabel: { color: p.text, fontSize: 12, width: 120, overflow: "truncate" }, axisLine: { show: false }, axisTick: { show: false } },
    series: [
      {
        type: "bar",
        data: rows.map((r) => r.share_pct),
        barWidth: 14,
        itemStyle: { color: p.accent, borderRadius: 3 },
        showBackground: true,
        backgroundStyle: { color: p.line, borderRadius: 3 },
        label: { show: true, position: "right", color: p.text, fontSize: 11, formatter: (e: { value: number }) => pct(e.value, 1) },
      },
    ],
  });
  return (
    <>
      <Chart height={20 + 26 * rows.length} make={make} />
      <p className="view-legend">{String(view.report ?? "")}</p>
    </>
  );
}

/* ── 8. 수익률 비교 ── */
function Returns({ view }: CardProps) {
  const periods = (view.periods as { period: string; change_pct?: number | null; market_median_pct?: number | null; peers_median_pct?: number | null; peers_counted?: number }[]).filter((x) => x.change_pct !== undefined);
  const peers = view.peers as { total: number; products: string[] } | null;
  if (periods.length === 0) return <p className="view-empty">보관한 시세로 계산할 수 없습니다.</p>;
  const make = (p: Palette): echarts.EChartsCoreOption => {
    const bar = (name: string, key: "change_pct" | "market_median_pct" | "peers_median_pct", color: string) => ({
      name,
      type: "bar",
      barGap: "15%",
      barMaxWidth: 26,
      data: periods.map((x) => x[key] ?? null),
      itemStyle: { color, borderRadius: 2 },
      label: { show: key === "change_pct", position: "top", color: p.text, fontSize: 11, formatter: (e: { value: number }) => signed(e.value) },
    });
    return {
      tooltip: { ...tooltip(p), trigger: "axis", valueFormatter: (v: number) => signed(v) },
      legend: { show: false },
      grid: { left: 40, right: 12, top: 24, bottom: 24 },
      xAxis: { type: "category", data: periods.map((x) => x.period), axisLabel: { color: p.text, fontSize: 12 }, axisLine: { lineStyle: { color: p.line } }, axisTick: { show: false } },
      yAxis: { type: "value", axisLabel: { color: p.muted, fontSize: 10, formatter: (v: number) => `${v}%` }, splitLine: { lineStyle: { color: p.line } } },
      series: [bar(view.company.name, "change_pct", p.accent), bar("시장 중앙값", "market_median_pct", p.muted), ...(peers && peers.total > 0 ? [bar("같은 제품 종목", "peers_median_pct", p.info)] : [])],
    };
  };
  return (
    <>
      <Chart height={240} make={make} />
      <p className="view-legend">
        <i className="dot accent" /> {view.company.name} <i className="dot muted" /> 시장 중앙값
        {peers && peers.total > 0 && (
          <>
            {" "}
            <i className="dot info" /> 같은 제품 종목 {peers.total}곳 ({peers.products.slice(0, 3).join(", ")})
          </>
        )}{" "}
        · {String(view.last_trading_day)} 종가 기준
      </p>
    </>
  );
}

type CardProps = { view: View; onCompany: (id: number) => void };
const pick = (data: unknown, onCompany: (id: number) => void) => {
  const id = (data as { company?: number | null } | undefined)?.company;
  if (id) onCompany(id);
};
const CARDS: Record<string, (props: CardProps) => React.ReactElement> = {
  ownership: Ownership,
  group: Group,
  supply_flow: SupplyFlow,
  path: Path,
  timeline: Timeline,
  contracts: Contracts,
  revenue: Revenue,
  returns: Returns,
};

function Card({ view, onCompany }: CardProps) {
  const Body = CARDS[view.kind];
  const [open, setOpen] = useState(false);
  if (!Body) return null;
  return (
    <figure className="view-card">
      <figcaption>
        <b>{view.title}</b>
        <span>{view.as_of} 기준</span>
      </figcaption>
      <Body view={view} onCompany={onCompany} />
      {(view.notes.length > 0 || view.sources.length > 0) && (
        <div className="view-foot">
          {view.notes.map((note) => (
            <p key={note}>{note}</p>
          ))}
          {view.sources.length > 0 && (
            <button className="view-sources" onClick={() => setOpen(!open)}>
              근거 공시 {view.sources.length}건 {open ? "▴" : "▾"}
            </button>
          )}
          {open && (
            <p className="view-receipts">
              {view.sources.map((no) => (
                <a key={no} href={DART + no} target="_blank" rel="noreferrer">
                  {no}
                </a>
              ))}
            </p>
          )}
        </div>
      )}
    </figure>
  );
}

export default function Views({ views, onCompany }: { views: View[]; onCompany: (id: number) => void }) {
  return (
    <div className="views">
      {views.map((view, i) => (
        <Card key={`${view.kind}-${i}`} view={view} onCompany={onCompany} />
      ))}
    </div>
  );
}
