import { useEffect, useMemo, useState } from "react";
import { searchCompanies } from "./api";
import { LINK_COLORS, LINK_LABELS, type ColorBy } from "./colors";
import { shortName } from "./Graph";
import type { Company, RelType } from "./types";

const ALL_TYPES: RelType[] = ["equity", "supply_contract", "affiliate"];

/** first~last 사이의 매달 말일 */
function monthEnds(first: string, last: string): string[] {
  const dates: string[] = [];
  const cursor = new Date(first.slice(0, 7) + "-01T00:00:00Z");
  const end = new Date(last + "T00:00:00Z");
  while (cursor <= end) {
    const monthEnd = new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 1, 0));
    dates.push((monthEnd > end ? end : monthEnd).toISOString().slice(0, 10));
    cursor.setUTCMonth(cursor.getUTCMonth() + 1);
  }
  return dates;
}

interface Props {
  firstDate: string;
  lastDate: string;
  asOf: string;
  onAsOf: (date: string) => void;
  types: RelType[];
  onTypes: (types: RelType[]) => void;
  colorBy: ColorBy;
  onColorBy: (value: ColorBy) => void;
  centerName: string | null;
  hops: number;
  onHops: (hops: number) => void;
  onOverview: () => void;
  onPick: (company: Company) => void;
}

export function Controls(props: Props) {
  const { firstDate, lastDate, asOf, onAsOf, types, onTypes } = props;
  const [text, setText] = useState("");
  const [results, setResults] = useState<Company[]>([]);
  const [playing, setPlaying] = useState(false);
  const months = useMemo(() => monthEnds(firstDate, lastDate), [firstDate, lastDate]);
  const index = Math.max(0, months.findLastIndex((m) => m <= asOf));

  useEffect(() => {
    if (text.trim().length < 1) {
      setResults([]);
      return;
    }
    const timer = setTimeout(() => searchCompanies(text.trim()).then(setResults).catch(() => setResults([])), 200);
    return () => clearTimeout(timer);
  }, [text]);

  // 과거 날짜 재생: 한 달씩 앞으로
  useEffect(() => {
    if (!playing) return;
    if (index >= months.length - 1) {
      setPlaying(false);
      return;
    }
    const timer = setTimeout(() => onAsOf(months[index + 1]), 1400);
    return () => clearTimeout(timer);
  }, [playing, index, months, onAsOf]);

  const toggle = (type: RelType) =>
    onTypes(types.includes(type) ? types.filter((t) => t !== type) : [...types, type]);

  return (
    <div className="controls">
      <div className="search">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="기업 이름 또는 종목코드"
          aria-label="기업 검색"
        />
        {results.length > 0 && (
          <ul className="results">
            {results.map((company) => (
              <li key={company.id}>
                <button
                  onClick={() => {
                    props.onPick(company);
                    setText("");
                    setResults([]);
                  }}
                >
                  <span>{shortName(company.name)}</span>
                  <span className="sub">
                    {company.stock_code ?? "비상장"}
                    {company.group ? ` · ${company.group}` : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="block">
        <div className="block-head">
          <span>조회 시점</span>
          <strong>{asOf}</strong>
        </div>
        <div className="timeline">
          <button
            className="icon"
            onClick={() => {
              if (!playing && index >= months.length - 1) onAsOf(months[0]);
              setPlaying(!playing);
            }}
            aria-label={playing ? "멈춤" : "재생"}
          >
            {playing ? "❚❚" : "▶"}
          </button>
          <input
            type="range"
            min={0}
            max={months.length - 1}
            value={index}
            onChange={(e) => {
              setPlaying(false);
              onAsOf(months[Number(e.target.value)]);
            }}
            aria-label="조회 시점"
          />
        </div>
        <p className="hint">그날까지 공시로 알려진 관계만 보입니다.</p>
      </div>

      <div className="block">
        <div className="block-head">
          <span>관계</span>
        </div>
        <div className="chips">
          {ALL_TYPES.map((type) => (
            <button
              key={type}
              className={types.includes(type) ? "chip on" : "chip"}
              onClick={() => toggle(type)}
              disabled={type === "affiliate" && props.centerName === null}
              title={type === "affiliate" && props.centerName === null ? "기업 하나를 중심으로 볼 때만 그립니다" : undefined}
            >
              <i style={{ background: LINK_COLORS[type] }} />
              {LINK_LABELS[type]}
            </button>
          ))}
        </div>
      </div>

      <div className="block">
        <div className="block-head">
          <span>점 색</span>
        </div>
        <div className="chips">
          <button className={props.colorBy === "group" ? "chip on" : "chip"} onClick={() => props.onColorBy("group")}>
            기업집단
          </button>
          <button className={props.colorBy === "stage" ? "chip on" : "chip"} onClick={() => props.onColorBy("stage")}>
            가치사슬 단계
          </button>
        </div>
      </div>

      <div className="block">
        <div className="block-head">
          <span>보는 범위</span>
        </div>
        {props.centerName === null ? (
          <p className="hint">수집 대상 전체. 기업을 검색하거나 점을 눌러 한 기업 중심으로 볼 수 있습니다.</p>
        ) : (
          <>
            <p className="scope">
              <strong>{props.centerName}</strong> 중심
            </p>
            <div className="chips">
              {[1, 2].map((n) => (
                <button key={n} className={props.hops === n ? "chip on" : "chip"} onClick={() => props.onHops(n)}>
                  {n}단계
                </button>
              ))}
              <button className="chip" onClick={props.onOverview}>
                전체로 돌아가기
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
