export type RelType = "equity" | "supply_contract" | "affiliate" | "stake_acquisition" | "stake_disposal";
/** 사건형 관계. 첫 화면의 그래프에는 그리지 않고, 기업 상세와 Agent가 찾은 결과에 나온다 */
export type EventType = "supply_termination" | "merger" | "split" | "business_transfer";
export type LinkType = RelType | EventType;

export interface Company {
  id: number;
  name: string;
  stock_code: string | null;
  listed: boolean;
  group: string | null;
  stage: string;
  /** 업종 (KSIC 중분류를 묶은 것) */
  sector: string;
  market: string | null;
  in_scope: boolean;
}

export interface GraphNode extends Company {
  degree: number;
  focus: boolean;
  x?: number;
  y?: number;
  z?: number;
  fx?: number;
  fy?: number;
  fz?: number;
}

export interface GraphLink {
  source: number | GraphNode;
  target: number | GraphNode;
  type: LinkType;
  count: number;
  value?: number;
  label: string;
}

export interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}

export interface Evidence {
  rcept_no: string;
  url: string;
}

export interface RelationRow {
  type: RelType | EventType;
  label: string;
  subject_id: number;
  subject: string;
  object_id: number | null;
  object: string;
  value: number | null;
  unit: "pct" | "krw" | null;
  as_of_date: string | null;
  disclosed_date: string;
  title: string | null;
  joint_parties: number | null;
  /** 지분 취득·처분 결정 뒤의 지분율(%) */
  pct_after: string | null;
  trust_tier: number;
  /** 누가 공시했나: 주체, 상대, 양쪽 */
  disclosed_by: "subject" | "object" | "both";
  /** 기준일이 오래됐고 그 뒤 보고서가 없다 */
  stale: boolean;
  evidence: Evidence[];
}

export interface CompanyDetail {
  company: Company;
  as_of: string;
  relations: RelationRow[];
  group: { count: number; source: Evidence[]; members: string[] };
}

export interface Meta {
  first_date: string;
  last_date: string;
  today: string;
  companies: number;
  in_scope: number;
  documents: number;
  relations: Record<string, number>;
  coverage: { notice: string; sources: Record<string, string> };
  /** 첫 화면을 나눠 볼 분류와 분류마다의 상장사 수 */
  categories: Record<"market" | "sector" | "group", { name: string; count: number }[]>;
}

export type FeedType = "supply_contract" | "supply_termination" | "stake_acquisition" | "stake_disposal";

export interface FeedItem {
  rcept_no: string;
  url: string;
  date: string;
  type: FeedType;
  label: string;
  subject_id: number;
  subject: string;
  object_id: number | null;
  object: string;
  value: number | null;
  title: string | null;
}

/** 한 번 공시된 사실이 그 뒤에 달라진 것 */
export interface ChangeItem {
  rcept_no: string;
  url: string;
  date: string;
  kind: "정정" | "해지" | "철회";
  company_id: number;
  company: string;
  what: string;
  reason: string | null;
}

export interface Insights {
  recent: FeedItem[];
  changed: ChangeItem[];
}

export interface ToolCall {
  name: string;
  input: Record<string, unknown>;
  total: number | null;
  error: string | null;
}

export interface AskResult {
  question: string;
  answer: string;
  as_of: string;
  model: string;
  seconds: number;
  tokens: number;
  tools: ToolCall[];
  unverified_citations: string[];
  /** 답이 근거로 든 공시. 조회 결과에 실제로 있었던 것만 */
  sources: { rcept_no: string; url: string; company: string | null; report: string | null; filed: string | null }[];
  graph: GraphData;
  left_for_you: number;
}

export interface AskStatus {
  enabled: boolean;
  model: string;
  left_today: number;
  left_for_you: number;
}
