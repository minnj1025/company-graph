import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchCompany, fetchGraph, fetchMeta, fetchOverview, type Scope } from "./api";
import { categoryColors, legend, LINK_COLORS, LINK_LABELS, type ColorBy } from "./colors";
import { Cards } from "./Cards";
import { ChatLog, Composer, useChat } from "./Chat";
import { NO_FILTER, Settings, TimeBar, type Filters } from "./Controls";
import { Graph, MENTIONED, shortName } from "./Graph";
import { Credit, DataPage, QuestionsPage } from "./Pages";
import { Panel } from "./Panel";
import type { AskResult, Company, CompanyDetail, GraphData, GraphNode, LinkType, Meta, RelType } from "./types";

const EMPTY: GraphData = { nodes: [], links: [] };

/** 오른쪽에서 밀려 나오는 칸에 무엇을 보일지. 닫혀 있으면 그래프가 화면을 다 쓴다 */
type Drawer = "answer" | "company" | "feed" | null;
/** 그래프가 무엇을 그리고 있는지. 바뀔 때마다 쌓아 두어 앞뒤로 오갈 수 있다 (브라우저의 뒤로·앞으로와 같다) */
interface View {
  /** 한 기업 중심으로 볼 때 그 기업 */
  center: Company | null;
  hops: number;
  scope: Scope;
  /** Agent가 찾은 결과. 있으면 그래프에 그 기업과 관계만 남긴다 */
  found: AskResult | null;
}
const HOME: View = { center: null, hops: 1, scope: "listed", found: null };

const DRAWER_TITLES: Record<Exclude<Drawer, null>, string> = { answer: "질문과 답", company: "기업 상세", feed: "최근 공시" };

export function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [types, setTypes] = useState<RelType[]>(["equity", "supply_contract"]);
  const [colorBy, setColorBy] = useState<ColorBy>("group");
  const [views, setViews] = useState<{ list: View[]; at: number }>({ list: [HOME], at: 0 });
  const { center, hops, scope, found } = views.list[views.at];
  const [tab, setTab] = useState<"graph" | "data" | "agent">("graph");
  const [filters, setFilters] = useState<Filters>(NO_FILTER);
  const [loading, setLoading] = useState(false);
  /** "화면 맞추기"를 누를 때마다 올린다. 그래프가 전체가 보이게 다시 맞춘다 */
  const [refit, setRefit] = useState(0);
  const [drawer, setDrawer] = useState<Drawer>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [data, setData] = useState<GraphData>(EMPTY);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<CompanyDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const positions = useRef(new Map<number, { x: number; y: number; z: number }>());
  const picked = useRef(selectedId);
  picked.current = selectedId;
  /** 그래프가 그리는 것을 바꾼다. replace 면 지금 자리를 고치고(단계 수처럼 작은 변화), 아니면 새 자리를 쌓는다 */
  const go = useCallback((patch: Partial<View>, replace = false) => {
    setViews((now) => {
      const current = now.list[now.at];
      const next = { ...current, ...patch };
      if ((Object.keys(next) as (keyof View)[]).every((key) => next[key] === current[key])) return now;
      if (replace) return { list: now.list.map((view, i) => (i === now.at ? next : view)), at: now.at };
      const list = [...now.list.slice(0, now.at + 1), next].slice(-40);
      return { list, at: list.length - 1 };
    });
  }, []);
  const step = useCallback((by: number) => {
    setViews((now) => ({ list: now.list, at: Math.min(Math.max(now.at + by, 0), now.list.length - 1) }));
    setSelectedId(null);
    setDrawer((open) => (open === "company" ? null : open));
  }, []);
  const showFound = useCallback((result: AskResult | null) => go({ found: result }), [go]);
  const chat = useChat(showFound);

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
    setLoading(true);
    request
      .then((next) => {
        if (cancelled) return;
        setLoading(false);
        // 날짜나 조건을 바꿔도 점이 제자리에 있게, 앞선 화면의 위치를 이어 준다
        for (const node of next.nodes) {
          const at = positions.current.get(node.id);
          if (at) Object.assign(node, at);
        }
        setData(next);
        setError(null);
      })
      .catch(() => {
        if (cancelled) return;
        setLoading(false);
        setError("그래프를 불러오지 못했습니다.");
      });
    return () => {
      cancelled = true;
    };
  }, [asOf, types, center, hops, scope]);

  // 그려진 점의 위치를 기억해 둔다 (라이브러리가 점 객체에 x, y, z를 직접 적는다)
  useEffect(() => {
    const timer = setInterval(() => {
      // 기업을 고른 동안에는 이어진 점들이 한곳에 모여 있다. 그 자리를 원래 자리로 기억하면 안 된다
      if (picked.current !== null) return;
      for (const node of data.nodes) {
        if (node.x !== undefined) positions.current.set(node.id, { x: node.x, y: node.y ?? 0, z: node.z ?? 0 });
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [data]);

  useEffect(() => {
    if (selectedId === null || selectedId < 0 || !asOf) {   // 음수는 사업 낱말이나 제품 점이다
      setDetail(null);
      return;
    }
    let cancelled = false;
    fetchCompany(selectedId, asOf).then((d) => !cancelled && setDetail(d));
    return () => {
      cancelled = true;
    };
  }, [selectedId, asOf]);

  // 솎아 보기: 받은 그래프에서 조건에 맞는 선과, 그 선에 닿은 기업만 남긴다. 질문으로 찾은 결과에는 걸지 않는다
  const filtered = useMemo(() => {
    if (filters === NO_FILTER) return data;
    const end = (side: number | GraphNode) => (typeof side === "number" ? side : side.id);
    let links = data.links.filter((link) =>
      link.type === "equity"
        ? (link.value ?? 0) >= filters.minPct
        : link.type === "affiliate" || link.type === "product" || link.type === "family" || (link.value ?? 0) >= filters.minAmount,
    );
    const degree = new Map<number, number>();
    for (const link of links) for (const id of [end(link.source), end(link.target)]) degree.set(id, (degree.get(id) ?? 0) + 1);
    const keep = new Set(data.nodes.filter((node) => node.focus || (degree.get(node.id) ?? 0) >= filters.minDegree).map((node) => node.id));
    links = links.filter((link) => keep.has(end(link.source)) && keep.has(end(link.target)));
    const linked = new Set(links.flatMap((link) => [end(link.source), end(link.target)]));
    return { nodes: data.nodes.filter((node) => node.focus || linked.has(node.id)).map((node) => ({ ...node, degree: degree.get(node.id) ?? 0 })), links };
  }, [data, filters]);
  const shownData = found ? found.graph : filtered;
  const groups = useMemo(() => categoryColors(shownData.nodes, colorBy), [shownData, colorBy]);
  const filtering = filters !== NO_FILTER && (filters.minPct > 0 || filters.minAmount > 0 || filters.minDegree > 1);
  /** 전체 그래프가 아니라 좁혀서 보고 있는가 */
  const narrowed = Boolean(found || center || filtering);
  const backToAll = useCallback(() => {
    go({ found: null, center: null });
    setFilters(NO_FILTER);
    setSelectedId(null);
  }, [go]);
  const closeDrawer = useCallback(() => {
    setDrawer(null);
    setSelectedId(null);
  }, []);
  // Esc: 펼친 설정, 오른쪽 칸, 고른 기업 순으로 닫고, 다 닫혀 있으면 전체 그래프로 돌아간다
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || tab !== "graph") return;
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (settingsOpen) setSettingsOpen(false);
      else if (drawer !== null) closeDrawer();
      else if (selectedId !== null) setSelectedId(null);
      else if (narrowed) backToAll();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedId, narrowed, backToAll, tab, drawer, settingsOpen, closeDrawer]);
  const openCompany = useCallback((id: number) => {
    setSelectedId(id);
    setDrawer("company");
  }, []);
  const onSelect = useCallback((node: GraphNode | null) => {
    setSelectedId(node ? node.id : null);
    // 낱말 점과 제품 점은 기업이 아니라서 상세를 열지 않고, 이어진 기업만 밝힌다
    if (node && !node.kind) setDrawer("company");
    else setDrawer((now) => (now === "company" ? null : now));
  }, []);
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

  const companies = shownData.nodes.filter((node) => !node.kind).length;
  const products = shownData.nodes.filter((node) => node.kind === "product").length;
  const families = shownData.nodes.filter((node) => node.kind === "family").length;
  const drawerTabs = (["answer", "company", "feed"] as const).filter(
    (kind) => kind === "feed" || kind === drawer || (kind === "answer" ? chat.turns.length > 0 : detail !== null),
  );

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

      <main className={drawer ? "explore open" : "explore"}>
        <section className="stage">
          <Graph
            data={shownData}
            colorBy={colorBy}
            groups={groups}
            selectedId={selectedId}
            onSelect={onSelect}
            fitKey={found ? `ask-${found.question}` : `${center?.id ?? scope}-${hops}`}
            refit={refit}
          />

          <div className="stage-top">
            <div className="stage-bar">
              <div className="history">
                <button onClick={() => step(-1)} disabled={views.at === 0} title="앞서 보던 그래프로" aria-label="뒤로">
                  ←
                </button>
                <button onClick={() => step(1)} disabled={views.at === views.list.length - 1} title="다시 그다음 그래프로" aria-label="앞으로">
                  →
                </button>
              </div>
              <div className={narrowed ? "crumb narrowed" : "crumb"}>
                <em>{found ? "질문으로 찾은 것" : center ? "한 기업 중심" : filtering ? "솎아 보는 중" : "보는 범위"}</em>
                <b>{found ? found.question : scopeLabel}</b>
                {!found && filtering && center && <em>· 솎아 보는 중</em>}
              </div>
              {narrowed && (
                <button className="primary" onClick={backToAll} title="질문 결과, 한 기업 중심 보기, 솎아 보기를 모두 풀고 전체 그래프로 돌아갑니다 (Esc)">
                  ← 전체 그래프로
                </button>
              )}
              <span className="spacer" />
              <TimeBar firstDate={meta.first_date} lastDate={meta.last_date} asOf={asOf} onAsOf={setAsOf} />
              <div className="settings-anchor">
                <button
                  className={settingsOpen ? "on" : ""}
                  onClick={() => setSettingsOpen(!settingsOpen)}
                  aria-expanded={settingsOpen}
                  title="관계 종류, 솎아 보기, 보는 범위, 점 색"
                >
                  보기 설정 {settingsOpen ? "▴" : "▾"}
                </button>
                {settingsOpen && (
                  <div className="settings">
                    <Settings
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
                      onHops={(n) => go({ hops: n }, true)}
                      onOverview={() => go({ center: null })}
                      scope={scope}
                      categories={meta.categories}
                      onScope={(value) => go({ scope: value })}
                      locked={found !== null}
                      filters={filters}
                      onFilters={setFilters}
                      shown={{ nodes: filtered.nodes.length, links: filtered.links.length }}
                    />
                  </div>
                )}
              </div>
              {chat.turns.length > 0 && (
                <button className={drawer === "answer" ? "on" : ""} onClick={() => setDrawer(drawer === "answer" ? null : "answer")}>
                  질문과 답 {chat.turns.length}
                </button>
              )}
              <button className={drawer === "feed" ? "on" : ""} onClick={() => setDrawer(drawer === "feed" ? null : "feed")}>
                최근 공시
              </button>
              <button onClick={() => setRefit((n) => n + 1)} title="그래프 전체가 보이게 화면을 다시 맞춥니다">
                화면 맞추기
              </button>
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
              {shownData.nodes.some((node) => node.mentioned) && (
                <div>
                  <span>
                    <i className="tag" style={{ background: MENTIONED }} />
                    이름표에 바탕이 있는 기업은 답에 나온 기업
                  </span>
                </div>
              )}
              <div className="stat">
                기업 {companies}곳{families > 0 && ` · 제품군 ${families}개`}
                {products > 0 && ` · 제품 ${products}개`} · 선 {shownData.links.length}개
              </div>
            </div>
          </div>
          {loading && !found && <div className="busy">그래프를 불러오는 중…</div>}
          {!loading && shownData.nodes.length === 0 && (
            <div className="stage-note">
              <div>
                이 조건에 맞는 관계가 없습니다.
                <br />
                조회 시점을 뒤로 옮기거나 솎아 보기 조건을 풀어 보세요.
                <br />
                {narrowed && <button onClick={backToAll}>전체 그래프로</button>}
              </div>
            </div>
          )}

          {error && <div className="toast">{error}</div>}

          <Composer
            chat={chat}
            examples={chat.turns.length === 0 && drawer === null}
            onAsk={() => setDrawer("answer")}
            onReopen={chat.turns.length > 0 && drawer !== "answer" ? () => setDrawer("answer") : null}
            onPick={(company: Company) => {
              go({ found: null, center: company });
              openCompany(company.id);
            }}
          />
        </section>

        <aside className="drawer" aria-hidden={drawer === null}>
          <div className="drawer-inner">
            <div className="drawer-head">
              {drawerTabs.map((kind) => (
                <button key={kind} className={drawer === kind ? "on" : ""} onClick={() => setDrawer(kind)}>
                  {DRAWER_TITLES[kind]}
                </button>
              ))}
              <span className="spacer" />
              <button className="close" onClick={closeDrawer} title="이 칸을 닫고 그래프를 넓게 봅니다 (Esc)" aria-label="닫기">
                ×
              </button>
            </div>
            {drawer === "answer" && <ChatLog chat={chat} shown={found} onShow={showFound} />}
            {drawer === "company" &&
              (detail ? (
                <Panel
                  detail={detail}
                  onOpen={openCompany}
                  onCenter={(id) => {
                    const node = shownData.nodes.find((n) => n.id === id) ?? detail.company;
                    go({ found: null, center: node });
                  }}
                  onClose={closeDrawer}
                />
              ) : (
                <p className="empty drawer-note">
                  {selectedId === null ? "그래프에서 기업을 누르거나 아래 입력 칸에서 찾으면 여기에 나옵니다." : "불러오는 중…"}
                </p>
              ))}
            {drawer === "feed" && <Cards asOf={asOf} data={shownData} scopeLabel={scopeLabel} onOpen={openCompany} />}
          </div>
        </aside>
      </main>
      <Credit />

      {tab !== "graph" && <div className="overlay">{tab === "data" ? <DataPage meta={meta} /> : <QuestionsPage />}</div>}
    </div>
  );
}
