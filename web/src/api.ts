import type { AskResult, AskStatus, Company, CompanyDetail, GraphData, Insights, Meta, RelType } from "./types";

async function get<T>(path: string, params: Record<string, string | number> = {}): Promise<T> {
  const query = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]));
  const response = await fetch(`/api${path}?${query}`);
  if (!response.ok) throw new Error(`${path} ${response.status}`);
  return response.json();
}

export const fetchMeta = () => get<Meta>("/meta");
export const searchCompanies = (q: string) => get<Company[]>("/companies", { q });
/** "listed", "focus", 또는 "market:코스닥", "sector:자동차", "group:삼성" */
export type Scope = string;
export const fetchOverview = (asOf: string, types: RelType[], scope: Scope) =>
  get<GraphData>("/overview", { as_of: asOf, types: types.join(","), scope });
export const fetchGraph = (center: number, asOf: string, types: RelType[], hops: number) =>
  get<GraphData>("/graph", { center, as_of: asOf, types: types.join(","), hops });
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
    headers: { "Content-Type": "application/json" },
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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : "지금은 답할 수 없습니다. 잠시 뒤에 다시 시도해 주세요.");
  }
  return response.json();
}
