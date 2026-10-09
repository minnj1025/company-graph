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
import { LINK_COLORS, nodeColor, type ColorBy } from "./colors";
import type { GraphData, GraphLink, GraphNode } from "./types";

// 기업을 골랐을 때 그 기업과 이어지지 않은 것들. 어둡게 칠하는 대신 비쳐 보이게 해서, 고른 것만 남고 나머지는 배경으로 물러난다
const DIM = "#6b7890";
const DIM_NODE_OPACITY = 0.13;
const DIM_LINK_OPACITY = 0.07;
const DIM_LINK = `rgba(107, 120, 144, ${DIM_LINK_OPACITY / 0.55})`;   // 라이브러리가 그리는 선은 linkOpacity(0.55)가 곱해진다
/** 답에 나온 기업의 이름표 바탕. 그래프의 다른 기업과 한눈에 갈리게 한다 */
export const MENTIONED = "#ffd666";
const MAX_LABELS = 45;
/** 한꺼번에 그리는 선의 진하기. 선이 수천 개라 옅게 겹쳐야 점이 묻히지 않는다 */
const LINK_OPACITY = 0.42;
const REL_SIZE = 4;
const ARROW = 3;
/** 선이 이보다 많으면 선과 화살표를 하나로 묶어 그린다. 굵기·흐르는 점·선 설명은 기업을 골랐을 때 그 기업의 선에만 남는다 */
const BATCH_LINKS = 600;

export const shortName = (name: string) =>
  name.replace(/\(주\)|㈜|주식회사|유한회사/g, "").trim();

const endId = (end: number | GraphNode) => (typeof end === "number" ? end : end.id);
// 제품군 점은 기업이 수십 곳씩 달려서, 연결 수 그대로 키우면 화면을 덮는다
/** 범주 점(공식 분류, 제품군)이 그려져 있는가. 그때는 범주가 가장 큰 공이어야 해서 기업 점을 작게 묶어 둔다 */
let grouped = false;
// 크기는 층을 따른다: 공식 분류 > 제품군 > 제품 > 기업. 같은 층 안에서는 이어진 수가 많을수록 크다
const nodeVal = (node: GraphNode) => {
  if (node.kind === "class") return 70 + Math.sqrt(node.degree) * 12;
  if (node.kind === "family") return 26 + Math.sqrt(node.degree) * 6;
  if (node.kind === "product" || node.kind === "topic") return (node.focus ? 14 : 4) + Math.sqrt(node.degree) * 2;
  const own = 1.2 + Math.min(node.degree, grouped ? 14 : 60) * 0.45;
  return node.focus ? Math.max(own, 12) : own;
};
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
  /** 고른 기업과 이어지지 않아 흐리게 그리는 공. 같은 점이 balls 와 faint 가운데 한쪽에만 그려진다 */
  faint: InstancedMesh;
  lines?: LineSegments<BufferGeometry, LineBasicMaterial>;
  arrows?: InstancedMesh;
}

interface Props {
  data: GraphData;
  colorBy: ColorBy;
  groups: Map<string, string>;
  selectedId: number | null;
  onSelect: (node: GraphNode | null) => void;
  /** 보는 범위가 바뀌었다는 표시. 이 값이 바뀐 뒤 새 자료가 오면 그래프 전체가 보이게 카메라를 맞춘다 */
  fitKey: string;
  /** "화면 맞추기"를 누른 횟수. 바뀌면 자료가 그대로여도 바로 맞춘다 */
  refit: number;
}

export const Graph = memo(function Graph({ data, colorBy, groups, selectedId, onSelect, fitKey, refit }: Props) {
  grouped = data.nodes.some((node) => node.kind === "class" || node.kind === "family");
  const container = useRef<HTMLDivElement>(null);
  const graph = useRef<ForceGraphMethods<GraphNode, GraphLink> | undefined>(undefined);
  const [size, setSize] = useState({ width: 800, height: 600 });
  const needsFit = useRef(true);
  const fitted = useRef(fitKey);
  const layer = useRef<Layer | null>(null);
  const selected = useRef(selectedId);
  selected.current = selectedId;
  const litNow = useRef<Set<number> | null>(null);
  // 기업을 고르면 그 기업과 이어진 점을 한곳에 모은다. 그동안 나머지 점이 움직이지 않게 전부 제자리에 고정해 둔다
  const pinnedAll = useRef(false);
  const userPinned = useRef(new Set<number>());   // 사용자가 끌어다 놓아 고정한 점. 풀 때 이것은 남긴다
  const wantUnpin = useRef(false);
  const moving = useRef<{ finish: () => void } | null>(null);
  const home = useRef<{ position: Vector3; target: Vector3 } | null>(null);   // 모으기 전에 보던 자리
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

  const shown = useRef(data);
  shown.current = data;
  /** 그려진 점 전체가 보이게 카메라를 옮긴다 */
  const fit = useCallback(() => {
    const placed = shown.current.nodes.filter((n) => n.x !== undefined);
    if (placed.length === 0) return;
    const mean = (pick: (n: GraphNode) => number) => placed.reduce((sum, n) => sum + pick(n), 0) / placed.length;
    const [cx, cy, cz] = [mean((n) => n.x!), mean((n) => n.y!), mean((n) => n.z!)];
    // 가장 먼 점 몇 개 때문에 전체가 작아지지 않게, 거리 순으로 92% 지점까지를 화면에 담는다
    const distances = placed.map((n) => Math.hypot(n.x! - cx, n.y! - cy, n.z! - cz)).sort((a, b) => a - b);
    const radius = Math.max(placed.length < 30 ? 70 : 170, distances[Math.floor((distances.length - 1) * 0.92)]);
    graph.current?.cameraPosition({ x: cx, y: cy, z: cz + radius * 2.5 }, { x: cx, y: cy, z: cz }, 900);
  }, []);

  // 점끼리 더 밀어내고 선을 길게 잡아야 뭉치지 않고 구조가 보인다
  useEffect(() => {
    const charge = graph.current?.d3Force("charge") as { strength?: (v: number) => void } | undefined;
    const link = graph.current?.d3Force("link") as { distance?: (fn: (l: GraphLink) => number) => void } | undefined;
    charge?.strength?.(-320);
    link?.distance?.((l) => (l.type === "affiliate" ? 130 : l.type === "equity" ? 85 : l.type === "product" ? 70 : l.type === "family" ? 45 : 100));
    // 다른 기업과 이어지지 않은 작은 무리가 멀리 날아가지 않게 가운데로 살짝 당긴다
    graph.current?.d3Force("x", forceX(0).strength(0.09) as never);
    graph.current?.d3Force("y", forceY(0).strength(0.09) as never);
    graph.current?.d3Force("z", forceZ(0).strength(0.09) as never);
    graph.current?.d3ReheatSimulation();
    // 새 자료의 점은 고정돼 있지 않다
    pinnedAll.current = false;
    wantUnpin.current = false;
    userPinned.current = new Set();
    // 보는 범위와 자료가 한 번에 바뀌는 경우(질문 결과로 바꿀 때)에도 맞추도록 여기서도 확인한다
    if (fitted.current !== fitKey) {
      fitted.current = fitKey;
      needsFit.current = true;
    }
    if (!needsFit.current) return;
    // 보는 범위를 바꿨을 때만 전체가 보이게 맞춘다. 날짜만 바꿀 때는 시점을 그대로 둔다
    needsFit.current = false;
    // 점들이 모여드는 동안 따라가며 맞춘다
    const timers = [500, 1600, 5000].map((ms) => setTimeout(fit, ms));
    return () => timers.forEach(clearTimeout);
  }, [data]);

  // 버튼을 눌렀을 때. 자료가 바뀌지 않았으니 위의 effect 가 돌지 않는다
  const asked = useRef(refit);
  useEffect(() => {
    if (asked.current === refit) return;
    asked.current = refit;
    fit();
  }, [refit, fit]);

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
      const bright = litNow.current === null || litNow.current.has(node.id);
      const [shown, hidden] = bright ? [made.balls, made.faint] : [made.faint, made.balls];
      hidden.setMatrixAt(i, NOTHING);
      if (node.x === undefined) return shown.setMatrixAt(i, NOTHING);
      const radius = nodeRadius(node);
      shown.setMatrixAt(i, matrix.makeScale(radius, radius, radius).setPosition(node.x, node.y ?? 0, node.z ?? 0));
    });
    made.balls.instanceMatrix.needsUpdate = true;
    made.faint.instanceMatrix.needsUpdate = true;
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
        // 고른 기업에 닿은 선은 라이브러리가 굵기와 화살표를 갖춰 따로 그린다.
        // 고른 기업과 이어진 점들은 한곳에 모여 있어서, 거기서 다른 곳으로 가는 흐린 선도 그리지 않는다
        (litNow.current !== null && (litNow.current.has(source.id) || litNow.current.has(target.id)))
      ) {
        points.fill(0, i * 6, i * 6 + 6);
        return arrows.setMatrixAt(i, NOTHING);
      }
      from.set(source.x, source.y ?? 0, source.z ?? 0);
      to.set(target.x, target.y ?? 0, target.z ?? 0);
      from.toArray(points, i * 6);
      to.toArray(points, i * 6 + 3);
      const gap = from.distanceTo(to);
      if (link.type === "affiliate" || link.type === "product" || link.type === "family" || gap < 1e-6) return arrows.setMatrixAt(i, NOTHING);
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
    // 그리는 순서: 흐린 것(공, 선) → 고른 기업의 선과 이름표(라이브러리) → 밝은 공. 흐린 것은 깊이를 적지 않으므로
    // 밝은 것이 흐린 것 뒤에 있어도 가려지지 않는다. 순서를 지키려면 셋 다 "비치는 물체" 차례에 그려져야 해서 밝은 공도 그렇게 둔다
    const balls = new InstancedMesh(BALL, new MeshLambertMaterial({ transparent: true }), Math.max(data.nodes.length, 1));
    balls.count = data.nodes.length;
    balls.renderOrder = 1;
    // 비쳐 보이는 공은 깊이를 적지 않는다. 적으면 뒤에 있는 밝은 공을 가린다
    const faint = new InstancedMesh(BALL, new MeshLambertMaterial({ color: DIM, transparent: true, opacity: DIM_NODE_OPACITY, depthWrite: false }),
                                    Math.max(data.nodes.length, 1));
    faint.count = data.nodes.length;
    faint.renderOrder = -1;
    const made: Layer = { data, balls, faint };
    if (batch) {
      const shape = new BufferGeometry();
      shape.setAttribute("position", new BufferAttribute(new Float32Array(data.links.length * 6), 3));
      shape.setAttribute("color", new BufferAttribute(new Float32Array(data.links.length * 6), 3));
      made.lines = new LineSegments(shape, new LineBasicMaterial({ vertexColors: true, transparent: true, opacity: LINK_OPACITY }));
      made.lines.renderOrder = -1;
      made.arrows = new InstancedMesh(CONE, new MeshLambertMaterial(), data.links.length);
    }
    const parts = [made.balls, made.faint, made.lines, made.arrows].filter((part) => part !== undefined);
    // 점이 움직여도 물체의 테두리 계산은 처음 것이라, 화면 밖이라고 잘못 걸러지지 않게 한다
    for (const part of parts) part.frustumCulled = false;
    scene.add(...parts);
    layer.current = made;
    return () => {
      layer.current = null;
      scene.remove(...parts);
      balls.material.dispose();
      balls.dispose();
      faint.material.dispose();
      faint.dispose();
      made.lines?.geometry.dispose();
      made.lines?.material.dispose();
      (made.arrows?.material as MeshLambertMaterial | undefined)?.dispose();
      made.arrows?.dispose();
    };
  }, [data, batch]);

  useEffect(() => {
    const made = layer.current;
    if (!made) return;
    litNow.current = lit;
    made.data.nodes.forEach((node, i) => made.balls.setColorAt(i, paint.set(nodeColor(node, colorBy, groups))));
    if (made.balls.instanceColor) made.balls.instanceColor.needsUpdate = true;
    const { lines, arrows } = made;
    if (lines && arrows) {
      const colors = lines.geometry.getAttribute("color") as BufferAttribute;
      made.data.links.forEach((link, i) => {
        paint.set(selectedId === null ? LINK_COLORS[link.type] : DIM);
        paint.toArray(colors.array, i * 6);
        paint.toArray(colors.array, i * 6 + 3);
        arrows.setColorAt(i, paint);
      });
      // 기업을 고르면 묶어 그리는 선은 전부 이어지지 않은 선이다. 아주 옅게 두고 화살표는 뺀다
      lines.material.opacity = selectedId === null ? LINK_OPACITY : DIM_LINK_OPACITY;
      lines.material.depthWrite = selectedId === null;
      arrows.visible = selectedId === null;
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
          ? `제품 "${node.name}"${node.sector !== "제품" ? ` · 제품군 ${node.sector}` : ""} · 이것을 파는 기업 ${node.degree}곳`
          : node.kind === "family"
            ? `제품군 "${node.name}" · 분야 ${node.sector} · 이 제품군의 것을 파는 기업 ${node.degree}곳`
            : node.kind === "class"
              ? `${node.sector} · 이 분류의 것을 파는 기업 ${node.degree}곳`
          : `${node.name}${node.group ? ` · ${node.group}` : ""} · ${node.sector}`,
    [],
  );
  const nodeObject = useCallback(
    (node: GraphNode) => {
      const hit = new Mesh(hitShape(nodeRadius(node)), HIT);
      // 흐려진 점은 눌리지 않는다. 눌리면 화면을 돌리려고 끄는 것이 점을 끄는 것이 된다
      if (lit !== null && !lit.has(node.id)) hit.raycast = () => {};
      const chosen = node.focus || node.mentioned || node.id === selectedId;
      const show = chosen || (lit === null ? labeled.has(node.id) : lit.has(node.id));
      if (!show) return hit;
      const label = new SpriteText(node.kind === "topic" ? `# ${node.name}` : shortName(node.name));
      label.color = node.kind ? nodeColor(node, "group", new Map()) : "#e8edf5";
      if (node.mentioned) {
        label.color = "#201600";
        label.backgroundColor = MENTIONED;
        label.padding = 1.5;
        label.borderRadius = 2.5;
      }
      label.textHeight = chosen ? 9 : 6;
      label.fontFace = "'Noto Sans KR', 'Malgun Gothic', sans-serif";
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
  const linkShown = useCallback(
    (link: GraphLink) => {
      if (selectedId === null || lit === null) return !batch;
      if (linkLit(link)) return true;
      // 고른 기업과 이어진 점에서 다른 곳으로 가는 선은 감춘다
      return !batch && !lit.has(endId(link.source)) && !lit.has(endId(link.target));
    },
    [batch, selectedId, lit, linkLit],
  );
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
  const arrowLength = useCallback((link: GraphLink) => (["affiliate", "product", "family"].includes(link.type) ? 0 : ARROW), []);
  const particles = useCallback((link: GraphLink) => (link.type === "supply_contract" && linkLit(link) ? 2 : 0), [linkLit]);
  const onNodeClick = useCallback(
    (node: GraphNode) => {
      // 고른 점을 다시 누르면 푼다. 낱말 점과 제품 점은 기업이 아니라서 상세를 열지 않고, 이어진 기업만 밝힌다
      onSelect(node.id === selected.current ? null : node);
    },
    [onSelect],
  );
  const onNodeDragEnd = useCallback((node: GraphNode) => {
    // 끌어다 놓은 자리에 고정한다
    node.fx = node.x;
    node.fy = node.y;
    node.fz = node.z;
    userPinned.current.add(node.id);
  }, []);

  /** 점들을 from 에서 to 로 부드럽게 옮긴다. 옮기는 동안 배치 계산이 돌아야 선이 따라오므로 다시 깨운다 */
  const glide = useCallback((moves: { node: GraphNode; to: [number, number, number] }[], ms = 520) => {
    moving.current?.finish();
    if (moves.length === 0) return;
    const starts = moves.map(({ node }) => [node.x ?? 0, node.y ?? 0, node.z ?? 0]);
    const put = (k: number) =>
      moves.forEach(({ node, to }, i) => {
        node.fx = starts[i][0] + (to[0] - starts[i][0]) * k;
        node.fy = starts[i][1] + (to[1] - starts[i][1]) * k;
        node.fz = starts[i][2] + (to[2] - starts[i][2]) * k;
      });
    const began = performance.now();
    let frame = 0;
    const step = () => {
      const t = Math.min((performance.now() - began) / ms, 1);
      put(1 - (1 - t) ** 3);
      if (t < 1) frame = requestAnimationFrame(step);
      else moving.current = null;
    };
    moving.current = {
      finish: () => {
        cancelAnimationFrame(frame);
        put(1);
        for (const { node, to } of moves) [node.x, node.y, node.z] = to;
        moving.current = null;
      },
    };
    graph.current?.d3ReheatSimulation();
    frame = requestAnimationFrame(step);
  }, []);

  // 기업을 고르면: 그 기업과 이어진 점을 그 기업 둘레에 공 모양으로 모으고 카메라를 가까이 댄다. 풀면 원래 자리로 돌려보낸다
  useEffect(() => {
    if (lit === null || selectedId === null) return;
    moving.current?.finish();   // 앞서 고른 것을 돌려보내는 중이었으면 바로 끝낸다
    const center = data.nodes.find((n) => n.id === selectedId);
    if (!center || center.x === undefined) return;
    if (!pinnedAll.current) {
      pinnedAll.current = true;
      for (const node of data.nodes) if (node.fx !== undefined) userPinned.current.add(node.id);
    }
    for (const node of data.nodes) {
      if (node.x === undefined) continue;
      [node.fx, node.fy, node.fz] = [node.x, node.y, node.z];
    }
    wantUnpin.current = false;
    const around = data.nodes.filter((n) => n.id !== selectedId && lit.has(n.id) && n.x !== undefined);
    const origin: [number, number, number] = [center.x, center.y ?? 0, center.z ?? 0];
    const radius = Math.min(Math.max(34 + 9 * Math.sqrt(around.length), 48), 230);
    const back = around.map((node) => ({ node, to: [node.x!, node.y ?? 0, node.z ?? 0] as [number, number, number] }));
    glide(
      around.map((node, i) => {
        // 공 겉면에 고르게 놓는다 (피보나치 격자)
        const y = 1 - (2 * (i + 0.5)) / around.length;
        const ring = Math.sqrt(1 - y * y);
        const turn = i * Math.PI * (3 - Math.sqrt(5));
        return { node, to: [origin[0] + Math.cos(turn) * ring * radius, origin[1] + y * radius, origin[2] + Math.sin(turn) * ring * radius] };
      }),
    );
    const view = graph.current;
    if (view) {
      const eye = view.camera().position;
      const target = (view.controls() as { target: Vector3 }).target;
      if (!home.current) home.current = { position: eye.clone(), target: target.clone() };
      const direction = new Vector3(eye.x - origin[0], eye.y - origin[1], eye.z - origin[2]);
      if (direction.lengthSq() < 1) direction.set(0, 0, 1);
      direction.normalize().multiplyScalar(radius * 2.5 + 90);
      view.cameraPosition(
        { x: origin[0] + direction.x, y: origin[1] + direction.y, z: origin[2] + direction.z },
        { x: origin[0], y: origin[1], z: origin[2] },
        700,
      );
    }
    return () => {
      glide(back);
      wantUnpin.current = true;
    };
  }, [data, selectedId, lit, glide]);

  // 고른 것을 다 풀었으면 모으기 전에 보던 자리로 카메라를 돌린다
  useEffect(() => {
    if (selectedId !== null || !home.current) return;
    const { position, target } = home.current;
    home.current = null;
    graph.current?.cameraPosition(position, target, 700);
  }, [selectedId]);

  // 점을 다 돌려보내고 배치 계산이 멈춘 뒤에 고정을 푼다. 계산이 도는 중에 풀면 자리 잡았던 그래프가 다시 출렁인다
  const onEngineStop = useCallback(() => {
    if (!wantUnpin.current || selected.current !== null) return;
    wantUnpin.current = false;
    pinnedAll.current = false;
    for (const node of shown.current.nodes) {
      if (!userPinned.current.has(node.id)) [node.fx, node.fy, node.fz] = [undefined, undefined, undefined];
    }
  }, []);
  const onBackgroundClick = useCallback(() => onSelect(null), [onSelect]);

  return (
    <div ref={container} className="graph">
      <ForceGraph3D
        ref={graph}
        width={size.width}
        height={size.height}
        graphData={data}
        backgroundColor="#0d0e10"
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
        onEngineStop={onEngineStop}
        warmupTicks={warmup}
        cooldownTime={8000}
        d3VelocityDecay={0.56}
      />
    </div>
  );
});
