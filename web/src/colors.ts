import type { GraphNode, RelType } from "./types";

export type ColorBy = "group" | "stage";

export const LINK_COLORS: Record<RelType, string> = {
  equity: "#7aa2ff",
  supply_contract: "#ffb454",
  affiliate: "#5d6b82",
};

export const LINK_LABELS: Record<RelType, string> = {
  equity: "지분",
  supply_contract: "공급계약",
  affiliate: "계열",
};

const STAGE_COLORS: Record<string, string> = {
  완성차: "#ff6b6b",
  "자동차 부품": "#ffb454",
  소재: "#c9a26b",
  "전자·전기": "#5ad1c9",
  "기계·운송장비": "#b48cff",
  "물류·판매": "#6fd08c",
  "IT·콘텐츠": "#7aa2ff",
  금융: "#f48fb1",
  건설: "#9aa7b8",
  기타: "#5d6b82",
};

const GROUP_PALETTE = ["#7aa2ff", "#ffb454", "#6fd08c", "#f48fb1", "#b48cff", "#5ad1c9", "#ff6b6b", "#c9a26b"];
const NO_GROUP = "#5d6b82";

/** 집단 이름 → 색. 화면에 많이 나온 집단부터 색을 준다. */
export function groupColors(nodes: GraphNode[]): Map<string, string> {
  const counts = new Map<string, number>();
  for (const node of nodes) if (node.group) counts.set(node.group, (counts.get(node.group) ?? 0) + 1);
  const ordered = [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([name]) => name);
  return new Map(ordered.map((name, i) => [name, GROUP_PALETTE[i % GROUP_PALETTE.length]]));
}

export function nodeColor(node: GraphNode, colorBy: ColorBy, groups: Map<string, string>): string {
  if (colorBy === "stage") return STAGE_COLORS[node.stage] ?? STAGE_COLORS["기타"];
  return node.group ? (groups.get(node.group) ?? NO_GROUP) : NO_GROUP;
}

export function legend(nodes: GraphNode[], colorBy: ColorBy, groups: Map<string, string>): [string, string][] {
  if (colorBy === "stage") {
    const present = new Set(nodes.map((n) => n.stage));
    return Object.entries(STAGE_COLORS).filter(([name]) => present.has(name));
  }
  return [...[...groups.entries()].slice(0, 7), ["집단 지정 없음", NO_GROUP]];
}
