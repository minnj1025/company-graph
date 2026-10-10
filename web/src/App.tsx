import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchCompany, fetchGraph, fetchMeta, fetchOverview, fetchPick, fetchTaxonomy, type Level, type Scope } from "./api";
import { categoryColors, CLASS_COLOR, FAMILY_COLOR, legend, LINK_COLORS, LINK_LABELS, PRODUCT_COLOR, TOPIC_COLOR, type ColorBy } from "./colors";
import { Cards } from "./Cards";
import { ChatLog, Composer, useChat } from "./Chat";
import { LevelList, NO_FILTER, Settings, TimeBar, type Filters } from "./Controls";
import { Graph, MENTIONED, shortName } from "./Graph";
import { HotPage } from "./Hot";
import { Credit, DataPage, QuestionsPage } from "./Pages";
import { Panel } from "./Panel";
import type { AskResult, Company, CompanyDetail, GraphData, GraphNode, LinkType, Meta, RelType, Suggestion, Taxonomy } from "./types";

/** 그래프에 따로 띄운 것: Agent의 답이 찾은 결과이거나, 입력 칸에서 고른 제품·제품군·분류 */
type Shown = Pick<AskResult, "question" | "graph"> & { picked?: boolean };

const EMPTY: GraphData = { nodes: [], links: [] };

/** 오른쪽에서 밀려 나오는 칸에 무엇을 보일지. 닫혀 있으면 그래프가 화면을 다 쓴다 */
type Drawer = "answer" | "company" | "feed" | null;
/** 그래프가 무엇을 그리고 있는지. 바뀔 때마다 쌓아 두어 앞뒤로 오갈 수 있다 (브라우저의 뒤로·앞으로와 같다) */
interface View {
  /** 한 기업 중심으로 볼 때 그 기업 */
  center: Company | null;
  hops: number;
  scope: Scope;
  /** Agent가 찾은 결과나 고른 제품. 있으면 그래프에 그 기업과 관계만 남긴다 */
  found: Shown | null;
  /** 고른 점. 점을 누르는 것도 한 걸음으로 쌓아서, 뒤로 가면 바로 앞의 행동으로 돌아간다 */
  selected: number | null;
}
const HOME: View = { center: null, hops: 1, scope: "listed", found: null, selected: null };

/** 기업이 아닌 점의 색. 화면에 그 점이 있을 때만 범례에 나온다 */
const KIND_LEGEND: [NonNullable<GraphNode["kind"]>, string, string][] = [
  ["class", "공식 분류", CLASS_COLOR],
  ["family", "제품군", FAMILY_COLOR],
  ["product", "제품", PRODUCT_COLOR],
  ["topic", "사업 낱말", TOPIC_COLOR],
];

const DRAWER_TITLES: Record<Exclude<Drawer, null>, string> = { answer: "채팅", company: "기업 상세", feed: "최근 공시" };

export function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [types, setTypes] = useState<RelType[]>(["equity", "supply_contract"]);
  const [colorBy, setColorBy] = useState<ColorBy>("group");
  const [views, setViews] = useState<{ list: View[]; at: number }>({ list: [HOME], at: 0 });
  const { center, hops, scope, found, selected: selectedId } = views.list[views.at];
  // 바탕: 어두운 것이 기본이고, 고른 것은 이 브라우저에 적어 둔다
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    try {
      return localStorage.getItem("theme") === "light" ? "light" : "dark";
    } catch {
      return "dark";
    }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("theme", theme);
    } catch {
      /* 적어 두지 못해도 이번에는 바뀐다 */
    }
  }, [theme]);
  const [tab, setTab] = useState<"graph" | "hot" | "data" | "agent">("graph");
  const [filters, setFilters] = useState<Filters>(NO_FILTER);
  const [loading, setLoading] = useState(false);
  /** "화면 맞추기"를 누를 때마다 올린다. 그래프가 전체가 보이게 다시 맞춘다 */
  const [refit, setRefit] = useState(0);
  const [drawer, setDrawer] = useState<Drawer>(null);
  const [drawerWidth, setDrawerWidth] = useState(520);
  const [level, setLevel] = useState<Level>("family");
  const [settingsOpen, setSettingsOpen] = useState(false);
  /** 보기 설정에서 옆에 목록을 펴 둔 제품 범주 */
  const [listLevel, setListLevel] = useState<Level | null>(null);
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null);
  // 보기 설정은 바깥(그래프, 다른 단추)을 누르면 닫힌다
  useEffect(() => {
    if (!settingsOpen) return;
    const onDown = (event: PointerEvent) => {
      if ((event.target as HTMLElement | null)?.closest(".settings-anchor")) return;
      setSettingsOpen(false);
      setListLevel(null);
    };
    window.addEventListener("pointerdown", onDown, true);
    return () => window.removeEventListener("pointerdown", onDown, true);
  }, [settingsOpen]);
  useEffect(() => {
    if (listLevel && !taxonomy) fetchTaxonomy().then(setTaxonomy).catch(() => undefined);
  }, [listLevel, taxonomy]);
  const [data, setData] = useState<GraphData>(EMPTY);
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
      // 그리는 것이 바뀌면 고른 점은 푼다 (같이 정해 준 경우는 빼고)
      if (!("selected" in patch) && (next.center !== current.center || next.found !== current.found || next.scope !== current.scope)) next.selected = null;
      if ((Object.keys(next) as (keyof View)[]).every((key) => next[key] === current[key])) return now;
      if (replace) return { list: now.list.map((view, i) => (i === now.at ? next : view)), at: now.at };
      const list = [...now.list.slice(0, now.at + 1), next].slice(-40);
      return { list, at: list.length - 1 };
    });
  }, []);
  const setSelectedId = useCallback((id: number | null) => go({ selected: id }), [go]);
  const step = useCallback(
    (by: number) => {
      const at = Math.min(Math.max(views.at + by, 0), views.list.length - 1);
      const to = views.list[at].selected;
      setViews({ list: views.list, at });
      // 그때 기업을 골라 두었으면 상세도 다시 연다
      setDrawer((open) => (to !== null && to >= 0 ? "company" : open === "company" ? null : open));
    },
    [views],
  );
  /** 찾은 그래프의 점을 전체 그래프에서 있던 자리에 놓는다. 그러면 화면이 한 번에 바뀌지 않고, 그 점들이 제자리에서 모여든다 */
  const seed = useCallback((graph: GraphData) => {
    const fresh = graph.nodes.filter((node) => node.x === undefined);
    const known = fresh.map((node) => positions.current.get(node.id)).filter((at) => at !== undefined);
    if (known.length === 0) return;
    const middle = { x: 0, y: 0, z: 0 };
    for (const at of known) for (const axis of ["x", "y", "z"] as const) middle[axis] += at[axis] / known.length;
    for (const node of fresh) {
      // 전체 그래프에 없던 점(제품 점, 비상장사)은 모여드는 한가운데 근처에서 나온다
      const at = positions.current.get(node.id);
      Object.assign(node, at ?? { x: middle.x + (Math.random() - 0.5) * 40, y: middle.y + (Math.random() - 0.5) * 40, z: middle.z + (Math.random() - 0.5) * 40 });
    }
  }, []);
  const showFound = useCallback(
    (result: AskResult | null) => {
      if (result) seed(result.graph);
      go({ found: result });
    },
    [go, seed],
  );
  /** 제품·제품군·분류를 골랐다: 그것을 파는 기업 전부를 그린다 (Agent를 부르지 않는다) */
  const showPicked = useCallback(
    (item: Suggestion) => {
      if (!asOf) return;
      const label = { product: "제품", family: "제품군", class: "공식 분류" }[item.kind];
      fetchPick(item.kind, item.key, asOf)
        .then((graph) => {
          seed(graph);
          go({ found: { question: `${label} · ${item.name}`, graph, picked: true } });
        })
        .catch(() => setError("그 제품을 파는 기업을 불러오지 못했습니다."));
    },
    [asOf, go, seed],
  );
  /** 오른쪽 칸이 열려 있을 때 그 경계를 끌어 너비를 바꾼다 */
  const dragEdge = (event: React.PointerEvent) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = drawer ? drawerWidth : 0;
    let moved = false;
    const move = (e: PointerEvent) => {
      const dx = startX - e.clientX;
      if (Math.abs(dx) > 4) moved = true;
      if (!moved) return;
      setDrawerWidth(Math.round(Math.min(Math.max(startWidth + dx, 340), Math.min(760, window.innerWidth * 0.6))));
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      document.body.classList.remove("resizing");
    };
    document.body.classList.add("resizing");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  };
  // Agent가 답하는 도중에 "이 기업들을 보여 줘"라고 하면, 지금 그래프에서 그 점들만 밝게 남긴다. 답이 끝나면 찾은 그래프로 넘어간다
  const [pointed, setPointed] = useState<Set<number> | null>(null);
  const chat = useChat(
    (result) => {
      setPointed(null);
      showFound(result);
    },
    (ids) => setPointed(new Set(ids)),
  );

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
    const request = center ? fetchGraph(center.id, asOf, types, hops, level) : fetchOverview(asOf, types, scope, level);
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
  }, [asOf, types, center, hops, scope, level]);

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
  // 찾은 그래프로 넘어갈 때는 먼저 지금 그래프에서 그 점들만 밝게 남기고(나머지는 흐려진다), 잠깐 뒤에 바꾼다
  const [drawn, setDrawn] = useState<Shown | null>(found);
  const [spotlight, setSpotlight] = useState<Set<number> | null>(null);
  useEffect(() => {
    if (!found) {
      setDrawn(null);
      setSpotlight(null);
      return;
    }
    setSpotlight(new Set(found.graph.nodes.map((node) => node.id)));
    const timer = setTimeout(() => {
      setDrawn(found);
      setSpotlight(null);
    }, 520);
    return () => clearTimeout(timer);
  }, [found]);
  const shownData = drawn ? drawn.graph : filtered;
  // 전체에서 기업 수가 많은 순서. 이 순서의 앞쪽 분류는 어느 화면에서나 같은 색을 받는다
  const colorOrder = useMemo(() => (meta ? meta.categories[colorBy === "sector" ? "sector" : "group"].map((c) => c.name) : []), [meta, colorBy]);
  const groups = useMemo(() => categoryColors(shownData.nodes, colorBy, colorOrder), [shownData, colorBy, colorOrder]);
  const filtering = filters !== NO_FILTER && (filters.minPct > 0 || filters.minAmount > 0 || filters.minDegree > 1);
  /** 전체 그래프가 아니라 좁혀서 보고 있는가 */
  const narrowed = Boolean(found || center || filtering);
  const backToAll = useCallback(() => {
    go({ found: null, center: null, selected: null });
    setFilters(NO_FILTER);
  }, [go]);
  const closeDrawer = useCallback(() => {
    setDrawer(null);
    setSelectedId(null);
  }, [setSelectedId]);
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
  }, [setSelectedId]);
  const onSelect = useCallback((node: GraphNode | null) => {
    setSelectedId(node ? node.id : null);
    // 낱말 점과 제품 점은 기업이 아니라서 상세를 열지 않고, 이어진 기업만 밝힌다
    if (node && !node.kind) setDrawer("company");
    else setDrawer((now) => (now === "company" ? null : now));
  }, [setSelectedId]);
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
    (kind) => kind !== "company" || kind === drawer || detail !== null,
  );

  return (
    <div className="app">
      <header className="top">
        <h1>
          {/* 기업(점)을 잇는 길이 C 모양을 이룬다 */}
          <svg className="logo" width="22" height="22" viewBox="0 0 32 32" aria-hidden="true">
            <path d="M22.4 9.2A9.4 9.4 0 1 0 22.4 22.8" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
            <circle cx="6.6" cy="16" r="3" fill="currentColor" />
            <circle cx="22.4" cy="9.2" r="3" fill="currentColor" />
            <circle cx="22.4" cy="22.8" r="3.2" fill="var(--accent)" />
          </svg>
          Corpath <span>공시에서 뽑은 기업 사이의 관계</span>
        </h1>
        <nav className="tabs">
          <button className={tab === "graph" ? "on" : ""} onClick={() => setTab("graph")}>
            탐색
          </button>
          <button className={tab === "hot" ? "on" : ""} onClick={() => setTab("hot")}>
            이슈 종목
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
          <button
            className="theme-toggle"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            title={theme === "dark" ? "밝은 바탕으로" : "어두운 바탕으로"}
            aria-label={theme === "dark" ? "밝은 바탕으로" : "어두운 바탕으로"}
          >
            {theme === "dark" ? (
              <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
                <circle cx="10" cy="10" r="3.6" fill="currentColor" />
                <path d="M10 1.5v2.6M10 15.9v2.6M1.5 10h2.6M15.9 10h2.6M4 4l1.8 1.8M14.2 14.2 16 16M16 4l-1.8 1.8M5.8 14.2 4 16" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
            ) : (
              <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
                <path d="M16.5 12.2A7 7 0 0 1 7.8 3.5a7 7 0 1 0 8.7 8.7Z" fill="currentColor" />
              </svg>
            )}
          </button>
        </div>
      </header>

      <main className={drawer ? "explore open" : "explore"}>
        <section className="stage">
          <Graph
            data={shownData}
            colorBy={colorBy}
            groups={groups}
            selectedId={drawn === found ? selectedId : null}
            onSelect={onSelect}
            spotlight={spotlight ?? pointed}
            light={theme === "light"}
            fitKey={drawn ? `ask-${drawn.question}` : `${center?.id ?? scope}-${hops}`}
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
                <em>{found ? (found.picked ? "고른 것" : "질문으로 찾은 것") : center ? "한 기업 중심" : filtering ? "솎아 보는 중" : "보는 범위"}</em>
                <b>{found ? found.question : scopeLabel}</b>
                {!found && filtering && center && <em>· 솎아 보는 중</em>}
              </div>
              {narrowed && (
                <button className="reset" onClick={backToAll} title="전체 그래프로 돌아갑니다 (Esc)" aria-label="전체 그래프로">
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
                    <path d="M2 2l8 8M10 2l-8 8" />
                  </svg>
                </button>
              )}
              <div className="bar-right">
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
                      level={level}
                      listed={listLevel}
                      onLevel={(value) => setListLevel((now) => (now === value ? null : value))}
                      filters={filters}
                      onFilters={setFilters}
                      shown={{ nodes: filtered.nodes.length, links: filtered.links.length }}
                    />
                  </div>
                )}
                {settingsOpen && listLevel && (
                  <LevelList
                    level={listLevel}
                    taxonomy={taxonomy}
                    onAll={() => {
                      setLevel(listLevel);
                      setTypes((now) => (now.includes("product") ? now : [...now, "product"]));
                      go({ found: null });
                      setListLevel(null);
                    }}
                    onPick={(item) => {
                      showPicked(item);
                      setListLevel(null);
                      setSettingsOpen(false);
                    }}
                  />
                )}
              </div>
              <button className={drawer === "feed" ? "feed-button on" : "feed-button"} onClick={() => setDrawer(drawer === "feed" ? null : "feed")}>
                최근 공시
              </button>
              <button
                className={drawer === "answer" ? "chat-button on" : "chat-button"}
                onClick={() => setDrawer(drawer === "answer" ? null : "answer")}
                title="채팅"
                aria-label="채팅"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" aria-hidden="true">
                  <path d="M2.5 3.5h11v7.5H7.2L4.3 13.4V11H2.5z" />
                </svg>
              </button>
              <button onClick={() => setRefit((n) => n + 1)} title="그래프 전체가 보이게 화면을 다시 맞춥니다">
                화면 맞추기
              </button>
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

          <div className="stage-bottom">
            <Composer
              chat={chat}
              examples={chat.turns.length === 0 && drawer === null}
              onAsk={() => setDrawer("answer")}
              onPickProduct={showPicked}
              onPick={(company: Company) => {
                go({ found: null, center: company, selected: company.id });
                setDrawer("company");
              }}
            />
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
              {shownData.nodes.some((node) => node.kind) && (
                <div>
                  {KIND_LEGEND.filter(([kind]) => shownData.nodes.some((node) => node.kind === kind)).map(([kind, name, color]) => (
                    <span key={kind}>
                      <i className="dot" style={{ background: color }} />
                      {name}
                    </span>
                  ))}
                </div>
              )}
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
          {drawer && <div className="edge open" onPointerDown={dragEdge} title="끌어서 오른쪽 칸의 너비를 바꿉니다" />}
        </section>

        <aside className="drawer" aria-hidden={drawer === null} style={{ "--drawer-width": `${drawerWidth}px` } as React.CSSProperties}>
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
            {drawer === "answer" && <ChatLog
                chat={chat}
                shown={found as AskResult | null}
                onShow={showFound}
                onCompany={(result, id) => go({ found: result, selected: id })}
              />}
            {drawer === "company" &&
              (detail ? (
                <Panel
                  detail={detail}
                  onOpen={openCompany}
                  onCenter={(id) => {
                    const node = shownData.nodes.find((n) => n.id === id) ?? detail.company;
                    go({ found: null, center: node, selected: id });
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

      {tab !== "graph" && (
        <div className="overlay">
          {tab === "hot" ? (
            <HotPage
              onShow={(title, graph) => {
                seed(graph);
                go({ found: { question: title, graph, picked: true } });
                setTab("graph");
              }}
            />
          ) : tab === "data" ? (
            <DataPage meta={meta} />
          ) : (
            <QuestionsPage />
          )}
        </div>
      )}
    </div>
  );
}
