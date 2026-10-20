import { lazy, Suspense, useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { reportStream, type ReportResult, type ReportSection } from "./api";

// 카드는 차트 라이브러리를 쓰므로 보고서를 열 때 받는다
const Views = lazy(() => import("./Views"));

/** 항목의 차례 (서버 report.py 의 gather 와 같다). 항목은 끝나는 대로 오지만 이 차례로 놓는다 */
const ORDER: [string, string][] = [
  ["business", "사업 구성"],
  ["financials", "실적과 재무"],
  ["governance", "지배 구조와 주주"],
  ["deals", "최근 1년의 계약과 결정"],
  ["capital", "자본 변동과 주주 환원"],
  ["insiders", "내부자 매매"],
  ["price", "주가"],
];
const DART = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=";

/** 접수번호를 DART 원문 링크로 */
const linked = (text: string) => text.replace(/\b(\d{14})\b/g, (no) => `[${no}](${DART}${no})`);

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/**
 * 기업 분석 보고서. 서버가 정해진 조회로 사실을 모으고 항목마다 따로 쓴다(company_graph/report.py).
 * 항목은 끝나는 대로 채워지고, 다 끝나면 요약과 '이 DB로 알 수 없는 것'이 붙는다.
 */
export default function ReportView({ companyId, name, onClose, onCompany }: { companyId: number; name: string; onClose: () => void; onCompany: (id: number) => void }) {
  const [sections, setSections] = useState<Record<string, ReportSection>>({});
  const [report, setReport] = useState<ReportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    reportStream(companyId, (section) => alive && setSections((all) => ({ ...all, [section.key]: section })))
      .then((result) => alive && setReport(result))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [companyId]);

  useEffect(() => {
    const close = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);

  const done = Object.keys(sections).length;
  return (
    <div className="report-overlay" role="dialog" aria-label={`${name} 기업 분석 보고서`}>
      <article className="report-page">
        <header className="report-head">
          <div>
            <h1>{name} 기업 분석 보고서</h1>
            <p className="sub">
              {report ? `조회 시점 ${report.as_of} · ` : ""}공시(DART)에서 모은 사실만 정리했습니다. 원인 판단, 전망, 투자 권유가 아닙니다.
            </p>
          </div>
          <div className="report-actions">
            {report && (
              <>
                <button onClick={() => download(`${name}_분석보고서_${report.as_of}.md`, report.markdown)}>마크다운 내려받기</button>
                <button onClick={() => window.print()}>인쇄 · PDF</button>
              </>
            )}
            <button className="icon" onClick={onClose} aria-label="닫기">
              ×
            </button>
          </div>
        </header>

        {error && <p className="report-error">{error}</p>}
        {!report && !error && (
          <p className="report-progress">
            공시에서 사실을 모아 항목마다 쓰는 중입니다 ({done}/{ORDER.length}). 보통 30초~1분 걸립니다.
          </p>
        )}

        {report && (
          <section className="report-section summary">
            <h2>요약</h2>
            <Markdown remarkPlugins={[remarkGfm]}>{report.summary}</Markdown>
          </section>
        )}

        {ORDER.map(([key, title]) => {
          const section = sections[key];
          return (
            <section key={key} className={`report-section${section ? "" : " pending"}`}>
              <h2>{title}</h2>
              {section ? (
                <>
                  <Markdown remarkPlugins={[remarkGfm]} components={{ a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer">{children}</a> }}>
                    {linked(section.text)}
                  </Markdown>
                  {section.views.length > 0 && (
                    <Suspense fallback={null}>
                      <Views views={section.views} onCompany={onCompany} />
                    </Suspense>
                  )}
                </>
              ) : (
                <p className="sub">쓰는 중…</p>
              )}
            </section>
          );
        })}

        {report && (
          <section className="report-section">
            <h2>이 DB로 알 수 없는 것</h2>
            <ul>
              {report.unknown.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
            <p className="sub">
              작성 {report.model} · {report.seconds}초{report.verified ? " · 숫자를 조회 결과와 대조함" : ""}
            </p>
          </section>
        )}
      </article>
    </div>
  );
}
