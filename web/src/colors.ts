import type { GraphNode, LinkType } from "./types";

export type ColorBy = "group" | "sector";

export const LINK_COLORS: Record<LinkType, string> = {
  equity: "#7aa2ff",
  supply_contract: "#ffb454",
  affiliate: "#5d6b82",
  supply_termination: "#ff6b6b",
  stake_acquisition: "#6fd08c",
  stake_disposal: "#f48fb1",
  merger: "#b48cff",
  split: "#b48cff",
  business_transfer: "#b48cff",
  business: "#5ad1c9",
  product: "#d8c75a",
  family: "#8a7f4a",
};

export const LINK_LABELS: Record<LinkType, string> = {
  equity: "지분",
  supply_contract: "공급계약",
  affiliate: "계열",
  supply_termination: "공급계약 해지",
  stake_acquisition: "지분 취득 결정",
  stake_disposal: "지분 처분 결정",
  merger: "합병",
  split: "분할",
  business_transfer: "영업양수도",
  business: "사업 내용에 언급",
  product: "제품",
  family: "제품군에 속함",
};

const GROUP_PALETTE = ["#7aa2ff", "#ffb454", "#6fd08c", "#f48fb1", "#b48cff", "#5ad1c9", "#ff6b6b", "#c9a26b"];
const NO_GROUP = "#5d6b82";
const NO_SECTOR = "업종 정보 없음";

const keyOf = (node: GraphNode, colorBy: ColorBy) => (colorBy === "sector" ? node.sector : node.group);

const hash = (text: string) => [...text].reduce((sum, ch) => (sum * 31 + ch.charCodeAt(0)) >>> 0, 7);

/** 분류 이름 → 색. 화면에 많이 나온 것부터 색을 주고, 색이 모자라면 나머지는 회색이다.
 *
 * 같은 분류는 화면이 바뀌어도 같은 색이다. order(전체에서 기업 수가 많은 순서)의 앞쪽 분류에는 색을 하나씩 맡겨 두고,
 * 나머지는 이름으로 정한 자리에서 시작해 이 화면에서 아직 안 쓴 색을 찾는다. */
export function categoryColors(nodes: GraphNode[], colorBy: ColorBy, order: string[] = []): Map<string, string> {
  const counts = new Map<string, number>();
  for (const node of nodes) {
    if (node.kind) continue;
    const key = keyOf(node, colorBy);
    // 비상장 계열사는 업종 정보가 없다. 색을 주지 않고 회색으로 둔다
    if (key && key !== NO_SECTOR) counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const shown = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, GROUP_PALETTE.length).map(([name]) => name);
  const reserved = new Map(order.slice(0, GROUP_PALETTE.length).map((name, i) => [name, GROUP_PALETTE[i]]));
  const colors = new Map<string, string>();
  const used = new Set<string>();
  for (const name of shown) {
    const color = reserved.get(name);
    if (color) {
      colors.set(name, color);
      used.add(color);
    }
  }
  for (const name of shown) {
    if (colors.has(name)) continue;
    let at = hash(name) % GROUP_PALETTE.length;
    while (used.has(GROUP_PALETTE[at])) at = (at + 1) % GROUP_PALETTE.length;
    colors.set(name, GROUP_PALETTE[at]);
    used.add(GROUP_PALETTE[at]);
  }
  // 범례는 많이 나온 순서로
  return new Map(shown.map((name) => [name, colors.get(name)!]));
}

export const TOPIC_COLOR = "#5ad1c9";
export const PRODUCT_COLOR = "#d8c75a";
export const FAMILY_COLOR = "#ff9f5a";
export const CLASS_COLOR = "#e6e8ec";

export function nodeColor(node: GraphNode, colorBy: ColorBy, colors: Map<string, string>): string {
  if (node.kind) return { topic: TOPIC_COLOR, family: FAMILY_COLOR, class: CLASS_COLOR, product: PRODUCT_COLOR }[node.kind];
  const key = keyOf(node, colorBy);
  return key ? (colors.get(key) ?? NO_GROUP) : NO_GROUP;
}

export function legend(colorBy: ColorBy, colors: Map<string, string>): [string, string][] {
  return [...colors.entries(), [colorBy === "sector" ? "그 밖의 업종 · 정보 없음" : "그 밖의 집단 · 지정 없음", NO_GROUP]];
}
