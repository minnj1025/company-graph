import { forceX, forceY, forceZ } from "d3-force-3d";
import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D, { type ForceGraphMethods } from "react-force-graph-3d";
import SpriteText from "three-spritetext";
import { LINK_COLORS, nodeColor, type ColorBy } from "./colors";
import type { GraphData, GraphLink, GraphNode } from "./types";

const DIM_NODE = "#3a465c";
const DIM_LINK = "#252f42";
const MAX_LABELS = 45;

export const shortName = (name: string) =>
  name.replace(/\(주\)|㈜|주식회사|유한회사/g, "").trim();

const endId = (end: number | GraphNode) => (typeof end === "number" ? end : end.id);

interface Props {
  data: GraphData;
  colorBy: ColorBy;
  groups: Map<string, string>;
  selectedId: number | null;
  onSelect: (node: GraphNode | null) => void;
  /** 이 값이 바뀌면 그래프 전체가 보이게 카메라를 맞춘다 */
  fitKey: string;
}

export function Graph({ data, colorBy, groups, selectedId, onSelect, fitKey }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const graph = useRef<ForceGraphMethods<GraphNode, GraphLink> | undefined>(undefined);
  const [size, setSize] = useState({ width: 800, height: 600 });
  const needsFit = useRef(true);
  const fitted = useRef(fitKey);

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) =>
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height }),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // 점끼리 더 밀어내고 선을 길게 잡아야 뭉치지 않고 구조가 보인다
  useEffect(() => {
    const charge = graph.current?.d3Force("charge") as { strength?: (v: number) => void } | undefined;
    const link = graph.current?.d3Force("link") as { distance?: (fn: (l: GraphLink) => number) => void } | undefined;
    charge?.strength?.(-320);
    link?.distance?.((l) => (l.type === "affiliate" ? 130 : l.type === "equity" ? 85 : 100));
    // 다른 기업과 이어지지 않은 작은 무리가 멀리 날아가지 않게 가운데로 살짝 당긴다
    graph.current?.d3Force("x", forceX(0).strength(0.09) as never);
    graph.current?.d3Force("y", forceY(0).strength(0.09) as never);
    graph.current?.d3Force("z", forceZ(0).strength(0.09) as never);
    graph.current?.d3ReheatSimulation();
    // 보는 범위와 자료가 한 번에 바뀌는 경우(질문 결과로 바꿀 때)에도 맞추도록 여기서도 확인한다
    if (fitted.current !== fitKey) {
      fitted.current = fitKey;
      needsFit.current = true;
    }
    if (!needsFit.current) return;
    // 보는 범위를 바꿨을 때만 전체가 보이게 맞춘다. 날짜만 바꿀 때는 시점을 그대로 둔다
    needsFit.current = false;
    const fit = () => {
      const placed = data.nodes.filter((n) => n.x !== undefined);
      if (placed.length === 0) return;
      const mean = (pick: (n: GraphNode) => number) => placed.reduce((sum, n) => sum + pick(n), 0) / placed.length;
      const [cx, cy, cz] = [mean((n) => n.x!), mean((n) => n.y!), mean((n) => n.z!)];
      // 가장 먼 점 몇 개 때문에 전체가 작아지지 않게, 거리 순으로 92% 지점까지를 화면에 담는다
      const distances = placed.map((n) => Math.hypot(n.x! - cx, n.y! - cy, n.z! - cz)).sort((a, b) => a - b);
      const radius = Math.max(placed.length < 30 ? 70 : 170, distances[Math.floor((distances.length - 1) * 0.92)]);
      graph.current?.cameraPosition({ x: cx, y: cy, z: cz + radius * 2.5 }, { x: cx, y: cy, z: cz }, 900);
    };
    const timers = [1500, 5000].map((ms) => setTimeout(fit, ms));
    return () => timers.forEach(clearTimeout);
  }, [data]);

  useEffect(() => {
    // 범위만 먼저 바뀌고 자료는 나중에 오는 경우. 위에서 이미 처리했으면 다시 맞추지 않는다
    if (fitted.current === fitKey) return;
    fitted.current = fitKey;
    needsFit.current = true;
  }, [fitKey]);

  // 선택한 기업과 바로 이어진 기업만 밝게 둔다
  const lit = useMemo(() => {
    if (selectedId === null) return null;
    const ids = new Set<number>([selectedId]);
    for (const link of data.links) {
      const [source, target] = [endId(link.source), endId(link.target)];
      if (source === selectedId) ids.add(target);
      if (target === selectedId) ids.add(source);
    }
    return ids;
  }, [data, selectedId]);

  // 이름표는 연결이 많은 기업부터. 전부 붙이면 글자가 겹쳐 읽을 수 없다
  const labeled = useMemo(() => {
    const ranked = [...data.nodes].sort((a, b) => b.degree - a.degree).slice(0, MAX_LABELS);
    return new Set(ranked.map((n) => n.id));
  }, [data]);

  const isLit = (id: number) => lit === null || lit.has(id);
  const linkLit = (link: GraphLink) =>
    selectedId === null || endId(link.source) === selectedId || endId(link.target) === selectedId;

  return (
    <div ref={container} className="graph">
      <ForceGraph3D
        ref={graph}
        width={size.width}
        height={size.height}
        graphData={data}
        backgroundColor="#131a29"
        showNavInfo={false}
        nodeId="id"
        nodeLabel={(node) =>
          node.kind === "topic" ? `사업 낱말 "${node.name}" · 보고서에 이 말이 나온 기업 ${node.degree}곳` : `${node.name}${node.group ? ` · ${node.group}` : ""} · ${node.sector}`
        }
        nodeVal={(node) => (node.focus ? 12 : 1.2 + node.degree * 0.45)}
        nodeRelSize={4}
        nodeOpacity={0.92}
        nodeColor={(node) => (isLit(node.id) ? nodeColor(node, colorBy, groups) : DIM_NODE)}
        nodeThreeObjectExtend
        nodeThreeObject={(node) => {
          const show = node.focus || node.id === selectedId || (isLit(node.id) && (lit !== null || labeled.has(node.id)));
          if (!show) return new SpriteText("");
          const label = new SpriteText(node.kind === "topic" ? `# ${node.name}` : shortName(node.name));
          label.color = node.kind === "topic" ? "#5ad1c9" : "#e8edf5";
          label.textHeight = node.focus || node.id === selectedId ? 9 : 6;
          label.fontFace = "Pretendard, 'Malgun Gothic', sans-serif";
          label.fontWeight = "600";
          label.position.y = 7 + Math.cbrt(node.focus ? 12 : 1.2 + node.degree * 0.45) * 3;
          return label;
        }}
        linkColor={(link) => (linkLit(link) ? LINK_COLORS[link.type] : DIM_LINK)}
        linkOpacity={0.55}
        linkWidth={(link) => {
          if (!linkLit(link)) return 0.1;
          if (link.type === "equity") return 0.4 + (link.value ?? 0) / 35;
          if (link.type === "business") return 0.3 + Math.min(link.count, 40) / 16;
          return link.type === "affiliate" ? 0.15 : 0.9;
        }}
        linkLabel={(link) =>
          `${shortName((link.source as GraphNode).name)} → ${shortName((link.target as GraphNode).name)} · ${link.label}`
        }
        linkDirectionalArrowLength={(link) => (link.type === "affiliate" ? 0 : 3)}
        linkDirectionalArrowRelPos={1}
        linkDirectionalParticles={(link) => (link.type === "supply_contract" && linkLit(link) ? 2 : 0)}
        linkDirectionalParticleWidth={1.4}
        linkDirectionalParticleSpeed={0.006}
        onNodeClick={(node) => {
          // 낱말 점은 기업이 아니라서 상세를 열지 않고, 이어진 기업만 밝힌다
          onSelect(node);
          const [x, y, z] = [node.x ?? 0, node.y ?? 0, node.z ?? 0];
          const ratio = 1 + 150 / Math.max(Math.hypot(x, y, z), 1);
          graph.current?.cameraPosition({ x: x * ratio, y: y * ratio, z: z * ratio }, { x, y, z }, 900);
        }}
        onNodeDragEnd={(node) => {
          // 끌어다 놓은 자리에 고정한다
          node.fx = node.x;
          node.fy = node.y;
          node.fz = node.z;
        }}
        onBackgroundClick={() => onSelect(null)}
        warmupTicks={80}
        cooldownTime={7000}
      />
    </div>
  );
}
