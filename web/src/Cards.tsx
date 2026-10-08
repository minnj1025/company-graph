import { useEffect, useMemo, useState } from "react";
import { fetchInsights } from "./api";
import { LINK_COLORS } from "./colors";
import { shortName } from "./Graph";
import type { FeedType, GraphData, Insights } from "./types";

const FEED_TYPES: FeedType[] = ["supply_contract", "stake_acquisition", "stake_disposal", "supply_termination"];
const won = (value: number) => (value >= 1e12 ? `${(value / 1e12).toFixed(1)}조` : `${Math.round(value / 1e8).toLocaleString()}억`);

interface Props {
  asOf: string;
  data: GraphData;
  /** 그래프가 지금 무엇을 그리고 있는지 (세 번째 칸의 제목에 쓴다) */
  scopeLabel: string;
  onOpen: (id: number) => void;
}

/** 그래프 아래의 세 칸: 최근 공시, 월별 공시 건수, 지금 그래프에서 연결이 많은 기업. */
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
          월별 공시 건수 <span>최근 18개월</span>
        </h3>
        {insights && <Monthly insights={insights} />}
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

function Monthly({ insights }: { insights: Insights }) {
  const months = insights.monthly.slice(-18);
  const max = Math.max(1, ...months.map((m) => FEED_TYPES.reduce((sum, t) => sum + m[t], 0)));
  const [width, height, gap] = [100 / months.length, 96, 0.22];
  return (
    <div className="monthly">
      <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" role="img" aria-label="월별 공시 건수 막대 그래프">
        {months.map((month, i) => {
          let y = height;
          return FEED_TYPES.map((type) => {
            const h = (month[type] / max) * height;
            y -= h;
            return (
              <rect key={`${month.month}-${type}`} x={i * width + (width * gap) / 2} y={y} width={width * (1 - gap)} height={h} fill={LINK_COLORS[type]}>
                <title>{`${month.month} ${insights.labels[type]} ${month[type]}건`}</title>
              </rect>
            );
          });
        })}
      </svg>
      <div className="axis">
        <span>{months[0]?.month}</span>
        <span>한 달 최대 {max.toLocaleString()}건</span>
        <span>{months[months.length - 1]?.month}</span>
      </div>
      <div className="keys">
        {FEED_TYPES.map((type) => (
          <span key={type}>
            <i style={{ background: LINK_COLORS[type] }} />
            {insights.labels[type]}
          </span>
        ))}
      </div>
    </div>
  );
}
