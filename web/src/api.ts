import type { AskResult, AskStatus, Company, CompanyDetail, GraphData, HotDay, Insights, Meta, RelType, Suggestion, Taxonomy, View } from "./types";

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
/** 그날 함께 오른 무리. day 가 없으면 가장 최근 거래일 */
export const fetchHot = (day?: string) => get<HotDay>("/hot", day ? { day } : {});
export const fetchHotGraph = (day: string, group: number, side: "up" | "down") => get<GraphData>("/hot/graph", { day, group, side });
export const fetchTaxonomy = () => get<Taxonomy>("/taxonomy");
export const fetchCompany = (id: number, asOf: string) => get<CompanyDetail>(`/company/${id}`, { as_of: asOf });
export const fetchInsights = (asOf: string) => get<Insights>("/insights", { as_of: asOf });
export const fetchAskStatus = () => get<AskStatus>("/ask/status");

/** 답이 만들어지는 동안 서버가 보내는 것: 지금 부르는 도구, 답의 글 조각, 도구 결과를 받고 새로 쓰기 시작 */
export type AskEvent = { kind: "tool"; name: string; input: Record<string, unknown> } | { kind: "text"; text: string } | { kind: "turn" } | { kind: "view"; view: View };

const REFUSED = "지금은 답할 수 없습니다. 잠시 뒤에 다시 시도해 주세요.";

/** Agent에게 묻고, 답이 만들어지는 대로 onEvent 로 받는다. 끝나면 askAgent 와 같은 값을 돌려준다. */
export async function askAgentStream(
  question: string,
  onEvent: (event: AskEvent) => void,
  history: { question: string; answer: string }[] = [],
): Promise<AskResult> {
  const response = await fetch("/api/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...ownerHeaders() },
    body: JSON.stringify({ question, history }),
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
      else if (kind === "view") onEvent({ kind, view: parsed as View });
    }
    if (done) throw new Error(REFUSED);
  }
}

export interface ReportSection {
  key: string;
  title: string;
  text: string;
  views: View[];
  sources: string[];
  flagged: string[];
  retried: boolean;
}

export interface ReportResult {
  company: { company_id: number; name: string };
  as_of: string;
  model: string;
  verified: boolean;
  summary: string;
  sections: ReportSection[];
  unknown: string[];
  seconds: number;
  markdown: string;
}

/** 기업 분석 보고서. 항목이 끝날 때마다 onSection 이 불리고, 다 끝나면 보고서 전체를 돌려준다 */
export async function reportStream(companyId: number, onSection: (section: ReportSection) => void): Promise<ReportResult> {
  const response = await fetch("/api/report/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...ownerHeaders() },
    body: JSON.stringify({ company_id: companyId }),
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
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const kind = /^event: (.+)$/m.exec(block)?.[1];
      const data = /^data: (.*)$/m.exec(block)?.[1];
      if (!kind || data === undefined) continue;
      const parsed = JSON.parse(data);
      if (kind === "done") return parsed as ReportResult;
      if (kind === "error") throw new Error(typeof parsed === "string" ? parsed : REFUSED);
      if (kind === "section") onSection(parsed as ReportSection);
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

/** 내 종목 탭 (company_graph/portfolio.py). 종목 목록은 이 브라우저에만 있고, 보낼 때마다 서버가 정리해 돌려준다 */
export interface PortfolioMove {
  day: string;
  /** 이 종목의 마지막 시세가 다른 종목보다 이르다 (거래 정지 등) */
  stale: boolean;
  d1: number | null;
  d5?: number | null;
  d20?: number | null;
  /** 최근 20거래일의 누적 등락률(%) */
  spark: number[];
}
export interface PortfolioStock {
  id: number;
  name: string;
  stock_code: string | null;
  market: string | null;
  listed: boolean;
  /** 보유 수량 × 최근 종가로 계산한 비중(%). 관심 종목이나 시세가 없으면 null */
  weight: number | null;
  move: PortfolioMove | null;
}
export interface PortfolioEvent {
  date: string;
  company_id: number;
  company: string;
  kind: string;
  text: string;
  rcept_no: string;
  url: string;
  tone: "deal" | "capital" | "holder" | "report" | "info";
}
export interface PortfolioIssue {
  day: string;
  company_id: number;
  company: string;
  side: "up" | "down";
  change: number;
  group: number | null;
  together: string[];
  why: string[];
  grade: string | null;
  reason: string | null;
  sources: { title: string; url: string }[];
}
export interface PortfolioResult {
  as_of: string;
  price_day: string | null;
  since: string;
  stocks: PortfolioStock[];
  events: PortfolioEvent[];
  issues: PortfolioIssue[];
  note: string;
}
export async function fetchPortfolio(items: { id: number; qty: number | null }[], days: number): Promise<PortfolioResult> {
  const response = await fetch("/api/portfolio", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items, days }) });
  if (!response.ok) throw new Error(`/portfolio ${response.status}`);
  return response.json();
}
export const fetchView = (kind: string, companyId: number, asOf: string) => get<View>("/view", { kind, company_id: companyId, as_of: asOf });

/** 뉴스 메모의 관련 종목 한 줄 */
export interface MemoRow {
  company_id: number;
  name: string | null;
  stock_code: string | null;
  link: string;
  closeness: string;
  fact: string;
  why: string;
  rcept_nos: string[];
  /** 고쳐 쓴 뒤에도 조회 결과에서 찾지 못한 숫자 (숫자 검사를 켠 메모만) */
  unchecked_numbers?: string[];
}

export interface Memo {
  summary: string;
  subjects: { company_id: number; name: string | null; stock_code: string | null; role: string }[];
  topics: string[];
  subject_facts: { fact: string; rcept_nos: string[]; unchecked_numbers?: string[] }[];
  related: MemoRow[];
  unknown: string[];
  next: string[];
}

export interface MemoResult {
  news: { title: string; date: string };
  as_of: string;
  model: string;
  memo: Memo | null;
  dropped: { name: string | null; link: string | null }[];
  first_problems: string[];
  seconds: number;
  tokens: number;
  tools: { name: string; input: Record<string, unknown>; total: number | null; error: string | null }[];
  sources: Record<string, { url: string; report: string; filed: string; company: string | null }>;
  graph: GraphData;
  left_for_you: number | null;
}

/** 뉴스 메모를 만드는 동안 서버가 보내는 것 */
export type MemoEvent =
  | { kind: "say"; text: string }
  | { kind: "tool"; name: string; input: Record<string, unknown> }
  | { kind: "result"; name: string; total: number | null; error: string | null; receipts: number; companies: number[] }
  | { kind: "check"; problems: string[]; related: number }
  | { kind: "memo"; related: number; left: number; retried: boolean }
  | { kind: "plan"; look_for: string; steps: { do: string; tool: string }[]; changed: string | null; after_calls: number };

/** 뉴스 한 건으로 메모를 만든다. 조회할 때마다 onEvent 가 불리고, 다 끝나면 메모 전체를 돌려준다 */
export async function memoStream(news: { title: string; text?: string; day?: string }, onEvent: (event: MemoEvent) => void): Promise<MemoResult> {
  const response = await fetch("/api/memo/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...ownerHeaders() },
    body: JSON.stringify(news),
  });
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? "뉴스 제목은 5자 이상, 200자 이하로 적어 주세요." : REFUSED);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const kind = /^event: (.+)$/m.exec(block)?.[1];
      const data = /^data: (.*)$/m.exec(block)?.[1];
      if (!kind || data === undefined) continue;
      const parsed = JSON.parse(data);
      if (kind === "done") return parsed as MemoResult;
      if (kind === "error") throw new Error(typeof parsed === "string" ? parsed : REFUSED);
      onEvent({ kind, ...parsed } as MemoEvent);
    }
    if (done) throw new Error(REFUSED);
  }
}

/** Agent가 매일 아침 스스로 고른 뉴스 한 건 (목록) */
export interface DailyMemoItem {
  id: number;
  day: string;
  title: string;
  source: string | null;
  company: string | null;
  picked: DailyPicked;
  related: number;
  /** 열지 않고 보는 한 줄: 주인공의 뉴스 뒤 누적 %와 뉴스 뒤 주인공의 공시 */
  glance?: MemoGlance;
}

export interface MemoGlance {
  /** 뉴스 날부터 센 거래일 수 (0이면 시세가 없음) */
  days: number;
  change: number | null;
  vs_median: number | null;
  /** 뉴스 뒤 주인공의 공시 수, 그 가운데 공급계약, 금액이 뉴스 제목과 같은 것 */
  count: number;
  contracts: number;
  same_amount: number;
}

/** 고른 까닭과 그날의 수: 중요도, 종류, 그날 모은 제목 수, 핵심 뉴스 수 */
export interface DailyPicked {
  imp: number;
  kind: string;
  dir: number;
  kw: string[];
  companies: number;
  with_news: number;
  titles: number;
  core: number;
}

/** 메모 하나: 그때의 사건과 결과 (화면이 다시 보여 준다) */
export interface DailyMemo {
  news: { title: string; day: string };
  events: { at: number; event: MemoEvent }[];
  result: MemoResult;
  picked: DailyPicked;
  link: string | null;
  source: string | null;
}

export const fetchDailyMemos = () => get<DailyMemoItem[]>("/memos");
export const fetchDailyMemo = (id: number) => get<DailyMemo>(`/memos/${id}`);

/** 뉴스 메모의 회사들이 뉴스 뒤에 어떻게 움직였는지. 뉴스 전 거래일 종가를 0으로 둔 누적 %(가격은 오지 않는다) */
export interface MemoPrices {
  news_day: string;
  base_day: string | null;
  days: string[];
  /** 시가총액 가중 */
  market: (number | null)[];
  /** 보통 종목(수익률 중앙값) */
  median: (number | null)[];
  companies: { company_id: number; name: string; series: (number | null)[] }[];
  source: string;
  note?: string;
}
export const fetchMemoPrices = (day: string, ids: number[]) => get<MemoPrices>("/memo-prices", { day, ids: ids.join(",") });

/** 분석의 조회 시점 뒤에 주인공이 낸 공시와, 뉴스 제목의 금액과 같은지 */
export interface FollowupFiling {
  company_id: number;
  rcept_no: string;
  filed: string;
  kind: string;
  report: string;
  amount: number | null;
  party: string | null;
  what: string | null;
  corrected: boolean;
  same_amount: boolean;
  url: string;
}
export interface MemoFollowups {
  after: string;
  until: string;
  checked_through: string | null;
  filings: FollowupFiling[];
  count: number;
  contracts: number;
  same_amount: number;
  news_amounts: number[];
}
export const fetchMemoFollowups = (after: string, ids: number[], title: string) =>
  get<MemoFollowups>("/memo-followups", { after, ids: ids.join(","), title });
