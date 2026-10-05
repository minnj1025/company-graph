import { useState } from "react";
import { shortName } from "./Graph";
import type { CompanyDetail, RelationRow } from "./types";

const formatValue = (row: RelationRow) => {
  if (row.value === null) return "금액 비공개";
  if (row.unit === "pct") return `${row.value.toFixed(2)}%`;
  return row.value >= 1e8 ? `${(row.value / 1e8).toLocaleString("ko-KR", { maximumFractionDigits: 0 })}억 원` : `${row.value.toLocaleString("ko-KR")}원`;
};

interface SectionProps {
  title: string;
  rows: RelationRow[];
  other: (row: RelationRow) => { id: number | null; name: string };
  onOpen: (id: number) => void;
}

function Section({ title, rows, other, onOpen }: SectionProps) {
  const [expanded, setExpanded] = useState(false);
  if (rows.length === 0) return null;
  const shown = expanded ? rows : rows.slice(0, 8);
  return (
    <section>
      <h3>
        {title} <span className="count">{rows.length}</span>
      </h3>
      <ul>
        {shown.map((row, i) => {
          const counterpart = other(row);
          return (
            <li key={i}>
              <div className="row-head">
                {counterpart.id ? (
                  <button className="link" onClick={() => onOpen(counterpart.id!)}>
                    {shortName(counterpart.name)}
                  </button>
                ) : (
                  <span className="unlinked" title="기업 원장에 없는 상대">
                    {counterpart.name}
                  </span>
                )}
                <strong>{formatValue(row)}</strong>
              </div>
              {row.title && <div className="row-title">{row.title}</div>}
              <div className="row-meta">
                기준일 {row.as_of_date ?? "-"} · 공개일 {row.disclosed_date}
                {row.joint_parties ? ` · 상대 ${row.joint_parties}곳 공동` : ""}
                {row.pct_after ? ` · 거래 뒤 지분 ${row.pct_after}%` : ""}
                {row.disclosed_by === "both" ? " · 양쪽 공시에서 확인" : ""}
                {row.stale ? <span className="warn"> · 그 뒤 보고서 없음</span> : null}
                {row.evidence.map((e) => (
                  <a key={e.rcept_no} href={e.url} target="_blank" rel="noreferrer" title={`DART 접수번호 ${e.rcept_no}`}>
                    공시 원문
                  </a>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
      {rows.length > 8 && (
        <button className="more" onClick={() => setExpanded(!expanded)}>
          {expanded ? "접기" : `${rows.length - 8}건 더 보기`}
        </button>
      )}
    </section>
  );
}

interface Props {
  detail: CompanyDetail;
  onOpen: (id: number) => void;
  onCenter: (id: number) => void;
  onClose: () => void;
}

export function Panel({ detail, onOpen, onCenter, onClose }: Props) {
  const { company, relations, group } = detail;
  const of = (type: string, outgoing: boolean) =>
    relations.filter((r) => r.type === type && (r.subject_id === company.id) === outgoing);
  const object = (r: RelationRow) => ({ id: r.object_id, name: r.object });
  const subject = (r: RelationRow) => ({ id: r.subject_id, name: r.subject });

  return (
    <aside className="panel">
      <header>
        <div>
          <h2>{shortName(company.name)}</h2>
          <p className="sub">
            {company.stock_code ? `${company.stock_code} · ` : "비상장 · "}
            {company.stage}
            {company.group ? ` · ${company.group}그룹` : ""}
          </p>
        </div>
        <button className="icon" onClick={onClose} aria-label="닫기">
          ×
        </button>
      </header>
      <div className="panel-actions">
        <button onClick={() => onCenter(company.id)}>이 기업 중심으로 보기</button>
        <span className="sub">{detail.as_of} 시점에 공시로 알려진 내용</span>
      </div>
      <div className="panel-body">
        <Section title="보유한 지분" rows={of("equity", true)} other={object} onOpen={onOpen} />
        <Section title="이 기업의 주주 (기업)" rows={of("equity", false)} other={subject} onOpen={onOpen} />
        <Section title="판 계약 (공급계약 공시)" rows={of("supply_contract", true)} other={object} onOpen={onOpen} />
        <Section title="산 계약 (상대가 공시)" rows={of("supply_contract", false)} other={subject} onOpen={onOpen} />
        <Section title="지분 취득 결정" rows={of("stake_acquisition", true)} other={object} onOpen={onOpen} />
        <Section title="지분 처분 결정" rows={of("stake_disposal", true)} other={object} onOpen={onOpen} />
        <Section
          title="이 기업의 지분을 사거나 판 결정 (상대가 공시)"
          rows={[...of("stake_acquisition", false), ...of("stake_disposal", false)]}
          other={subject}
          onOpen={onOpen}
        />
        {group.count > 0 && (
          <section>
            <h3>
              같은 집단 <span className="count">{group.count}</span>
              {group.source.map((e) => (
                <a key={e.rcept_no} href={e.url} target="_blank" rel="noreferrer" className="h3-link">
                  공시 원문
                </a>
              ))}
            </h3>
            <p className="members">{group.members.map(shortName).join(", ")}</p>
          </section>
        )}
        {relations.length === 0 && group.count === 0 && <p className="empty">이 시점에 알려진 관계가 없습니다.</p>}
      </div>
    </aside>
  );
}
