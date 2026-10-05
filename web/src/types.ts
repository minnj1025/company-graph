export type RelType = "equity" | "supply_contract" | "affiliate";

export interface Company {
  id: number;
  name: string;
  stock_code: string | null;
  listed: boolean;
  group: string | null;
  stage: string;
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
  type: RelType;
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
  type: RelType;
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
}
