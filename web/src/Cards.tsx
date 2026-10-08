import { useEffect, useMemo, useState } from "react";
import { fetchInsights } from "./api";
import { LINK_COLORS } from "./colors";
import { shortName } from "./Graph";
import type { GraphData, Insights } from "./types";

const won = (value: number) => (value >= 1e12 ? `${(value / 1e12).toFixed(1)}조` : `${Math.round(value / 1e8).toLocaleString()}억`);

interface Props {
  asOf: string;
  data: GraphData;
  /** 그래프가 지금 무엇을 그리고 있는지 (세 번째 칸의 제목에 쓴다) */
  scopeLabel: string;
  onOpen: (id: number) => void;
}

/** 그래프 아래의 세 칸: 최근 공시, 최근에 바뀐 것, 지금 그래프에서 연결이 많은 기업. */
export function Cards({ asOf, data, scopeLabel, onOpen }: Props) {
  const [insights, setInsights] = useState<Insights | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetchInsights(asOf)
      .then((value) => !cancelled && setInsights(value))
      .catch(() => !cancelled && setInsights(null));
    return () => {
      cancelled = true;
    };
  }, [asOf]);

  const hubs = useMemo(() => [...data.nodes].sort((a, b) => b.degree - a.degree).slice(0, 8), [data]);
  const top = hubs[0]?.degree ?? 1;

  return (
    <section className="cards">
      <div className="card">
        <h3>
          최근 공시 <span>{asOf}까지</span>
        </h3>
        <ul className="feed">
          {insights?.recent.map((item) => (
            <li key={item.rcept_no}>
              <i style={{ background: LINK_COLORS[item.type] }} title={item.label} />
              <span className="when">{item.date.slice(5)}</span>
              <button onClick={() => onOpen(item.subject_id)} title={item.title ?? item.label}>
                {shortName(item.subject)}
                <em>
                  {" → "}
                  {item.object === "-" ? "상대 비공개" : shortName(item.object)}
                </em>
              </button>
              <a href={item.url} target="_blank" rel="noreferrer" title={`${item.label} 공시 원문`}>
                {item.value ? won(item.value) : item.label}
              </a>
            </li>
          ))}
          {!insights && <li className="empty">불러오는 중…</li>}
        </ul>
      </div>

      <div className="card">
        <h3>
          최근에 바뀐 것 <span>정정 · 해지 · 철회</span>
        </h3>
        <ul className="feed">
          {insights?.changed.map((item) => (
            <li key={item.rcept_no}>
              <span className={`kind kind-${item.kind}`}>{item.kind}</span>
              <span className="when">{item.date.slice(5)}</span>
              <button onClick={() => onOpen(item.company_id)} title={item.reason ? `${item.what} — ${item.reason}` : item.what}>
                {shortName(item.company)}
                <em> {item.reason ?? item.what}</em>
              </button>
              <a href={item.url} target="_blank" rel="noreferrer" title="공시 원문">
                원문
              </a>
            </li>
          ))}
          {!insights && <li className="empty">불러오는 중…</li>}
        </ul>
      </div>

      <div className="card">
        <h3>
          연결이 많은 기업 <span>{scopeLabel}</span>
        </h3>
        <ul className="hubs">
          {hubs.map((node) => (
            <li key={node.id}>
              <button onClick={() => onOpen(node.id)}>{shortName(node.name)}</button>
              <div className="meter">
                <div style={{ width: `${(node.degree / top) * 100}%` }} />
              </div>
              <span>{node.degree}</span>
            </li>
          ))}
          {hubs.length === 0 && <li className="empty">그래프에 기업이 없습니다</li>}
        </ul>
      </div>
    </section>
  );
}
