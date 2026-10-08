import type { Company, CompanyDetail, GraphData, Meta, RelType } from "./types";

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
