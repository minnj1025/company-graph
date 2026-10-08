import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchCompany, fetchGraph, fetchMeta, fetchOverview, type Scope } from "./api";
import { categoryColors, legend, LINK_COLORS, LINK_LABELS, type ColorBy } from "./colors";
import { Controls } from "./Controls";
import { Graph, shortName } from "./Graph";
import { AgentPage, DataPage } from "./Pages";
import { Panel } from "./Panel";
import type { Company, CompanyDetail, GraphData, GraphNode, Meta, RelType } from "./types";

const EMPTY: GraphData = { nodes: [], links: [] };

export function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [types, setTypes] = useState<RelType[]>(["equity", "supply_contract"]);
  const [colorBy, setColorBy] = useState<ColorBy>("group");
  const [center, setCenter] = useState<Company | null>(null);
  const [hops, setHops] = useState(1);
  const [scope, setScope] = useState<Scope>("listed");
  const [tab, setTab] = useState<"graph" | "data" | "agent">("graph");
  const [data, setData] = useState<GraphData>(EMPTY);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<CompanyDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const positions = useRef(new Map<number, { x: number; y: number; z: number }>());

  useEffect(() => {
    fetchMeta()
      .then((m) => {
        setMeta(m);
        setAsOf(m.last_date);
      })
      .catch(() => setError("서버에 연결하지 못했습니다. API 서버(8000번)가 떠 있는지 확인해 주세요."));
  }, []);

  useEffect(() => {
    if (!asOf) return;
    let cancelled = false;
    const request = center ? fetchGraph(center.id, asOf, types, hops) : fetchOverview(asOf, types, scope);
    request
      .then((next) => {
        if (cancelled) return;
        // 날짜나 조건을 바꿔도 점이 제자리에 있게, 앞선 화면의 위치를 이어 준다
        for (const node of next.nodes) {
          const at = positions.current.get(node.id);
          if (at) Object.assign(node, at);
        }
        setData(next);
        setError(null);
      })
      .catch(() => !cancelled && setError("그래프를 불러오지 못했습니다."));
    return () => {
      cancelled = true;
    };
  }, [asOf, types, center, hops, scope]);

  // 그려진 점의 위치를 기억해 둔다 (라이브러리가 점 객체에 x, y, z를 직접 적는다)
  useEffect(() => {
    const timer = setInterval(() => {
      for (const node of data.nodes) {
        if (node.x !== undefined) positions.current.set(node.id, { x: node.x, y: node.y ?? 0, z: node.z ?? 0 });
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [data]);

  useEffect(() => {
    if (selectedId === null || !asOf) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    fetchCompany(selectedId, asOf).then((d) => !cancelled && setDetail(d));
    return () => {
      cancelled = true;
    };
  }, [selectedId, asOf]);

  const groups = useMemo(() => categoryColors(data.nodes, colorBy), [data, colorBy]);
  const onSelect = useCallback((node: GraphNode | null) => setSelectedId(node ? node.id : null), []);
  const drawnTypes = center ? types : types.filter((t) => t !== "affiliate");

  if (!meta || !asOf) return <div className="loading">{error ?? "불러오는 중…"}</div>;

  return (
    <div className="app">
      <Graph
        data={data}
        colorBy={colorBy}
        groups={groups}
        selectedId={selectedId}
        onSelect={onSelect}
        fitKey={`${center?.id ?? scope}-${hops}`}
      />

      <div className="left">
        <h1>
          기업 관계 그래프 <span>공시 기반 · {scope === "listed" ? "상장사 전체" : scope === "focus" ? "자동차 가치사슬" : scope.split(":")[1]}</span>
        </h1>
        <Controls
          firstDate={meta.first_date}
          lastDate={meta.last_date}
          asOf={asOf}
          onAsOf={setAsOf}
          types={types}
          onTypes={setTypes}
          colorBy={colorBy}
          onColorBy={setColorBy}
          centerName={center ? shortName(center.name) : null}
          hops={hops}
          onHops={setHops}
          onOverview={() => setCenter(null)}
          scope={scope}
          categories={meta.categories}
          onScope={setScope}
          onPick={(company) => {
            setCenter(company);
            setSelectedId(company.id);
          }}
        />
      </div>

      <div className="legend">
        <div>
          {drawnTypes.map((type) => (
            <span key={type}>
              <i className="line" style={{ background: LINK_COLORS[type] }} />
              {LINK_LABELS[type]}
            </span>
          ))}
        </div>
        <div>
          {legend(colorBy, groups).map(([name, color]) => (
            <span key={name}>
              <i className="dot" style={{ background: color }} />
              {name}
            </span>
          ))}
        </div>
        <div className="stat">
          기업 {data.nodes.length}곳 · 선 {data.links.length}개 · 점을 끌어 옮기면 그 자리에 고정됩니다
        </div>
        <div className="stat">{meta.coverage?.notice}</div>
      </div>

      {error && <div className="toast">{error}</div>}

      <nav className="tabs">
        <button className={tab === "graph" ? "on" : ""} onClick={() => setTab("graph")}>
          그래프
        </button>
        <button className={tab === "data" ? "on" : ""} onClick={() => setTab("data")}>
          데이터와 검증
        </button>
        <button className={tab === "agent" ? "on" : ""} onClick={() => setTab("agent")}>
          Agent 예시
        </button>
      </nav>
      {tab !== "graph" && <div className="overlay">{tab === "data" ? <DataPage meta={meta} /> : <AgentPage />}</div>}

      {detail && (
        <Panel
          detail={detail}
          onOpen={setSelectedId}
          onCenter={(id) => {
            const node = data.nodes.find((n) => n.id === id) ?? detail.company;
            setCenter(node);
          }}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  );
}
