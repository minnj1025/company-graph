import type { GraphNode, RelType } from "./types";

export type ColorBy = "group" | "sector";

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

const GROUP_PALETTE = ["#7aa2ff", "#ffb454", "#6fd08c", "#f48fb1", "#b48cff", "#5ad1c9", "#ff6b6b", "#c9a26b"];
const NO_GROUP = "#5d6b82";
const NO_SECTOR = "업종 정보 없음";

const keyOf = (node: GraphNode, colorBy: ColorBy) => (colorBy === "sector" ? node.sector : node.group);

/** 분류 이름 → 색. 화면에 많이 나온 것부터 색을 주고, 색이 모자라면 나머지는 회색이다. */
export function categoryColors(nodes: GraphNode[], colorBy: ColorBy): Map<string, string> {
  const counts = new Map<string, number>();
  for (const node of nodes) {
    const key = keyOf(node, colorBy);
    // 비상장 계열사는 업종 정보가 없다. 색을 주지 않고 회색으로 둔다
    if (key && key !== NO_SECTOR) counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const ordered = [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([name]) => name);
  return new Map(ordered.slice(0, GROUP_PALETTE.length).map((name, i) => [name, GROUP_PALETTE[i]]));
}

export function nodeColor(node: GraphNode, colorBy: ColorBy, colors: Map<string, string>): string {
  const key = keyOf(node, colorBy);
  return key ? (colors.get(key) ?? NO_GROUP) : NO_GROUP;
}

export function legend(colorBy: ColorBy, colors: Map<string, string>): [string, string][] {
  return [...colors.entries(), [colorBy === "sector" ? "그 밖의 업종 · 정보 없음" : "그 밖의 집단 · 지정 없음", NO_GROUP]];
}
