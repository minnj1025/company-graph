import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchPortfolio,
  fetchView,
  searchCompanies,
  type PortfolioEvent,
  type PortfolioIssue,
  type PortfolioResult,
  type PortfolioStock,
} from "./api";
import { Credit } from "./Credit";
import type { Company, View } from "./types";

const Views = lazy(() => import("./Views"));

/** 내 종목: 보유 종목은 비중대로 상자 크기를, 등락률대로 색을 정한다. 목록은 이 브라우저에만 둔다 */
type Holding = { company: Company; qty: number | null };
type Saved = { holdings: Holding[]; watch: Company[] };
type Period = "d1" | "d5" | "d20";

const KEY = "my-stocks.v1";
const PERIODS: { key: Period; label: string; scale: number }[] = [
  { key: "d1", label: "1일", scale: 8 },
  { key: "d5", label: "5일", scale: 15 },
  { key: "d20", label: "20일", scale: 25 },
];
const WINDOWS = [7, 30, 90];
/** 상세에서 함께 보여 줄 카드 (Agent 없이 서버가 DB에서 채운다) */
const CARDS = ["returns", "financials", "holders", "payout"];

function load(): Saved {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? "null") as Saved | null;
    if (saved && Array.isArray(saved.holdings) && Array.isArray(saved.watch)) return saved;
  } catch {
    /* 저장소를 못 쓰는 브라우저(사생활 보호 창 등)면 빈 목록으로 시작한다 */
  }
  return { holdings: [], watch: [] };
}

function store(saved: Saved) {
  try {
    localStorage.setItem(KEY, JSON.stringify(saved));
  } catch {
    /* 저장하지 못해도 이 화면에서는 계속 쓸 수 있다 */
  }
}

const signed = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined ? "–" : `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
const tone = (value: number | null | undefined) => (value === null || value === undefined || value === 0 ? "" : value > 0 ? "up" : "down");
const newsSearch = (name: string) => `https://search.naver.com/search.naver?${new URLSearchParams({ where: "news", query: name, sort: "1" })}`;

/** 등락률 → 상자 바탕색. 크게 움직일수록 진하다. 기간마다 '진함의 끝'이 다르다 */
function fill(value: number | null | undefined, scale: number): string {
  if (value === null || value === undefined) return "var(--surface)";
  const strength = Math.min(1, Math.abs(value) / scale);
  const color = value >= 0 ? "var(--up)" : "var(--down)";
  return `color-mix(in srgb, ${color} ${Math.round(14 + strength * 62)}%, var(--surface))`;
}

interface Rect {
  id: number;
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 비중대로 상자를 나눈다 (squarified treemap). 좌표는 0~100 */
function squarify(items: { id: number; value: number }[], width = 100, height = 100): Rect[] {
  const total = items.reduce((sum, item) => sum + item.value, 0);
  if (total <= 0) return [];
  const scaled = items
    .filter((item) => item.value > 0)
    .sort((a, b) => b.value - a.value)
    .map((item) => ({ id: item.id, area: (item.value / total) * width * height }));
  const out: Rect[] = [];
  let x = 0;
  let y = 0;
  let w = width;
  let h = height;
  const worst = (row: number[], side: number) => {
    const sum = row.reduce((a, b) => a + b, 0);
    const max = Math.max(...row);
    const min = Math.min(...row);
    return Math.max((side * side * max) / (sum * sum), (sum * sum) / (side * side * min));
  };
  let row: typeof scaled = [];
  const lay = () => {
    const sum = row.reduce((a, b) => a + b.area, 0);
    if (w >= h) {
      const rowW = sum / h;
      let cy = y;
      for (const item of row) {
        const ih = item.area / rowW;
        out.push({ id: item.id, x, y: cy, w: rowW, h: ih });
        cy += ih;
      }
      x += rowW;
      w -= rowW;
    } else {
      const rowH = sum / w;
      let cx = x;
      for (const item of row) {
        const iw = item.area / rowH;
        out.push({ id: item.id, x: cx, y, w: iw, h: rowH });
        cx += iw;
      }
      y += rowH;
      h -= rowH;
    }
    row = [];
  };
  for (const item of scaled) {
    const side = Math.min(w, h);
    const next = [...row, item];
    if (row.length === 0 || worst(next.map((r) => r.area), side) <= worst(row.map((r) => r.area), side)) {
      row = next;
    } else {
      lay();
      row = [item];
    }
  }
  if (row.length) lay();
  return out;
}

function Spark({ values, wide = false }: { values: number[]; wide?: boolean }) {
  if (values.length < 2) return null;
  const width = wide ? 240 : 64;
  const height = wide ? 56 : 20;
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = max - min || 1;
  const point = (value: number, i: number) => `${((i / (values.length - 1)) * width).toFixed(1)},${(height - ((value - min) / span) * height).toFixed(1)}`;
  const last = values[values.length - 1];
  return (
    <svg className={`mine-spark ${tone(last)}`} viewBox={`0 0 ${width} ${height}`} width={width} height={height} aria-hidden="true">
      <line x1="0" x2={width} y1={height - ((0 - min) / span) * height} y2={height - ((0 - min) / span) * height} className="zero" />
      <polyline points={values.map(point).join(" ")} />
    </svg>
  );
}

/** 기업을 찾아 보유·관심에 더하는 칸 */
function AddBar({ onAdd }: { onAdd: (company: Company, kind: "hold" | "watch", qty: number | null) => void }) {
  const [text, setText] = useState("");
  const [found, setFound] = useState<Company[]>([]);
  const [picked, setPicked] = useState<Company | null>(null);
  const [kind, setKind] = useState<"hold" | "watch">("hold");
  const [qty, setQty] = useState("");
  useEffect(() => {
    const typed = text.trim();
    if (picked || typed.length < 1) {
      setFound([]);
      return;
    }
    const timer = setTimeout(() => {
      searchCompanies(typed)
        .then((rows) => setFound(rows.filter((row) => row.listed).slice(0, 8)))
        .catch(() => setFound([]));
    }, 180);
    return () => clearTimeout(timer);
  }, [text, picked]);
  const amount = Number(qty.replaceAll(",", ""));
  const ready = picked !== null && (kind === "watch" || (Number.isFinite(amount) && amount > 0));
  const submit = () => {
    if (!picked || !ready) return;
    onAdd(picked, kind, kind === "hold" ? amount : null);
    setText("");
    setPicked(null);
    setQty("");
  };
  return (
    <form
      className="mine-add"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <div className="mine-search">
        <input
          value={picked ? picked.name : text}
          onChange={(event) => {
            setPicked(null);
            setText(event.target.value);
          }}
          placeholder="종목 이름이나 종목코드"
          aria-label="더할 종목"
        />
        {found.length > 0 && (
          <ul className="mine-found" role="listbox">
            {found.map((company) => (
              <li key={company.id}>
                <button
                  type="button"
                  onClick={() => {
                    setPicked(company);
                    setFound([]);
                  }}
                >
                  <b>{company.name}</b>
                  <span>
                    {company.stock_code} · {company.market}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="mine-kind" role="group" aria-label="보유·관심">
        <button type="button" className={kind === "hold" ? "on" : ""} onClick={() => setKind("hold")}>
          보유
        </button>
        <button type="button" className={kind === "watch" ? "on" : ""} onClick={() => setKind("watch")}>
          관심
        </button>
      </div>
      {kind === "hold" && (
        <input className="mine-qty" inputMode="numeric" value={qty} onChange={(event) => setQty(event.target.value)} placeholder="수량(주)" aria-label="보유 수량" />
      )}
      <button type="submit" className="mine-submit" disabled={!ready}>
        더하기
      </button>
    </form>
  );
}

/** 보유 종목 상자 그림 */
function Treemap({ stocks, period, selected, onPick }: { stocks: PortfolioStock[]; period: (typeof PERIODS)[number]; selected: number | null; onPick: (id: number) => void }) {
  const rects = useMemo(() => squarify(stocks.map((stock) => ({ id: stock.id, value: stock.weight ?? 0 }))), [stocks]);
  const byId = new Map(stocks.map((stock) => [stock.id, stock]));
  if (rects.length === 0) return <p className="mine-empty">보유 종목을 더하면 비중대로 상자가 그려집니다.</p>;
  return (
    <div className="mine-map" role="list" aria-label="보유 종목 비중과 등락률">
      {rects.map((rect) => {
        const stock = byId.get(rect.id)!;
        const change = stock.move?.[period.key] ?? null;
        const small = rect.w * rect.h < 140;
        // 납작한 상자는 이름과 등락률을 한 줄에 둔다
        const flat = rect.h < 14;
        return (
          <button
            key={rect.id}
            role="listitem"
            className={`mine-tile ${selected === rect.id ? "on" : ""} ${small ? "small" : ""} ${flat ? "flat" : ""}`}
            style={{ left: `${rect.x}%`, top: `${rect.y}%`, width: `${rect.w}%`, height: `${rect.h}%`, background: fill(change, period.scale) }}
            onClick={() => onPick(rect.id)}
            title={`${stock.name} · 비중 ${stock.weight?.toFixed(1)}% · ${period.label} ${signed(change)}`}
          >
            <b>{stock.name}</b>
            <span className="mine-tile-change">{signed(change)}</span>
            {!small && !flat && <span className="mine-tile-weight">비중 {stock.weight?.toFixed(1)}%</span>}
          </button>
        );
      })}
    </div>
  );
}

/** 한 종목의 자세한 칸: 등락, 바로 가기, 카드, 그 종목의 소식 */
function Detail({
  stock,
  result,
  holding,
  onClose,
  onOpenCompany,
  onAsk,
}: {
  stock: PortfolioStock;
  result: PortfolioResult;
  holding: Holding | Company | undefined;
  onClose: () => void;
  onOpenCompany: (company: Company) => void;
  onAsk: (question: string) => void;
}) {
  const [views, setViews] = useState<View[] | null>(null);
  const box = useRef<HTMLElement>(null);
  useEffect(() => {
    box.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    let live = true;
    setViews(null);
    Promise.all(CARDS.map((kind) => fetchView(kind, stock.id, result.as_of).catch(() => null))).then((found) => {
      if (live) setViews(found.filter((view): view is View => view !== null && !String(view.summary ?? "").includes("없음")));
    });
    return () => {
      live = false;
    };
  }, [stock.id, result.as_of]);
  const company = holding && "company" in holding ? holding.company : (holding as Company | undefined);
  const events = result.events.filter((event) => event.company_id === stock.id);
  const issues = result.issues.filter((issue) => issue.company_id === stock.id);
  return (
    <section className="mine-detail" ref={box} aria-label={`${stock.name} 자세히`}>
      <header>
        <div>
          <h2>
            {stock.name} <span className="hot-market">{stock.market}</span>
          </h2>
          <p className="muted">
            {stock.stock_code}
            {stock.weight !== null && ` · 내 보유 비중 ${stock.weight.toFixed(1)}%`}
            {stock.move?.stale && ` · 마지막 시세 ${stock.move.day} (거래가 없었던 날이 있음)`}
          </p>
        </div>
        <button className="mine-close" onClick={onClose} aria-label="닫기">
          ×
        </button>
      </header>
      <div className="mine-detail-top">
        <dl className="mine-moves">
          {PERIODS.map((period) => (
            <div key={period.key}>
              <dt>{period.label}</dt>
              <dd className={tone(stock.move?.[period.key])}>{signed(stock.move?.[period.key])}</dd>
            </div>
          ))}
        </dl>
        {stock.move && (
          <figure className="mine-detail-spark">
            <Spark values={stock.move.spark} wide />
            <figcaption>최근 20거래일 누적 등락률</figcaption>
          </figure>
        )}
      </div>
      <div className="mine-actions">
        {company && <button onClick={() => onOpenCompany(company)}>그래프에서 관계 보기</button>}
        <button onClick={() => onAsk(`${stock.name}의 최근 1년 공시에서 바뀐 것을 정리해 줘`)}>Agent에게 최근 변화 묻기</button>
        <a href={newsSearch(stock.name)} target="_blank" rel="noreferrer">
          뉴스 검색 ↗
        </a>
      </div>
      <div className="mine-detail-body">
        <div className="mine-detail-feed">
          <h4>이 종목의 소식</h4>
          {events.length === 0 && issues.length === 0 && <p className="muted">이 기간에 공시나 이슈 기록이 없습니다.</p>}
          <Feed events={events} issues={issues} showCompany={false} onPick={() => {}} />
        </div>
        <div className="mine-detail-cards">
          <h4>카드</h4>
          {views === null ? (
            <p className="muted">불러오는 중…</p>
          ) : views.length === 0 ? (
            <p className="muted">보여 줄 카드가 없습니다.</p>
          ) : (
            <Suspense fallback={<p className="muted">불러오는 중…</p>}>
              <Views views={views} onCompany={() => {}} />
            </Suspense>
          )}
        </div>
      </div>
    </section>
  );
}

type Item = { day: string; event?: PortfolioEvent; issue?: PortfolioIssue };

/** 공시와 이슈를 날짜별로 묶은 목록 */
function Feed({ events, issues, showCompany, onPick }: { events: PortfolioEvent[]; issues: PortfolioIssue[]; showCompany: boolean; onPick: (id: number) => void }) {
  const days = useMemo(() => {
    const items: Item[] = [...events.map((event) => ({ day: event.date, event })), ...issues.map((issue) => ({ day: issue.day, issue }))];
    items.sort((a, b) => (a.day < b.day ? 1 : a.day > b.day ? -1 : a.issue ? -1 : 1));
    const grouped = new Map<string, Item[]>();
    for (const item of items) grouped.set(item.day, [...(grouped.get(item.day) ?? []), item]);
    return [...grouped.entries()];
  }, [events, issues]);
  return (
    <ol className="mine-feed">
      {days.map(([day, items]) => (
        <li key={day}>
          <time>{day}</time>
          <ul>
            {items.map((item, i) =>
              item.event ? (
                <li key={`e${i}`} className={`mine-event ${item.event.tone}`}>
                  <span className="mine-badge">{item.event.kind}</span>
                  <span className="mine-text">
                    {showCompany && (
                      <button className="mine-who" onClick={() => onPick(item.event!.company_id)}>
                        {item.event.company}
                      </button>
                    )}
                    {item.event.text}
                  </span>
                  <a href={item.event.url} target="_blank" rel="noreferrer" aria-label="공시 원문">
                    원문 ↗
                  </a>
                </li>
              ) : (
                <li key={`i${i}`} className={`mine-event issue issue-${item.issue!.side}`}>
                  <span className="mine-badge">{item.issue!.group === null ? (item.issue!.side === "up" ? "개별 급등" : "개별 급락") : item.issue!.side === "up" ? "연관 급등" : "연관 급락"}</span>
                  <span className="mine-text">
                    {showCompany && (
                      <button className="mine-who" onClick={() => onPick(item.issue!.company_id)}>
                        {item.issue!.company}
                      </button>
                    )}
                    <b className={item.issue!.side}>{signed(item.issue!.change)}</b>
                    {item.issue!.together.length > 0 && ` · 함께: ${item.issue!.together.slice(0, 3).join(", ")}${item.issue!.together.length > 3 ? ` 외 ${item.issue!.together.length - 3}` : ""}`}
                    {item.issue!.why.length > 0 && ` · 묶은 고리: ${item.issue!.why.slice(0, 3).join(", ")}`}
                    {item.issue!.reason && <em className="mine-reason">{item.issue!.reason}</em>}
                  </span>
                  {item.issue!.sources[0] ? (
                    <a href={item.issue!.sources[0].url} target="_blank" rel="noreferrer">
                      기사 ↗
                    </a>
                  ) : (
                    <a href={newsSearch(item.issue!.company)} target="_blank" rel="noreferrer">
                      검색 ↗
                    </a>
                  )}
                </li>
              ),
            )}
          </ul>
        </li>
      ))}
    </ol>
  );
}

export function PortfolioPage({ onOpenCompany, onAsk }: { onOpenCompany: (company: Company) => void; onAsk: (question: string) => void }) {
  const [saved, setSaved] = useState<Saved>(load);
  const [result, setResult] = useState<PortfolioResult | null>(null);
  const [error, setError] = useState(false);
  const [period, setPeriod] = useState<(typeof PERIODS)[number]>(PERIODS[0]);
  const [days, setDays] = useState(30);
  const [selected, setSelected] = useState<number | null>(null);
  const [filter, setFilter] = useState<"all" | "filings" | "issues">("all");
  const [editing, setEditing] = useState(false);

  const update = (next: Saved) => {
    setSaved(next);
    store(next);
  };
  const items = useMemo(
    () => [...saved.holdings.map((h) => ({ id: h.company.id, qty: h.qty })), ...saved.watch.map((c) => ({ id: c.id, qty: null }))],
    [saved],
  );
  const itemsKey = JSON.stringify(items);
  useEffect(() => {
    if (items.length === 0) {
      setResult(null);
      return;
    }
    let live = true;
    setError(false);
    fetchPortfolio(items, days)
      .then((found) => live && setResult(found))
      .catch(() => live && setError(true));
    return () => {
      live = false;
    };
    // items 는 itemsKey 로 비교한다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemsKey, days]);

  const stockOf = new Map((result?.stocks ?? []).map((stock) => [stock.id, stock]));
  const held = saved.holdings.map((h) => stockOf.get(h.company.id)).filter((s): s is PortfolioStock => !!s);
  const watched = saved.watch.map((c) => stockOf.get(c.id)).filter((s): s is PortfolioStock => !!s);
  const weighted = held.length
    ? held.reduce((sum, stock) => sum + (stock.weight ?? 0) * (stock.move?.[period.key] ?? 0), 0) / Math.max(1, held.reduce((sum, stock) => sum + (stock.weight ?? 0), 0))
    : null;
  const ups = [...held, ...watched].filter((stock) => (stock.move?.[period.key] ?? 0) > 0).length;
  const downs = [...held, ...watched].filter((stock) => (stock.move?.[period.key] ?? 0) < 0).length;
  const pick = (id: number) => setSelected((now) => (now === id ? null : id));

  const add = (company: Company, kind: "hold" | "watch", qty: number | null) => {
    const holdings = saved.holdings.filter((h) => h.company.id !== company.id);
    const watch = saved.watch.filter((c) => c.id !== company.id);
    update(kind === "hold" ? { holdings: [...holdings, { company, qty }], watch } : { holdings, watch: [...watch, company] });
  };
  const remove = (id: number) => {
    update({ holdings: saved.holdings.filter((h) => h.company.id !== id), watch: saved.watch.filter((c) => c.id !== id) });
    if (selected === id) setSelected(null);
  };
  const setQty = (id: number, qty: number) => update({ ...saved, holdings: saved.holdings.map((h) => (h.company.id === id ? { ...h, qty } : h)) });
  const toWatch = (id: number) => {
    const found = saved.holdings.find((h) => h.company.id === id);
    if (found) update({ holdings: saved.holdings.filter((h) => h.company.id !== id), watch: [...saved.watch, found.company] });
  };

  const chosen = selected !== null ? stockOf.get(selected) : undefined;
  const events = filter === "issues" ? [] : result?.events ?? [];
  const issues = filter === "filings" ? [] : result?.issues ?? [];
  const empty = items.length === 0;

  return (
    <div className="page mine">
      <h2>내 종목</h2>
      <p className="muted">
        보유·관심 종목을 모아 등락률과 최근 공시, 연관 급등·급락 기록을 한곳에서 봅니다. 목록은 이 브라우저에만 저장되고 서버에 남지 않습니다. 등락률은 거래일 다음 날
        나오는 종가 기준이며, 매수·매도 판단을 돕지 않습니다.
      </p>

      <AddBar onAdd={add} />

      {empty ? (
        <div className="mine-start">
          <p>
            위 칸에서 종목을 찾아 <b>보유</b>(수량을 적으면 비중대로 상자가 커집니다)나 <b>관심</b>으로 더해 보세요.
          </p>
        </div>
      ) : (
        <>
          <div className="mine-head">
            <dl className="hot-stats mine-stats">
              <div>
                <dt>보유 종목 가중 등락률 ({period.label})</dt>
                <dd className={tone(weighted)}>{weighted === null ? "–" : signed(weighted)}</dd>
              </div>
              <div>
                <dt>보유 · 관심</dt>
                <dd>
                  {saved.holdings.length} · {saved.watch.length}
                  <small>종목</small>
                </dd>
              </div>
              <div>
                <dt>오름 / 내림</dt>
                <dd>
                  <span className="up">{ups}</span> / <span className="down">{downs}</span>
                </dd>
              </div>
              <div>
                <dt>기준 거래일</dt>
                <dd className="mine-day">{result?.price_day ?? "–"}</dd>
              </div>
            </dl>
            <div className="mine-period" role="group" aria-label="등락률 기간">
              {PERIODS.map((p) => (
                <button key={p.key} className={period.key === p.key ? "on" : ""} onClick={() => setPeriod(p)}>
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          {error && <p className="mine-error">불러오지 못했습니다. 잠시 뒤 다시 열어 주세요.</p>}

          <section>
            <h3>보유 종목</h3>
            <Treemap stocks={held} period={period} selected={selected} onPick={pick} />
          </section>

          {watched.length > 0 && (
            <section>
              <h3>관심 종목</h3>
              <div className="mine-watch">
                {watched.map((stock) => {
                  const change = stock.move?.[period.key] ?? null;
                  return (
                    <button key={stock.id} className={`mine-chip ${selected === stock.id ? "on" : ""}`} style={{ background: fill(change, period.scale) }} onClick={() => pick(stock.id)}>
                      <b>{stock.name}</b>
                      <span>{signed(change)}</span>
                      {stock.move && <Spark values={stock.move.spark} />}
                    </button>
                  );
                })}
              </div>
            </section>
          )}

          {chosen && result && (
            <Detail
              stock={chosen}
              result={result}
              holding={saved.holdings.find((h) => h.company.id === chosen.id) ?? saved.watch.find((c) => c.id === chosen.id)}
              onClose={() => setSelected(null)}
              onOpenCompany={onOpenCompany}
              onAsk={onAsk}
            />
          )}

          <section className="mine-columns">
            <div>
              <div className="mine-section-head">
                <h3>내 종목 소식</h3>
                <div className="mine-filter" role="group" aria-label="소식 종류">
                  {(
                    [
                      ["all", "전체"],
                      ["filings", "공시"],
                      ["issues", "이슈"],
                    ] as const
                  ).map(([key, label]) => (
                    <button key={key} className={filter === key ? "on" : ""} onClick={() => setFilter(key)}>
                      {label}
                    </button>
                  ))}
                  <select value={days} onChange={(event) => setDays(Number(event.target.value))} aria-label="기간">
                    {WINDOWS.map((n) => (
                      <option key={n} value={n}>
                        최근 {n}일
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              {result && events.length + issues.length === 0 && <p className="muted">이 기간에 내 종목의 공시나 이슈 기록이 없습니다.</p>}
              {result && <Feed events={events} issues={issues} showCompany onPick={pick} />}
              <p className="mine-note muted">
                공시는 이 서비스가 모으는 종류(공급계약·해지, 지분 취득·처분 결정, 증자·사채·자기주식 결정, 5% 보고, 임원·주요주주 소유보고, 정기보고서)만 나옵니다. 기사는 그 종목이
                연관 급등·급락에 들었던 날 찾아 둔 것만 있고, 나머지는 뉴스 검색으로 넘깁니다.
              </p>
            </div>
            <div>
              <div className="mine-section-head">
                <h3>목록 관리</h3>
                <button className="mine-link" onClick={() => setEditing((now) => !now)}>
                  {editing ? "다 했음" : "고치기"}
                </button>
              </div>
              <table className="mine-table">
                <thead>
                  <tr>
                    <th>종목</th>
                    <th className="num">수량</th>
                    <th className="num">비중</th>
                    <th className="num">{period.label}</th>
                    {editing && <th />}
                  </tr>
                </thead>
                <tbody>
                  {saved.holdings.map((h) => {
                    const stock = stockOf.get(h.company.id);
                    return (
                      <tr key={h.company.id} className={selected === h.company.id ? "on" : ""}>
                        <td>
                          <button className="mine-who" onClick={() => pick(h.company.id)}>
                            {h.company.name}
                          </button>
                        </td>
                        <td className="num">
                          {editing ? (
                            <input
                              className="mine-qty small"
                              inputMode="numeric"
                              defaultValue={h.qty ?? ""}
                              onBlur={(event) => {
                                const value = Number(event.target.value.replaceAll(",", ""));
                                if (Number.isFinite(value) && value > 0) setQty(h.company.id, value);
                              }}
                              aria-label={`${h.company.name} 수량`}
                            />
                          ) : (
                            (h.qty ?? 0).toLocaleString()
                          )}
                        </td>
                        <td className="num">{stock?.weight === null || stock?.weight === undefined ? "–" : `${stock.weight.toFixed(1)}%`}</td>
                        <td className={`num ${tone(stock?.move?.[period.key])}`}>{signed(stock?.move?.[period.key])}</td>
                        {editing && (
                          <td className="num mine-row-actions">
                            <button onClick={() => toWatch(h.company.id)}>관심으로</button>
                            <button onClick={() => remove(h.company.id)}>빼기</button>
                          </td>
                        )}
                      </tr>
                    );
                  })}
                  {saved.watch.map((c) => {
                    const stock = stockOf.get(c.id);
                    return (
                      <tr key={c.id} className={`watch ${selected === c.id ? "on" : ""}`}>
                        <td>
                          <button className="mine-who" onClick={() => pick(c.id)}>
                            {c.name}
                          </button>
                          <span className="mine-tag">관심</span>
                        </td>
                        <td className="num">–</td>
                        <td className="num">–</td>
                        <td className={`num ${tone(stock?.move?.[period.key])}`}>{signed(stock?.move?.[period.key])}</td>
                        {editing && (
                          <td className="num mine-row-actions">
                            <button onClick={() => remove(c.id)}>빼기</button>
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
      <p className="mine-note muted">
        공시 원문은 <a href="https://dart.fss.or.kr" target="_blank" rel="noreferrer">DART</a>에서 열립니다 · 등락률은 한국거래소 일별 종가로 계산했습니다.
      </p>
      <Credit />
    </div>
  );
}
