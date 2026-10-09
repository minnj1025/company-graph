import { useEffect, useMemo, useState } from "react";
import type { Level, Scope } from "./api";
import { LINK_COLORS, LINK_LABELS, type ColorBy } from "./colors";
import type { Meta, RelType, Suggestion, Taxonomy } from "./types";

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
/** 큰 범주에서 작은 범주로: 값, 단추 이름, 설명 */
const LEVELS: [Level, string, string][] = [
  ["section", "대분류", "공식 분류 21개"],
  ["division", "중분류", "공식 분류 77개"],
  ["family", "제품군", "약 165개"],
  ["product", "제품", "가장 작게"],
];
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
  /** 제품을 어느 크기의 범주로 묶어 그릴지 */
  level: Level;
  /** 범주 단추를 눌렀다. 옆에 그 범주의 목록을 편다 */
  onLevel: (level: Level) => void;
  /** 옆에 목록이 펴져 있는 범주 */
  listed: Level | null;
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

interface ListProps {
  level: Level;
  taxonomy: Taxonomy | null;
  /** 이 크기의 범주 전체로 묶어 그린다 */
  onAll: () => void;
  /** 범주 하나를 골랐다. 그것을 파는 기업만 그린다 */
  onPick: (item: Suggestion) => void;
}

/** 범주 단추 옆에 펴지는 목록: 맨 위는 "전체", 그 아래로 그 범주의 항목이 순서대로 */
export function LevelList({ level, taxonomy, onAll, onPick }: ListProps) {
  const label = LEVELS.find(([value]) => value === level)![1];
  const groups = useMemo(() => {
    if (!taxonomy) return [];
    if (level === "section")
      return [{ head: null as string | null, items: taxonomy.sections.map((s) => ({ kind: "class" as const, key: s.code, name: s.name, note: s.code })) }];
    if (level === "division")
      return taxonomy.sections.map((s) => ({
        head: s.name,
        items: s.divisions.map((d) => ({ kind: "class" as const, key: d.code, name: d.short, note: d.code })),
      }));
    if (level === "family") {
      const fields = new Map<string, Map<string, number>>();
      for (const s of taxonomy.sections)
        for (const d of s.divisions)
          for (const f of d.families) {
            const names = fields.get(f.field ?? "기타") ?? new Map<string, number>();
            names.set(f.name, Math.max(names.get(f.name) ?? 0, f.companies));
            fields.set(f.field ?? "기타", names);
          }
      return [...fields.entries()].map(([field, names]) => ({
        head: field,
        items: [...names.keys()].sort((a, b) => a.localeCompare(b, "ko")).map((name) => ({ kind: "family" as const, key: name, name, note: "" })),
      }));
    }
    return [];
  }, [taxonomy, level]);

  return (
    <div className="level-list">
      <button className="all" onClick={onAll}>
        {label} 전체
        <em>이 크기의 범주로 묶어 그리기</em>
      </button>
      {!taxonomy && <p className="hint">목록을 불러오는 중…</p>}
      {level === "product" && <p className="hint">제품은 3,000개가 넘어서 목록으로 펴지 않습니다. 아래 입력 칸에 제품 이름을 적으면 바로 찾습니다.</p>}
      {groups.map((group) => (
        <div key={group.head ?? "all"} className="group">
          {group.head && <h4>{group.head}</h4>}
          {group.items.map((item) => (
            <button key={item.key} onClick={() => onPick({ kind: item.kind, key: item.key, name: item.name, companies: 0 })}>
              {item.note && <code>{item.note}</code>}
              {item.name}
            </button>
          ))}
        </div>
      ))}
    </div>
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
      <div className="block">
        <div className="block-head">
          <span>제품 범주</span>
          <strong>{types.includes("product") ? LEVELS.find(([value]) => value === props.level)?.[2] : "꺼짐"}</strong>
        </div>
        <div className="chips">
          {LEVELS.map(([value, label]) => (
            <button
              key={value}
              className={(types.includes("product") && props.level === value ? "chip on" : "chip") + (props.listed === value ? " listed" : "")}
              onClick={() => props.onLevel(value)}
              aria-expanded={props.listed === value}
            >
              {label} ›
            </button>
          ))}
        </div>
        <p className="hint">누르면 옆에 목록이 펴집니다. 전체를 고르면 그 크기의 범주로 묶어 그리고, 하나를 고르면 그것을 파는 기업만 그립니다.</p>
      </div>

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
