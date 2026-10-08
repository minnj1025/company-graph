import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchCompany, fetchGraph, fetchMeta, fetchOverview, type Scope } from "./api";
import { categoryColors, legend, LINK_COLORS, LINK_LABELS, type ColorBy } from "./colors";
import { Cards } from "./Cards";
import { Chat } from "./Chat";
import { Controls } from "./Controls";
import { Graph, shortName } from "./Graph";
import { Credit, DataPage, QuestionsPage } from "./Pages";
import { Panel } from "./Panel";
import type { AskResult, Company, CompanyDetail, GraphData, GraphNode, LinkType, Meta, RelType } from "./types";

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
  /** Agent가 찾은 결과. 있으면 그래프에 그 기업과 관계만 남긴다 */
  const [found, setFound] = useState<AskResult | null>(null);
  const [showControls, setShowControls] = useState(true);
  /** 오른쪽 칸의 너비. 왼쪽 가장자리를 끌어 바꾸고, 다음에 와도 그대로 둔다 */
  const [sideWidth, setSideWidth] = useState(() => Number(localStorage.getItem("side-width")) || 400);
  const resizeSide = useCallback((event: React.PointerEvent) => {
    event.preventDefault();
    const move = (e: PointerEvent) => {
      const width = Math.round(Math.min(Math.max(window.innerWidth - e.clientX - 10, 320), window.innerWidth * 0.6));
      setSideWidth(width);
      localStorage.setItem("side-width", String(width));
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      document.body.classList.remove("resizing");
    };
    document.body.classList.add("resizing");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }, []);
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

  const shownData = found ? found.graph : data;
  const groups = useMemo(() => categoryColors(shownData.nodes, colorBy), [shownData, colorBy]);
  const onSelect = useCallback((node: GraphNode | null) => setSelectedId(node ? node.id : null), []);
  const drawnTypes = useMemo(() => {
    const present = new Set<LinkType>(shownData.links.map((link) => link.type));
    return (Object.keys(LINK_LABELS) as LinkType[]).filter((type) => present.has(type));
  }, [shownData]);
  const scopeLabel = found
    ? "질문으로 찾은 결과"
    : center
      ? `${shortName(center.name)} 중심`
      : scope === "listed"
        ? "상장사 전체"
        : scope === "focus"
          ? "자동차 가치사슬"
          : scope.split(":")[1];

  if (!meta || !asOf) return <div className="loading">{error ?? "불러오는 중…"}</div>;

  return (
    <div className="app">
      <header className="top">
        <h1>
          기업 관계 그래프 <span>공시에서 뽑은 기업 사이의 관계</span>
        </h1>
        <nav className="tabs">
          <button className={tab === "graph" ? "on" : ""} onClick={() => setTab("graph")}>
            탐색
          </button>
          <button className={tab === "data" ? "on" : ""} onClick={() => setTab("data")}>
            데이터와 검증
          </button>
          <button className={tab === "agent" ? "on" : ""} onClick={() => setTab("agent")}>
            평가 문항
          </button>
        </nav>
        <div className="top-stat">
          기업 {meta.companies.toLocaleString()}곳 · 최근 공시 {meta.last_date}
        </div>
      </header>

      <main className="work" style={{ "--side": `${sideWidth}px` } as React.CSSProperties}>
        <section className="stage">
          <Graph
            data={shownData}
            colorBy={colorBy}
            groups={groups}
            selectedId={selectedId}
            onSelect={onSelect}
            fitKey={found ? `ask-${found.question}` : `${center?.id ?? scope}-${hops}`}
          />

          {found ? (
            <div className="found">
              <div>
                <b>질문으로 찾은 것만 보는 중</b>
                <span>{found.question}</span>
              </div>
              <button onClick={() => setFound(null)}>전체 그래프로</button>
            </div>
          ) : showControls ? (
            <div className="left">
              <button className="fold" onClick={() => setShowControls(false)}>
                접기
              </button>
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
          ) : (
            <button className="unfold" onClick={() => setShowControls(true)}>
              조건 · {scopeLabel} · {asOf}
            </button>
          )}

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
              기업 {shownData.nodes.length}곳 · 선 {shownData.links.length}개 · 점을 끌어 옮기면 그 자리에 고정됩니다
            </div>
          </div>
          {error && <div className="toast">{error}</div>}
        </section>

        <Cards asOf={asOf} data={shownData} scopeLabel={scopeLabel} onOpen={setSelectedId} />

        <aside className="side">
          <div className="side-grip" onPointerDown={resizeSide} onDoubleClick={() => setSideWidth(400)} title="끌어서 너비 바꾸기 (두 번 누르면 처음 크기)" />
          <div className={detail ? "side-pane hidden" : "side-pane"}>
            <div className="side-head">
              <b>질문하기</b>
              <span>Agent가 공시 DB를 조회해 답합니다</span>
            </div>
            <Chat shown={found} onShow={setFound} />
          </div>
          {detail && (
            <Panel
              detail={detail}
              onOpen={setSelectedId}
              onCenter={(id) => {
                const node = shownData.nodes.find((n) => n.id === id) ?? detail.company;
                setFound(null);
                setCenter(node);
              }}
              onClose={() => setSelectedId(null)}
            />
          )}
        </aside>
      </main>
      <Credit />

      {tab !== "graph" && <div className="overlay">{tab === "data" ? <DataPage meta={meta} /> : <QuestionsPage />}</div>}
    </div>
  );
}
