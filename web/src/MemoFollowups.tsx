import { useEffect, useState } from "react";
import { fetchMemoFollowups, type MemoFollowups } from "./api";

/** 분석은 뉴스 전날까지의 공시만 본다. 그 뒤 매일 아침 받은 공시에서 주인공이 낸 것을 찾아,
 * 뉴스가 전한 일을 공시가 뒤따라 확인했는지 보인다. 금액은 뉴스 제목과 견주고, 같은 일인지는 원문으로 확인하게 한다. */

export function won(value: number): string {
  if (value >= 1e12) return `${(value / 1e12).toFixed(2).replace(/\.?0+$/, "")}조 원`;
  if (value >= 1e8) return `${Math.round(value / 1e8).toLocaleString()}억 원`;
  return `${Math.round(value / 1e6).toLocaleString()}백만 원`;
}

const nextDay = (day: string) => new Date(new Date(`${day}T00:00:00Z`).getTime() + 86400e3).toISOString().slice(0, 10);

export function Followups({ after, subjects, title }: { after: string; subjects: { id: number; name: string }[]; title: string }) {
  const [data, setData] = useState<MemoFollowups | null>(null);
  const [failed, setFailed] = useState(false);
  const ids = subjects.map((s) => s.id);
  const key = `${after}:${ids.join(",")}:${title}`;
  useEffect(() => {
    setData(null);
    setFailed(false);
    fetchMemoFollowups(after, ids, title)
      .then(setData)
      .catch(() => setFailed(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  if (failed || subjects.length === 0) return null;
  if (!data) return <p className="muted small">뉴스 뒤에 나온 공시를 찾는 중…</p>;

  const who = subjects.map((s) => s.name).join(", ");
  const nameOf = new Map(subjects.map((s) => [s.id, s.name]));
  const from = nextDay(data.after);
  const through = data.checked_through && data.checked_through < data.until ? data.checked_through : data.until;
  const notYet = !data.checked_through || data.checked_through < from;
  const hasAmount = data.news_amounts.length > 0;
  const span = from === through ? `${from.slice(5)} 하루 동안` : `${from.slice(5)}부터 ${through.slice(5)}까지`;
  return (
    <section className="memo-sec followups">
      <h4>뉴스 뒤에 나온 주인공의 공시</h4>
      <p className={data.same_amount > 0 ? "fu-sum fu-hit" : "fu-sum"}>
        {notYet ? (
          <>분석한 뒤의 공시를 아직 받지 않았습니다. 공시는 매일 아침 들어옵니다.</>
        ) : data.count === 0 ? (
          <>
            {span} {who}의 공급계약·지분·증자 공시가 없습니다.
          </>
        ) : (
          <>
            {span} {who}의 공시 {data.count}건
            {data.contracts > 0 && ` (공급계약 ${data.contracts}건)`}
            {hasAmount && (data.same_amount > 0 ? ` · 뉴스 제목의 금액과 같은 공시 ${data.same_amount}건` : " · 뉴스 제목의 금액과 같은 공시는 없음")}.
          </>
        )}
      </p>
      {data.filings.length > 0 && (
        <ul className="fu-list">
          {data.filings.map((f) => (
            <li key={f.rcept_no} className={f.same_amount ? "fu-same" : undefined}>
              <span className="fu-day">{f.filed.slice(5)}</span>
              <span className="fu-body">
                <a href={f.url} target="_blank" rel="noreferrer" title={`${f.report} (${f.rcept_no})`}>
                  {f.kind}
                  {f.corrected && " 정정"}
                </a>
                {subjects.length > 1 && <small> · {nameOf.get(f.company_id)}</small>}
                {f.what && <span className="fu-what">{f.what}</span>}
                <small className="muted">
                  {[f.party, f.amount ? won(f.amount) : null].filter(Boolean).join(" · ")}
                </small>
              </span>
              {f.same_amount ? (
                <em className="fu-tag fu-tag-same" title="뉴스 제목의 금액과 3% 안쪽으로 같습니다">✓ 뉴스 금액과 같음</em>
              ) : hasAmount && f.kind === "공급계약" && f.amount ? (
                <em className="fu-tag" title="뉴스 제목의 금액과 다릅니다. 다른 계약이거나 금액을 다르게 셌을 수 있습니다">금액 다름</em>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      <p className="muted small">
        분석은 {data.after}까지 나온 공시만 봤습니다. 그 뒤 {data.until}까지 매일 아침 받는 공시(공급계약·해지, 지분 취득·처분 결정, 증자·사채·자기주식
        결정)에서 찾았습니다{hasAmount && `. 뉴스 제목의 금액 ${data.news_amounts.map(won).join(", ")}과 3% 안쪽이면 같다고 표시합니다`}. 같은 일인지는 원문으로
        확인해 주세요.
      </p>
    </section>
  );
}
