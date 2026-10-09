import type { GraphNode, LinkType } from "./types";

export type ColorBy = "group" | "sector";

// 선은 점보다 조용해야 한다 (선이 점보다 몇 배 많아서, 선이 진하면 덩어리만 보인다).
// 그래서 선은 채도를 낮춘 색으로, 점은 밝고 또렷한 색으로 쓰고, 둘이 같은 색을 나눠 쓰지 않는다.
// 지분(푸른 회색)과 공급계약(누런 갈색)은 색각 이상에서도 갈리는 파랑·주황 짝이다.
export const LINK_COLORS: Record<LinkType, string> = {
  equity: "#7f93b8",
  supply_contract: "#c0935c",
  affiliate: "#5f636b",
  supply_termination: "#c46a5e",
  stake_acquisition: "#79a58c",
  stake_disposal: "#b0839a",
  merger: "#9a8fb8",
  split: "#9a8fb8",
  business_transfer: "#9a8fb8",
  business: "#7fb3ad",
  product: "#a8975c",
  family: "#7a7150",
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

// 점의 색. 한 화면에서 색으로 가려 볼 수 있는 것은 여섯 가지쯤이라 여섯 색만 쓰고 나머지는 회색으로 둔다.
// 색각 이상에서도 갈리는 조합(Okabe-Ito)을 어두운 바탕에 맞게 밝힌 것이다: 하늘, 주홍, 초록, 분홍, 연보라, 연두
const GROUP_PALETTE = ["#56b4e9", "#f0704a", "#3fc79a", "#e389b9", "#b79cf2", "#b5d46a"];
const NO_GROUP = "#6b6e76";
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

export const TOPIC_COLOR = "#7fd0c8";
// 제품 쪽 점은 금색 한 계열로: 공식 분류는 흰색, 제품군은 금색, 제품은 옅은 금색 (크기도 그 순서로 작아진다)
export const PRODUCT_COLOR = "#b9a465";
export const FAMILY_COLOR = "#f2c14e";
export const CLASS_COLOR = "#f2f2f2";

export function nodeColor(node: GraphNode, colorBy: ColorBy, colors: Map<string, string>): string {
  if (node.kind) return { topic: TOPIC_COLOR, family: FAMILY_COLOR, class: CLASS_COLOR, product: PRODUCT_COLOR }[node.kind];
  const key = keyOf(node, colorBy);
  return key ? (colors.get(key) ?? NO_GROUP) : NO_GROUP;
}

export function legend(colorBy: ColorBy, colors: Map<string, string>): [string, string][] {
  return [...colors.entries(), [colorBy === "sector" ? "그 밖의 업종 · 정보 없음" : "그 밖의 집단 · 지정 없음", NO_GROUP]];
}
