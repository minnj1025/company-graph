import { useEffect, useMemo, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { deletePage, fetchPage, myPages, pageUrl, rememberPages } from "./api";
import { Cell, DART, linkCompanies, linkReceipts } from "./AnswerText";
import { Credit } from "./Credit";
import type { PageBlock, UserPage, View } from "./types";
import Views from "./Views";

/** Corpath 그래프에서 기업을 여는 주소 (App 이 ?show= 를 읽는다) */
const showUrl = (ids: number[]) => `/?show=${ids.join(",")}`;

function Text({ text, companies }: { text: string; companies: UserPage["companies"] }) {
  const linked = useMemo(() => linkReceipts(linkCompanies(text, companies)), [text, companies]);
  return (
    <div className="answer">
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          td: Cell,
          a: ({ href, children }) =>
            href?.startsWith("#company-") ? (
              <a className="company-link" href={showUrl([Number(href.slice(9))])} title="Corpath 그래프에서 이 기업 보기">
                {children}
              </a>
            ) : href?.startsWith(DART) ? (
              <a className="receipt" href={href} target="_blank" rel="noreferrer" title={`DART 공시 원문 ${href.slice(DART.length)}`}>
                {children}
              </a>
            ) : (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            ),
        }}
      >
        {linked}
      </Markdown>
    </div>
  );
}

/** 이어진 카드 블록은 한 묶음으로 그린다 (Views 가 여러 장을 나란히 다룬다) */
function group(blocks: PageBlock[]): ({ type: "text"; text: string } | { type: "views"; views: View[]; errors: string[] })[] {
  const out: ({ type: "text"; text: string } | { type: "views"; views: View[]; errors: string[] })[] = [];
  for (const block of blocks) {
    if (block.type === "text") {
      out.push(block);
      continue;
    }
    const last = out[out.length - 1];
    const into = last?.type === "views" ? last : { type: "views" as const, views: [], errors: [] };
    if (into !== last) out.push(into);
    if ("view" in block) into.views.push(block.view);
    else into.errors.push(block.error);
  }
  return out;
}

/** 분석 페이지 (?page=주소). 방문자가 Agent의 답을 남긴 것. 카드는 열 때 서버가 DB로 채운다 */
export default function PageView({ slug }: { slug: string }) {
  const [basis, setBasis] = useState<"made" | "today">(() => (new URLSearchParams(window.location.search).get("basis") === "today" ? "today" : "made"));
  const [page, setPage] = useState<UserPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(true);
  const [mine, setMine] = useState(myPages);
  const own = mine.find((p) => p.slug === slug);

  useEffect(() => {
    let theme = "dark";
    try {
      theme = localStorage.getItem("theme") === "light" ? "light" : "dark";
    } catch {
      /* 기본은 어두운 바탕 */
    }
    document.documentElement.dataset.theme = theme;
    // 방문자가 만든 글이라 검색엔진이 모으지 않게 한다 (서버도 X-Robots-Tag 를 보낸다)
    const robots = document.createElement("meta");
    robots.name = "robots";
    robots.content = "noindex";
    document.head.appendChild(robots);
    return () => robots.remove();
  }, []);

  useEffect(() => {
    setError(null);
    setLoading(true);
    let current = true;
    fetchPage(slug, basis)
      .then((loaded) => {
        if (!current) return;
        setPage(loaded);
        document.title = `${loaded.title} · Corpath`;
      })
      .catch((reason: Error) => current && setError(reason.message))
      .finally(() => current && setLoading(false));
    const url = new URL(window.location.href);
    if (basis === "today") url.searchParams.set("basis", "today");
    else url.searchParams.delete("basis");
    window.history.replaceState(null, "", url);
    return () => {
      current = false;
    };
  }, [slug, basis]);

  const parts = useMemo(() => (page ? group(page.blocks) : []), [page]);
  const others = mine.filter((p) => p.slug !== slug).slice(-8).reverse();

  return (
    <div className="page-view">
      <header className="page-top">
        <a className="brand" href="/" title="Corpath 첫 화면">
          Corpath
        </a>
        <span>분석 페이지</span>
      </header>
      <main className="page-main">
        {error && <p className="page-error">{error}</p>}
        {!page && !error && <p className="page-note">불러오는 중…</p>}
        {page && (
          <>
            <h1>{page.title}</h1>
            <p className="page-meta">
              <span>{page.created_at.slice(0, 10)}에 남김</span>
              {" · "}
              <span>조회 시점 {page.as_of}</span>
              {" · "}
              <span>카드 기준 {page.shown_as_of}</span>
            </p>
            <p className="page-note">
              Corpath Agent가 공시 DB를 조회해 쓴 답입니다. 카드의 숫자는 페이지를 열 때 DB에서 채웁니다. 공시에 적힌 사실을 정리한 것이며 투자 권유가 아닙니다.
            </p>

            <section className={page.basis === "today" ? "page-band today" : "page-band"}>
              {loading ? (
                <span>카드를 {basis === "today" ? "오늘" : "남긴 날"} 기준으로 다시 채우는 중…</span>
              ) : page.basis === "made" ? (
                <>
                  <span>
                    {page.newer.total > 0
                      ? `남긴 뒤 이 기업들이 낸 공시 ${page.newer.total}건`
                      : "남긴 뒤 이 기업들이 낸 공시는 아직 없습니다 (이 DB가 모으는 공시 기준)"}
                  </span>
                  {page.newer.total > 0 && (
                    <button onClick={() => setBasis("today")} title="글은 그대로 두고 카드만 오늘 기준으로 다시 채웁니다">
                      카드를 오늘 기준으로 보기
                    </button>
                  )}
                </>
              ) : (
                <>
                  <span>
                    카드는 오늘({page.shown_as_of}) 기준입니다. 글은 {page.as_of} 기준으로 쓴 것이라 카드와 다를 수 있습니다.
                  </span>
                  <button onClick={() => setBasis("made")}>남긴 날 기준으로 보기</button>
                </>
              )}
              {page.newer.total > 0 && (
                <details>
                  <summary>새 공시 보기</summary>
                  {page.newer.items.map((item) => (
                    <a key={item.rcept_no} href={DART + item.rcept_no} target="_blank" rel="noreferrer" title="DART 공시 원문">
                      <em>{item.filed}</em>
                      {item.company ? `${item.company} · ` : ""}
                      {item.report}
                    </a>
                  ))}
                  {page.newer.total > page.newer.items.length && <span>외 {page.newer.total - page.newer.items.length}건</span>}
                </details>
              )}
            </section>

            {page.question !== page.title && (
              <p className="page-question">
                <b>물은 것</b> {page.question}
              </p>
            )}
            {parts.map((part, i) =>
              part.type === "text" ? (
                <Text key={i} text={part.text} companies={page.companies} />
              ) : (
                <div key={i} className="page-views">
                  {part.views.length > 0 && <Views views={part.views} onCompany={(id) => window.location.assign(showUrl([id]))} />}
                  {part.errors.map((message, j) => (
                    <p key={j} className="page-error">
                      이 카드를 지금 채우지 못했습니다: {message}
                    </p>
                  ))}
                </div>
              ),
            )}

            {page.sources.length > 0 && (
              <details className="sources">
                <summary>출처 {page.sources.length}건</summary>
                {page.sources.map((source) => (
                  <a key={source.rcept_no} href={source.url} target="_blank" rel="noreferrer" title="DART 공시 원문">
                    <em>{source.filed ?? source.rcept_no}</em>
                    {source.company ? `${source.company} · ${source.report}` : `접수번호 ${source.rcept_no}`}
                  </a>
                ))}
              </details>
            )}

            <div className="page-actions">
              {page.highlight.length > 0 && <a href={showUrl(page.highlight)}>Corpath 그래프에서 보기</a>}
              <button onClick={() => void navigator.clipboard?.writeText(pageUrl(slug)).then(() => setCopied(true))} title="이 주소를 아는 사람은 누구나 이 페이지를 열 수 있습니다">
                {copied ? "주소를 복사함" : "주소 복사"}
              </button>
              {own && (
                <button
                  className="danger"
                  onClick={() => {
                    if (!window.confirm("이 페이지를 지울까요? 주소를 받은 사람도 더는 열 수 없고, 되돌릴 수 없습니다.")) return;
                    deletePage(slug, own.key)
                      .then(() => {
                        const left = myPages().filter((p) => p.slug !== slug);
                        rememberPages(left);
                        setMine(left);
                        setPage(null);
                        setError("페이지를 지웠습니다.");
                      })
                      .catch((reason: Error) => setError(reason.message));
                  }}
                >
                  지우기
                </button>
              )}
            </div>
          </>
        )}

        {others.length > 0 && (
          <section className="page-mine">
            <b>이 브라우저에서 남긴 페이지</b>
            {others.map((p) => (
              <a key={p.slug} href={`/?page=${p.slug}`}>
                <em>{p.created.slice(0, 10)}</em>
                {p.title}
              </a>
            ))}
          </section>
        )}
      </main>
      <Credit />
    </div>
  );
}
