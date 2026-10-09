import type { AskResult, AskStatus, Company, CompanyDetail, GraphData, Insights, Meta, RelType, Suggestion, Taxonomy } from "./types";

/** 운영자 열쇠. 주소 끝에 #owner=열쇠 를 붙여 한 번 열면 이 브라우저에 적어 두고, 질문할 때 같이 보낸다 (횟수 한도를 받지 않는다) */
function ownerKey(): string | null {
  try {
    const fromHash = /^#owner=(.*)$/.exec(window.location.hash)?.[1];
    if (fromHash !== undefined) {
      if (fromHash) localStorage.setItem("owner-key", decodeURIComponent(fromHash));
      else localStorage.removeItem("owner-key");   // #owner= 만 적으면 지운다
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
    return localStorage.getItem("owner-key");
  } catch {
    return null;
  }
}
const OWNER_KEY = ownerKey();
const ownerHeaders = (): Record<string, string> => (OWNER_KEY ? { "X-Owner-Key": OWNER_KEY } : {});

async function get<T>(path: string, params: Record<string, string | number> = {}): Promise<T> {
  const query = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]));
  const response = await fetch(`/api${path}?${query}`, path.startsWith("/ask") ? { headers: ownerHeaders() } : undefined);
  if (!response.ok) throw new Error(`${path} ${response.status}`);
  return response.json();
}

export const fetchMeta = () => get<Meta>("/meta");
export const searchCompanies = (q: string) => get<Company[]>("/companies", { q });
/** "listed", "focus", 또는 "market:코스닥", "sector:자동차", "group:삼성" */
export type Scope = string;
/** 제품을 어느 크기의 범주로 묶어 그릴지: 대분류, 중분류(공식 분류), 제품군, 제품 */
export type Level = "section" | "division" | "family" | "product";
export const fetchOverview = (asOf: string, types: RelType[], scope: Scope, level: Level) =>
  get<GraphData>("/overview", { as_of: asOf, types: types.join(","), scope, level });
export const fetchGraph = (center: number, asOf: string, types: RelType[], hops: number, level: Level) =>
  get<GraphData>("/graph", { center, as_of: asOf, types: types.join(","), hops, level });
/** 적는 글에 맞는 제품, 제품군, 공식 분류 */
export const fetchSuggest = (q: string) => get<Suggestion[]>("/suggest", { q });
/** 제품·제품군·분류 하나를 파는 기업 전부의 그래프 */
export const fetchPick = (kind: Suggestion["kind"], key: string, asOf: string) => get<GraphData>("/pick", { kind, key, as_of: asOf });
export const fetchTaxonomy = () => get<Taxonomy>("/taxonomy");
export const fetchCompany = (id: number, asOf: string) => get<CompanyDetail>(`/company/${id}`, { as_of: asOf });
export const fetchInsights = (asOf: string) => get<Insights>("/insights", { as_of: asOf });
export const fetchAskStatus = () => get<AskStatus>("/ask/status");

/** 답이 만들어지는 동안 서버가 보내는 것: 지금 부르는 도구, 답의 글 조각, 도구 결과를 받고 새로 쓰기 시작 */
export type AskEvent = { kind: "tool"; name: string; input: Record<string, unknown> } | { kind: "text"; text: string } | { kind: "turn" };

const REFUSED = "지금은 답할 수 없습니다. 잠시 뒤에 다시 시도해 주세요.";

/** Agent에게 묻고, 답이 만들어지는 대로 onEvent 로 받는다. 끝나면 askAgent 와 같은 값을 돌려준다. */
export async function askAgentStream(question: string, onEvent: (event: AskEvent) => void): Promise<AskResult> {
  const response = await fetch("/api/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...ownerHeaders() },
    body: JSON.stringify({ question }),
  });
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : REFUSED);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    // 사건 하나는 빈 줄로 끝난다
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const kind = /^event: (.+)$/m.exec(block)?.[1];
      const data = /^data: (.*)$/m.exec(block)?.[1];
      if (!kind || data === undefined) continue;
      const parsed = JSON.parse(data);
      if (kind === "done") return parsed as AskResult;
      if (kind === "error") throw new Error(typeof parsed === "string" ? parsed : REFUSED);
      if (kind === "text") onEvent({ kind, text: parsed });
      else if (kind === "tool") onEvent({ kind, name: parsed.name, input: parsed.input });
      else if (kind === "turn") onEvent({ kind });
    }
    if (done) throw new Error(REFUSED);
  }
}

/** Agent에게 묻는다. 서버가 거절하면(횟수 초과 등) 그 사유를 그대로 던진다. */
export async function askAgent(question: string): Promise<AskResult> {
  const response = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...ownerHeaders() },
    body: JSON.stringify({ question }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : "지금은 답할 수 없습니다. 잠시 뒤에 다시 시도해 주세요.");
  }
  return response.json();
}
