import { forceX, forceY, forceZ } from "d3-force-3d";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D, { type ForceGraphMethods } from "react-force-graph-3d";
import {
  BufferAttribute,
  BufferGeometry,
  Color,
  ConeGeometry,
  InstancedMesh,
  LineBasicMaterial,
  LineSegments,
  Matrix4,
  Mesh,
  MeshBasicMaterial,
  MeshLambertMaterial,
  Quaternion,
  SphereGeometry,
  Vector3,
} from "three";
import SpriteText from "three-spritetext";
import { LINK_COLORS, nodeColor, PRODUCT_COLOR, TOPIC_COLOR, type ColorBy } from "./colors";
import type { GraphData, GraphLink, GraphNode } from "./types";

const DIM_NODE = "#273140";
const DIM_LINK = "#18202b";
/** 답에 나온 기업의 이름표 바탕. 그래프의 다른 기업과 한눈에 갈리게 한다 */
export const MENTIONED = "#ffd666";
const MAX_LABELS = 45;
const REL_SIZE = 4;
const ARROW = 3;
/** 선이 이보다 많으면 선과 화살표를 하나로 묶어 그린다. 굵기·흐르는 점·선 설명은 기업을 골랐을 때 그 기업의 선에만 남는다 */
const BATCH_LINKS = 600;

export const shortName = (name: string) =>
  name.replace(/\(주\)|㈜|주식회사|유한회사/g, "").trim();

const endId = (end: number | GraphNode) => (typeof end === "number" ? end : end.id);
const nodeVal = (node: GraphNode) => (node.focus ? 12 : 1.2 + node.degree * 0.45);
const nodeRadius = (node: GraphNode) => Math.cbrt(nodeVal(node)) * REL_SIZE;

// 점마다 물체를 하나씩 그리면 기업 1,800곳에서 그리기 명령이 수천 번이 된다.
// 보이는 공은 한 번에 그리고(InstancedMesh), 점마다 두는 것은 누르고 끌기 위한 보이지 않는 공뿐이다
const BALL = new SphereGeometry(1, 12, 8);
const CONE = new ConeGeometry(ARROW * 0.25, ARROW, 6);
const HIT = new MeshBasicMaterial({ visible: false });
const hitShapes = new Map<number, SphereGeometry>();
const hitShape = (radius: number) => {
  const key = Math.round(radius * 10);
  let shape = hitShapes.get(key);
  if (!shape) hitShapes.set(key, (shape = new SphereGeometry(key / 10, 6, 4)));
  return shape;
};

const UP = new Vector3(0, 1, 0);
const ONE = new Vector3(1, 1, 1);
const NOTHING = new Matrix4().makeScale(0, 0, 0);
const matrix = new Matrix4();
const turn = new Quaternion();
const from = new Vector3();
const to = new Vector3();
const paint = new Color();

interface Layer {
  data: GraphData;
  balls: InstancedMesh;
  lines?: LineSegments<BufferGeometry, LineBasicMaterial>;
  arrows?: InstancedMesh;
}

interface Props {
  data: GraphData;
  colorBy: ColorBy;
  groups: Map<string, string>;
  selectedId: number | null;
  onSelect: (node: GraphNode | null) => void;
  /** 이 값이 바뀌면 그래프 전체가 보이게 카메라를 맞춘다 */
  fitKey: string;
}

export const Graph = memo(function Graph({ data, colorBy, groups, selectedId, onSelect, fitKey }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const graph = useRef<ForceGraphMethods<GraphNode, GraphLink> | undefined>(undefined);
  const [size, setSize] = useState({ width: 800, height: 600 });
  const needsFit = useRef(true);
  const fitted = useRef(fitKey);
  const layer = useRef<Layer | null>(null);
  const selected = useRef(selectedId);
  selected.current = selectedId;
  const batch = data.links.length > BATCH_LINKS;
  // 처음 자리를 잡을 때만 화면에 그리기 전에 미리 계산한다. 앞선 화면의 위치를 이어받았으면 건너뛰어 멈칫하지 않게 한다
  const warmup = useMemo(() => (data.nodes.filter((n) => n.x === undefined).length * 2 > data.nodes.length ? 80 : 0), [data]);

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
    link?.distance?.((l) => (l.type === "affiliate" ? 130 : l.type === "equity" ? 85 : l.type === "product" ? 70 : 100));
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

  /** 묶어 그리는 물체를 점의 지금 위치에 맞춘다. 배치 계산이 한 걸음 나아갈 때마다 부른다 */
  const place = useCallback(() => {
    const made = layer.current;
    if (!made) return;
    made.data.nodes.forEach((node, i) => {
      if (node.x === undefined) return made.balls.setMatrixAt(i, NOTHING);
      const radius = nodeRadius(node);
      made.balls.setMatrixAt(i, matrix.makeScale(radius, radius, radius).setPosition(node.x, node.y ?? 0, node.z ?? 0));
    });
    made.balls.instanceMatrix.needsUpdate = true;
    const { lines, arrows } = made;
    if (!lines || !arrows) return;
    const at = lines.geometry.getAttribute("position") as BufferAttribute;
    const points = at.array as Float32Array;
    made.data.links.forEach((link, i) => {
      const { source, target } = link;
      if (
        typeof source === "number" ||
        typeof target === "number" ||
        source.x === undefined ||
        target.x === undefined ||
        // 고른 기업에 닿은 선은 라이브러리가 굵기와 화살표를 갖춰 따로 그린다
        source.id === selected.current ||
        target.id === selected.current
      ) {
        points.fill(0, i * 6, i * 6 + 6);
        return arrows.setMatrixAt(i, NOTHING);
      }
      from.set(source.x, source.y ?? 0, source.z ?? 0);
      to.set(target.x, target.y ?? 0, target.z ?? 0);
      from.toArray(points, i * 6);
      to.toArray(points, i * 6 + 3);
      const gap = from.distanceTo(to);
      if (link.type === "affiliate" || link.type === "product" || gap < 1e-6) return arrows.setMatrixAt(i, NOTHING);
      from.subVectors(to, from).divideScalar(gap);
      turn.setFromUnitVectors(UP, from);
      to.addScaledVector(from, -(nodeRadius(target) + ARROW / 2));
      arrows.setMatrixAt(i, matrix.compose(to, turn, ONE));
    });
    at.needsUpdate = true;
    arrows.instanceMatrix.needsUpdate = true;
  }, []);

  useEffect(() => {
    const scene = graph.current?.scene();
    if (!scene) return;
    const balls = new InstancedMesh(BALL, new MeshLambertMaterial(), Math.max(data.nodes.length, 1));
    balls.count = data.nodes.length;
    const made: Layer = { data, balls };
    if (batch) {
      const shape = new BufferGeometry();
      shape.setAttribute("position", new BufferAttribute(new Float32Array(data.links.length * 6), 3));
      shape.setAttribute("color", new BufferAttribute(new Float32Array(data.links.length * 6), 3));
      made.lines = new LineSegments(shape, new LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.55 }));
      made.arrows = new InstancedMesh(CONE, new MeshLambertMaterial(), data.links.length);
    }
    const parts = [made.balls, made.lines, made.arrows].filter((part) => part !== undefined);
    // 점이 움직여도 물체의 테두리 계산은 처음 것이라, 화면 밖이라고 잘못 걸러지지 않게 한다
    for (const part of parts) part.frustumCulled = false;
    scene.add(...parts);
    layer.current = made;
    return () => {
      layer.current = null;
      scene.remove(...parts);
      balls.material.dispose();
      balls.dispose();
      made.lines?.geometry.dispose();
      made.lines?.material.dispose();
      (made.arrows?.material as MeshLambertMaterial | undefined)?.dispose();
      made.arrows?.dispose();
    };
  }, [data, batch]);

  useEffect(() => {
    const made = layer.current;
    if (!made) return;
    made.data.nodes.forEach((node, i) =>
      made.balls.setColorAt(i, paint.set(lit === null || lit.has(node.id) ? nodeColor(node, colorBy, groups) : DIM_NODE)),
    );
    if (made.balls.instanceColor) made.balls.instanceColor.needsUpdate = true;
    const { lines, arrows } = made;
    if (lines && arrows) {
      const colors = lines.geometry.getAttribute("color") as BufferAttribute;
      made.data.links.forEach((link, i) => {
        paint.set(selectedId === null ? LINK_COLORS[link.type] : DIM_LINK);
        paint.toArray(colors.array, i * 6);
        paint.toArray(colors.array, i * 6 + 3);
        arrows.setColorAt(i, paint);
      });
      colors.needsUpdate = true;
      if (arrows.instanceColor) arrows.instanceColor.needsUpdate = true;
    }
    place();
  }, [data, batch, lit, selectedId, colorBy, groups, place]);

  // 아래 함수들은 값이 바뀔 때만 새로 만든다. 그릴 때마다 새 함수를 넘기면 라이브러리가 점과 선을 전부 다시 만든다
  const nodeLabel = useCallback(
    (node: GraphNode) =>
      node.kind === "topic"
        ? `사업 낱말 "${node.name}" · 보고서에 이 말이 나온 기업 ${node.degree}곳`
        : node.kind === "product"
          ? `제품 "${node.name}" · 보고서의 제품 표에 이것을 적은 기업 ${node.degree}곳`
          : `${node.name}${node.group ? ` · ${node.group}` : ""} · ${node.sector}`,
    [],
  );
  const nodeObject = useCallback(
    (node: GraphNode) => {
      const hit = new Mesh(hitShape(nodeRadius(node)), HIT);
      const chosen = node.focus || node.mentioned || node.id === selectedId;
      const show = chosen || (lit === null ? labeled.has(node.id) : lit.has(node.id));
      if (!show) return hit;
      const label = new SpriteText(node.kind === "topic" ? `# ${node.name}` : shortName(node.name));
      label.color = node.kind === "topic" ? TOPIC_COLOR : node.kind === "product" ? PRODUCT_COLOR : "#e8edf5";
      if (node.mentioned) {
        label.color = "#201600";
        label.backgroundColor = MENTIONED;
        label.padding = 1.5;
        label.borderRadius = 2.5;
      }
      label.textHeight = chosen ? 9 : 6;
      label.fontFace = "Pretendard, 'Malgun Gothic', sans-serif";
      label.fontWeight = "600";
      label.position.y = 7 + Math.cbrt(nodeVal(node)) * 3;
      hit.add(label);
      return hit;
    },
    [selectedId, lit, labeled],
  );
  const linkLit = useCallback(
    (link: GraphLink) => selectedId === null || endId(link.source) === selectedId || endId(link.target) === selectedId,
    [selectedId],
  );
  const linkShown = useCallback((link: GraphLink) => !batch || (selectedId !== null && linkLit(link)), [batch, selectedId, linkLit]);
  const linkColor = useCallback((link: GraphLink) => (linkLit(link) ? LINK_COLORS[link.type] : DIM_LINK), [linkLit]);
  const linkWidth = useCallback(
    (link: GraphLink) => {
      if (!linkLit(link)) return 0.1;
      if (link.type === "equity") return 0.4 + (link.value ?? 0) / 35;
      if (link.type === "business") return 0.3 + Math.min(link.count, 40) / 16;
      if (link.type === "product") return 0.3 + (link.value ?? 0) / 30;
      return link.type === "affiliate" ? 0.15 : 0.9;
    },
    [linkLit],
  );
  const linkLabel = useCallback(
    (link: GraphLink) => `${shortName((link.source as GraphNode).name)} → ${shortName((link.target as GraphNode).name)} · ${link.label}`,
    [],
  );
  const arrowLength = useCallback((link: GraphLink) => (link.type === "affiliate" || link.type === "product" ? 0 : ARROW), []);
  const particles = useCallback((link: GraphLink) => (link.type === "supply_contract" && linkLit(link) ? 2 : 0), [linkLit]);
  const onNodeClick = useCallback(
    (node: GraphNode) => {
      // 낱말 점은 기업이 아니라서 상세를 열지 않고, 이어진 기업만 밝힌다
      onSelect(node);
      const [x, y, z] = [node.x ?? 0, node.y ?? 0, node.z ?? 0];
      const ratio = 1 + 150 / Math.max(Math.hypot(x, y, z), 1);
      graph.current?.cameraPosition({ x: x * ratio, y: y * ratio, z: z * ratio }, { x, y, z }, 900);
    },
    [onSelect],
  );
  const onNodeDragEnd = useCallback((node: GraphNode) => {
    // 끌어다 놓은 자리에 고정한다
    node.fx = node.x;
    node.fy = node.y;
    node.fz = node.z;
  }, []);
  const onBackgroundClick = useCallback(() => onSelect(null), [onSelect]);

  return (
    <div ref={container} className="graph">
      <ForceGraph3D
        ref={graph}
        width={size.width}
        height={size.height}
        graphData={data}
        backgroundColor="#0b0f16"
        showNavInfo={false}
        nodeId="id"
        nodeLabel={nodeLabel}
        nodeVal={nodeVal}
        nodeRelSize={REL_SIZE}
        nodeThreeObject={nodeObject}
        linkVisibility={linkShown}
        linkColor={linkColor}
        linkOpacity={0.55}
        linkWidth={linkWidth}
        linkLabel={linkLabel}
        linkDirectionalArrowLength={arrowLength}
        linkDirectionalArrowRelPos={1}
        linkDirectionalParticles={particles}
        linkDirectionalParticleWidth={1.4}
        linkDirectionalParticleSpeed={0.006}
        onNodeClick={onNodeClick}
        onNodeDrag={place}
        onNodeDragEnd={onNodeDragEnd}
        onBackgroundClick={onBackgroundClick}
        onEngineTick={place}
        warmupTicks={warmup}
        cooldownTime={7000}
      />
    </div>
  );
});
