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
