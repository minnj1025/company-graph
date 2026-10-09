import { useEffect, useMemo, useState } from "react";
import type { Scope } from "./api";
import { LINK_COLORS, LINK_LABELS, type ColorBy } from "./colors";
import type { Meta, RelType } from "./types";

const ALL_TYPES: RelType[] = ["equity", "supply_contract", "stake_acquisition", "stake_disposal", "affiliate", "product"];

/** 그래프를 솎아 내는 조건. 서버에 다시 묻지 않고 받은 그래프에서 거른다 */
export interface Filters {
  /** 지분율이 이 값(%) 이상인 지분 선만 */
  minPct: number;
  /** 금액이 이 값(원) 이상인 계약·결정 선만 */
  minAmount: number;
  /** 연결이 이 수 이상인 기업만 */
  minDegree: number;
}

export const NO_FILTER: Filters = { minPct: 0, minAmount: 0, minDegree: 1 };
const PCT_STEPS: [number, string][] = [[0, "전체"], [5, "5% 이상"], [20, "20% 이상"], [50, "50% 이상"]];
const AMOUNT_STEPS: [number, string][] = [[0, "전체"], [1e10, "100억 이상"], [1e11, "1,000억 이상"], [1e12, "1조 이상"]];
const DEGREE_STEPS: [number, string][] = [[1, "전체"], [2, "2곳 이상"], [5, "5곳 이상"], [10, "10곳 이상"]];

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

/** 자주 쓰는 날짜: 연말들과 가장 최근 */
function quickDates(first: string, last: string): [string, string][] {
  const dates: [string, string][] = [];
  for (let year = Number(first.slice(0, 4)); year < Number(last.slice(0, 4)); year++) {
    const end = `${year}-12-31`;
    if (end >= first) dates.push([`${year}년 말`, end]);
  }
  dates.push(["최근", last]);
  return dates.slice(-4);
}

interface TimeProps {
  firstDate: string;
  lastDate: string;
  asOf: string;
  onAsOf: (date: string) => void;
}

/** 그래프 위에 작게 두는 조회 시점: 재생, 달 단위로 옮기기, 날짜 고르기 */
export function TimeBar({ firstDate, lastDate, asOf, onAsOf }: TimeProps) {
  const [playing, setPlaying] = useState(false);
  const months = useMemo(() => monthEnds(firstDate, lastDate), [firstDate, lastDate]);
  const index = Math.max(0, months.findLastIndex((m) => m <= asOf));

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

  return (
    <div className="timebar" title="그날까지 공시로 알려진 관계만 보입니다">
      <span>조회 시점</span>
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
      <input
        type="date"
        className="date"
        value={asOf}
        min={firstDate}
        max={lastDate}
        onChange={(e) => {
          if (e.target.value >= firstDate && e.target.value <= lastDate) {
            setPlaying(false);
            onAsOf(e.target.value);
          }
        }}
        aria-label="조회 시점을 날짜로 고르기"
      />
    </div>
  );
}

interface Props extends TimeProps {
  types: RelType[];
  onTypes: (types: RelType[]) => void;
  colorBy: ColorBy;
  onColorBy: (value: ColorBy) => void;
  scope: Scope;
  categories: Meta["categories"];
  onScope: (value: Scope) => void;
  centerName: string | null;
  hops: number;
  onHops: (hops: number) => void;
  onOverview: () => void;
  filters: Filters;
  onFilters: (filters: Filters) => void;
  /** 지금 그려진 기업과 선의 수 (조건을 건 뒤) */
  shown: { nodes: number; links: number };
  /** 질문으로 찾은 그래프를 보는 중. 그 그래프는 Agent가 조회한 결과라서 아래 조건으로 바뀌지 않는다 */
  locked: boolean;
}

function Steps({ label, steps, value, onChange }: { label: string; steps: [number, string][]; value: number; onChange: (value: number) => void }) {
  return (
    <label className="steps">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {steps.map(([step, text]) => (
          <option key={step} value={step}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

/** 그래프 위의 "보기 설정"을 펼치면 나오는 칸: 관계, 솎아 보기, 보는 범위, 자주 쓰는 날짜, 점 색 */
export function Settings(props: Props) {
  const { firstDate, lastDate, asOf, onAsOf, types, onTypes } = props;
  const toggle = (type: RelType) => onTypes(types.includes(type) ? types.filter((t) => t !== type) : [...types, type]);

  return (
    <div className="controls">
      {props.locked && (
        <p className="locked-note">
          질문으로 찾은 그래프에는 아래 세 가지가 적용되지 않습니다. 전체 그래프로 돌아가거나 한 기업 중심으로 보면 적용됩니다.
        </p>
      )}
      <fieldset disabled={props.locked}>
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
              data-type={type}
              title={
                type === "affiliate" && props.centerName === null
                  ? "기업 하나를 중심으로 볼 때만 그립니다"
                  : type === "product"
                    ? "보고서의 제품 표에서 읽은 것입니다. 기업 → 제품 → 제품군으로 이어지고, 선의 굵기는 매출 비중입니다. 전체 화면에서는 두 곳 이상이 함께 파는 제품만 점으로 그립니다"
                    : undefined
              }
            >
              <i style={{ background: LINK_COLORS[type] }} />
              {LINK_LABELS[type]}
            </button>
          ))}
        </div>
      </div>

      <div className="block">
        <div className="block-head">
          <span>솎아 보기</span>
          <strong>
            기업 {props.shown.nodes.toLocaleString()} · 선 {props.shown.links.toLocaleString()}
          </strong>
        </div>
        <Steps label="지분율" steps={PCT_STEPS} value={props.filters.minPct} onChange={(minPct) => props.onFilters({ ...props.filters, minPct })} />
        <Steps
          label="금액"
          steps={AMOUNT_STEPS}
          value={props.filters.minAmount}
          onChange={(minAmount) => props.onFilters({ ...props.filters, minAmount })}
        />
        <Steps
          label="연결 수"
          steps={DEGREE_STEPS}
          value={props.filters.minDegree}
          onChange={(minDegree) => props.onFilters({ ...props.filters, minDegree })}
        />
        <p className="hint">지분율은 지분 선에, 금액은 공급계약과 취득·처분 결정 선에 적용됩니다. 조건에 맞는 선이 없는 기업은 사라집니다.</p>
        {(props.filters.minPct > 0 || props.filters.minAmount > 0 || props.filters.minDegree > 1) && (
          <button className="reset-filters" onClick={() => props.onFilters(NO_FILTER)}>
            조건 풀기
          </button>
        )}
      </div>

      <div className="block">
        <div className="block-head">
          <span>보는 범위</span>
        </div>
        {props.centerName === null ? (
          <>
            <select className="scope-select" value={props.scope} onChange={(e) => props.onScope(e.target.value)}>
              <option value="listed">상장사 전체</option>
              <optgroup label="시장">
                {props.categories.market.map((c) => (
                  <option key={c.name} value={`market:${c.name}`}>
                    {c.name} ({c.count})
                  </option>
                ))}
              </optgroup>
              <optgroup label="업종">
                {props.categories.sector.map((c) => (
                  <option key={c.name} value={`sector:${c.name}`}>
                    {c.name} ({c.count})
                  </option>
                ))}
              </optgroup>
              <optgroup label="기업집단">
                {props.categories.group.map((c) => (
                  <option key={c.name} value={`group:${c.name}`}>
                    {c.name} ({c.count})
                  </option>
                ))}
              </optgroup>
              <optgroup label="기타">
                <option value="focus">자동차 가치사슬 (처음 수집한 범위)</option>
              </optgroup>
            </select>
            <p className="hint">
              {props.scope === "listed" || props.scope.startsWith("market:")
                ? "상장사끼리의 관계만 그립니다. 비상장사와의 관계는 아래 입력 칸에서 기업을 찾거나 점을 눌러 한 기업 중심으로 보면 나옵니다."
                : "고른 분류의 기업과, 그 기업들이 관계를 맺은 상대까지 그립니다. 괄호 안은 상장사 수입니다."}
            </p>
          </>
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

      </fieldset>

      <div className="block">
        <div className="block-head">
          <span>자주 쓰는 날짜</span>
          <strong>{asOf}</strong>
        </div>
        <div className="chips">
          {quickDates(firstDate, lastDate).map(([label, value]) => (
            <button key={label} className={asOf === value ? "chip on" : "chip"} onClick={() => onAsOf(value)}>
              {label}
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
          <button className={props.colorBy === "sector" ? "chip on" : "chip"} onClick={() => props.onColorBy("sector")}>
            업종
          </button>
        </div>
      </div>
    </div>
  );
}
