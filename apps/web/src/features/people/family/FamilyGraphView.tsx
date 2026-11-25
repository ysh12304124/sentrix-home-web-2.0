/**
 * 关系图：手写 SVG + d3-force 只算坐标（不引入整套图可视化库）。2026-11-12 重构。
 *
 * - **布局**（`forceLayout.ts`）：总览是分层力导向——长辈在上、夫妻挨着、朋友/自定义关系
 *   自由分布；「以 TA 为中心」是环形聚焦，直接关系第一圈、隔一层的第二圈，其余变淡。
 * - **谁进图**：认出来的人（已确认、没标为路人）＋ 任何出现在已确认关系里的人。还没有
 *   关系的人也在图上（散在外围），可以直接从他身上拖出关系——不再是一张只有连线的图。
 * - **称谓标签**：一条关系两端各贴一个称谓，贴在谁旁边就是谁的身份（「父亲」贴爸爸、
 *   「儿子」贴孩子），不用再猜方向；对称的称谓（朋友 / 同事）只贴一个在中间。缩小到一定
 *   程度只留高亮的标签（细节层级），避免满屏文字。
 * - **交互**：滚轮缩放（光标为不动点）、拖空白平移、拖动人物摆位置（固定住，其余人围着
 *   他重排）、从人物的 ⊕ 拖到另一个人建关系、双击以 TA 为中心。
 *
 * 缩放只改 `<g>` 的 transform 属性，不 setState；平移 / 拖拽走 window 上的原生指针事件，
 * 指针移出 SVG 边界时事件不断流。
 */

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";

import {
  deleteFamilyRelationship,
  faceInstanceCropUrl,
  putFamilyRelationship,
  retryCorrectionJob,
  retryMemoryCorrections,
  type FamilyPerson,
  type FamilyRelationship,
  type MemorySyncResult,
} from "../../../shared/api/client";
import { EmptyState } from "../../../shared/ui/EmptyState";
import { describeReviewError } from "../reviewErrors";
import { edgeKind, forceLayout, radialLayout, relationDistances } from "./forceLayout";
import { useCorrectionJobPolling } from "./useCorrectionJob";
import {
  confirmedRelationships,
  fitTransform,
  isConfirmedRelationship,
  layoutBounds,
  nearestNodeId,
  NODE_RADIUS,
  screenToWorld,
  zoomAt,
  type LayoutPoint,
  type RelationEdge,
} from "./graphLayout";
import {
  canonicalKinship,
  isPendingPerson,
  membershipLabel,
  membershipOf,
  personDisplayName,
  relationLabelSuggestions,
} from "./membership";
import { RelationLabelFields, useRelationLabels } from "./RelationEditor";
import { relationshipWriteKey, usePeopleWrites } from "./usePeopleWrites";

/** 命中另一个节点的半径：比节点本身松一点，拖拽时不必精确压中圆心。 */
const DROP_RADIUS = NODE_RADIUS * 1.8;
const DEFAULT_SIZE = { width: 960, height: 560 };
/** 缩放低于这个比例时只画高亮的称谓标签。 */
const LABEL_MIN_SCALE = 0.75;
/** 标签太长时截断，悬停看全文。 */
const LABEL_MAX_CHARS = 6;
/** 移动超过这么多像素才算拖动，否则是点击。 */
const DRAG_THRESHOLD = 4;

interface Props {
  readonly people: FamilyPerson[];
  readonly relationships: FamilyRelationship[];
  readonly selectedId: string | null;
  readonly onSelect: (personId: string) => void;
  readonly onRelate: (subjectId: string, objectId: string) => void;
  /** 写完关系后重拉图谱（不闪 loading）。 */
  readonly onChanged: () => Promise<void>;
  /** 关系写回执先落本地（一对人之间的行整体替换），重拉在后台对账。不给就等重拉。 */
  readonly onRelationsChanged?: (scopeId: string, a: string, b: string, rows: readonly FamilyRelationship[]) => void;
}

/** 点中的边。方向保留去重后的方向；编辑时用它作为 PUT 的 subject → object。 */
interface EdgeSelection {
  readonly subject: string;
  readonly object: string;
  readonly predicate: string;
  readonly inverse_predicate: string;
  readonly scopeId: string;
}

interface DragState {
  mode: "pan" | "move" | "relate";
  pointerId: number;
  fromId: string | null;
  startX: number;
  startY: number;
  lastX: number;
  lastY: number;
  moved: boolean;
}

type Mode = "overview" | "focus";

function shortLabel(label: string): string {
  return label.length > LABEL_MAX_CHARS ? `${label.slice(0, LABEL_MAX_CHARS)}…` : label;
}

/** 布局的结构签名：人（id + 出现次数，`stableOrder` 用它排序）与边（两端 + 两个称谓）。 */
function layoutSignature(people: readonly FamilyPerson[], edges: readonly RelationEdge[]): string {
  return [
    ...people.map((person) => `p:${person.id}:${person.appearance_count}`),
    ...edges.map((edge) => `e:${edge.subject_entity_id}:${edge.object_entity_id}:${edge.predicate}:${edge.inverse_predicate}`),
  ].join("\n");
}

/** 线段上 `t` 处的点（0 = from，1 = to）。 */
function along(from: LayoutPoint, to: LayoutPoint, t: number): LayoutPoint {
  return { x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t };
}

export function FamilyGraphView({
  people,
  relationships,
  selectedId,
  onSelect,
  onRelate,
  onChanged,
  onRelationsChanged,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const groupRef = useRef<SVGGElement | null>(null);
  const clipId = useId();

  const [size, setSize] = useState(DEFAULT_SIZE);
  const [query, setQuery] = useState("");
  const [hovered, setHovered] = useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<EdgeSelection | null>(null);
  const [mode, setMode] = useState<Mode>("overview");
  const [pins, setPins] = useState<ReadonlyMap<string, LayoutPoint>>(new Map());
  const [dragPos, setDragPos] = useState<{ id: string; x: number; y: number } | null>(null);
  const [relatePreview, setRelatePreview] = useState<{
    from: string;
    x: number;
    y: number;
    target: string | null;
  } | null>(null);

  // 变换与布局放 ref：原生事件处理里要读最新值，而它们不该触发重渲染。
  const transformRef = useRef({ k: 1, x: 0, y: 0 });
  const dragRef = useRef<DragState | null>(null);
  const dragPosRef = useRef(dragPos);
  const onSelectRef = useRef(onSelect);
  const onRelateRef = useRef(onRelate);
  const modeRef = useRef(mode);
  // 上一次的总览坐标：关系变化后从这里出发重排，图不会整体跳动。
  const previousRef = useRef<ReadonlyMap<string, LayoutPoint> | undefined>(undefined);

  // 只画用户确认过的边；模型推断与角色补边不进图谱。
  const confirmedRows = useMemo(() => relationships.filter(isConfirmedRelationship), [relationships]);
  const edges = useMemo(() => confirmedRelationships(relationships), [relationships]);
  const graphPeople = useMemo(() => {
    const related = new Set<string>();
    for (const edge of edges) {
      related.add(edge.subject_entity_id);
      related.add(edge.object_entity_id);
    }
    return people.filter((person) => related.has(person.id) || (!isPendingPerson(person) && !person.hidden));
  }, [edges, people]);
  const graphIds = useMemo(() => new Set(graphPeople.map((person) => person.id)), [graphPeople]);
  const focusId = mode === "focus" && selectedId !== null && graphIds.has(selectedId) ? selectedId : null;

  // 布局只取决于「谁在图上、出现几次、哪几条边」。改名、改别名、后台对账重拉都会换一份新的
  // `people` 数组，但这些都不改变结构——不能每次都把 320 步的力导向模拟重跑一遍。
  const layoutKey = useMemo(() => layoutSignature(graphPeople, edges), [edges, graphPeople]);
  const layoutInputs = useRef({ key: layoutKey, people: graphPeople, edges });
  if (layoutInputs.current.key !== layoutKey) layoutInputs.current = { key: layoutKey, people: graphPeople, edges };
  const layoutPeople = layoutInputs.current.people;
  const layoutEdges = layoutInputs.current.edges;
  const layout = useMemo(
    () =>
      focusId !== null
        ? radialLayout(focusId, layoutPeople, layoutEdges)
        : forceLayout(layoutPeople, layoutEdges, { pins, previous: previousRef.current }),
    [focusId, layoutEdges, layoutPeople, pins],
  );
  const distances = useMemo(
    () => (focusId === null ? null : relationDistances(focusId, edges)),
    [edges, focusId],
  );
  const layoutRef = useRef(layout);
  const peopleRef = useRef(people);
  const sizeRef = useRef(size);
  const positionOf = (id: string): LayoutPoint | undefined =>
    dragPos !== null && dragPos.id === id ? dragPos : layout.get(id);

  // 同步「处理函数要读的最新值」。在 effect 里写而不是渲染期写。
  useEffect(() => {
    layoutRef.current = layout;
    peopleRef.current = people;
    sizeRef.current = size;
    dragPosRef.current = dragPos;
    onSelectRef.current = onSelect;
    onRelateRef.current = onRelate;
    modeRef.current = mode;
    if (focusId === null) previousRef.current = layout;
  });

  const hits = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (needle === "") return null;
    return new Set(
      graphPeople
        .filter(
          (person) =>
            personDisplayName(person).toLowerCase().includes(needle) ||
            person.aliases.some((alias) => alias.toLowerCase().includes(needle)),
        )
        .map((person) => person.id),
    );
  }, [graphPeople, query]);

  const applyTransform = useCallback(() => {
    const { k, x, y } = transformRef.current;
    groupRef.current?.setAttribute("transform", `translate(${x} ${y}) scale(${k})`);
    // 细节层级：缩得很小时只留高亮的称谓标签。只改属性，不触发重渲染。
    svgRef.current?.setAttribute("data-lod", k < LABEL_MIN_SCALE ? "far" : "near");
  }, []);

  /** 自适应：包围盒 → 初始 scale/translate，让全图刚好装下。公式在 `fitTransform`。 */
  const fit = useCallback(() => {
    const { width, height } = sizeRef.current;
    const next = fitTransform(layoutBounds(layoutRef.current), width, height);
    if (next === null) return;
    transformRef.current = next;
    applyTransform();
  }, [applyTransform]);

  // 量容器。jsdom 里没有 ResizeObserver，兜底用默认尺寸。
  useEffect(() => {
    const element = containerRef.current;
    if (element === null || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect === undefined) return;
      setSize({ width: Math.max(rect.width, 320), height: Math.max(rect.height, 320) });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // 换了一批人、切换总览 / 聚焦、窗口尺寸变化时重新自适应；拖动摆位置不触发（否则一松手
  // 画面就跳）。
  const fitKey = `${focusId ?? "overview"}|${[...graphIds].sort().join(",")}|${size.width}x${size.height}`;
  useEffect(() => {
    fit();
  }, [fit, fitKey]);

  // 滚轮缩放。原生监听 + passive:false：React 的 onWheel 是 passive 的，preventDefault 会被忽略。
  useEffect(() => {
    const svg = svgRef.current;
    if (svg === null) return;
    const element: SVGSVGElement = svg;
    function handleWheel(event: WheelEvent) {
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      transformRef.current = zoomAt(
        transformRef.current,
        event.clientX - rect.left,
        event.clientY - rect.top,
        Math.exp(-event.deltaY * 0.0015),
      );
      applyTransform();
    }
    svg.addEventListener("wheel", handleWheel, { passive: false });
    return () => svg.removeEventListener("wheel", handleWheel);
  }, [applyTransform]);

  const clientToWorld = useCallback((clientX: number, clientY: number) => {
    const svg = svgRef.current;
    if (svg === null) return { x: 0, y: 0 };
    const rect = svg.getBoundingClientRect();
    return screenToWorld(transformRef.current, rect.left, rect.top, clientX, clientY);
  }, []);

  // 平移 / 摆位置 / 拖拽建关系。挂 window 而不是挂 SVG：指针移出边界后事件还能继续。
  useEffect(() => {
    function relatedTarget(fromId: string, world: LayoutPoint): string | null {
      const scope = peopleRef.current.find((person) => person.id === fromId)?.scope_id;
      if (!scope) return null;
      const candidate = nearestNodeId(layoutRef.current, world, fromId, DROP_RADIUS);
      if (candidate === null) return null;
      return peopleRef.current.find((person) => person.id === candidate)?.scope_id === scope ? candidate : null;
    }
    function handleMove(event: PointerEvent) {
      const drag = dragRef.current;
      if (drag === null || drag.pointerId !== event.pointerId) return;
      if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > DRAG_THRESHOLD) drag.moved = true;
      if (drag.mode === "pan") {
        const current = transformRef.current;
        transformRef.current = {
          k: current.k,
          x: current.x + event.clientX - drag.lastX,
          y: current.y + event.clientY - drag.lastY,
        };
        drag.lastX = event.clientX;
        drag.lastY = event.clientY;
        applyTransform();
        return;
      }
      if (!drag.moved || drag.fromId === null) return;
      const world = clientToWorld(event.clientX, event.clientY);
      if (drag.mode === "move") {
        setDragPos({ id: drag.fromId, x: world.x, y: world.y });
        return;
      }
      setRelatePreview({
        from: drag.fromId,
        x: world.x,
        y: world.y,
        target: relatedTarget(drag.fromId, world),
      });
    }
    function handleUp(event: PointerEvent) {
      const drag = dragRef.current;
      if (drag === null || drag.pointerId !== event.pointerId) return;
      dragRef.current = null;
      if (drag.mode === "pan" || drag.fromId === null) return;
      const fromId = drag.fromId;
      if (drag.mode === "move") {
        const dropped = dragPosRef.current;
        setDragPos(null);
        if (!drag.moved) {
          onSelectRef.current(fromId);
          return;
        }
        // 聚焦视图是按距离排的环，摆位置没有意义；只在总览里固定。
        if (dropped !== null && modeRef.current === "overview") {
          setPins((current) => new Map(current).set(fromId, { x: dropped.x, y: dropped.y }));
        }
        return;
      }
      setRelatePreview(null);
      if (!drag.moved && Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) <= DRAG_THRESHOLD) return;
      const target = relatedTarget(fromId, clientToWorld(event.clientX, event.clientY));
      if (target !== null) onRelateRef.current(fromId, target);
    }
    function handleCancel(event: PointerEvent) {
      if (dragRef.current?.pointerId !== event.pointerId) return;
      dragRef.current = null;
      setRelatePreview(null);
      setDragPos(null);
    }
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
    window.addEventListener("pointercancel", handleCancel);
    return () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
      window.removeEventListener("pointercancel", handleCancel);
    };
  }, [applyTransform, clientToWorld]);

  function handlePointerDown(event: ReactPointerEvent<SVGSVGElement>) {
    if (event.button !== 0 || dragRef.current !== null) return;
    const target = event.target as Element;
    const handle = target.closest("[data-relate-handle]");
    const node = target.closest("[data-node-id]");
    const base = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, lastX: event.clientX, lastY: event.clientY, moved: false };
    if (handle !== null) {
      event.stopPropagation();
      dragRef.current = { ...base, mode: "relate", fromId: handle.getAttribute("data-relate-handle") };
    } else if (node !== null) {
      event.stopPropagation();
      dragRef.current = { ...base, mode: "move", fromId: node.getAttribute("data-node-id") };
    } else {
      dragRef.current = { ...base, mode: "pan", fromId: null };
    }
  }

  function handleDoubleClick(event: ReactMouseEvent<SVGSVGElement>) {
    const node = (event.target as Element).closest("[data-node-id]");
    const id = node?.getAttribute("data-node-id");
    if (id === null || id === undefined) return;
    onSelect(id);
    setMode("focus");
  }

  const nameOf = (id: string): string => {
    const person = people.find((item) => item.id === id);
    return person === undefined ? id : personDisplayName(person);
  };
  const selectedInGraph = selectedId !== null && graphIds.has(selectedId);

  if (graphPeople.length === 0) {
    return (
      <EmptyState
        symbol="◎"
        title="还没有可以成图的人"
        description="先在「人物」里认出几个人，再从一个人身上拖出关系连到另一个人。"
      />
    );
  }

  const isActiveEdge = (edge: RelationEdge) =>
    (selectedEdge !== null &&
      selectedEdge.subject === edge.subject_entity_id &&
      selectedEdge.object === edge.object_entity_id) ||
    selectedId === edge.subject_entity_id ||
    selectedId === edge.object_entity_id ||
    hovered === edge.subject_entity_id ||
    hovered === edge.object_entity_id;

  return (
    <div className="graph-shell">
      <div className="graph-toolbar">
        <input
          type="search"
          className="roster-search"
          aria-label="在图中搜索人物"
          placeholder="高亮某个人…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="roster-views" role="tablist" aria-label="布局">
          <button
            type="button"
            role="tab"
            aria-selected={focusId === null}
            className={focusId === null ? "roster-view active" : "roster-view"}
            onClick={() => setMode("overview")}
          >
            总览
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={focusId !== null}
            className={focusId !== null ? "roster-view active" : "roster-view"}
            disabled={!selectedInGraph}
            title={selectedInGraph ? undefined : "先在图上点选一个人"}
            onClick={() => setMode("focus")}
          >
            {selectedInGraph ? `以 ${nameOf(selectedId as string)} 为中心` : "以选中的人为中心"}
          </button>
        </div>
        <button type="button" className="button ghost small" onClick={fit}>
          适配全图
        </button>
        {pins.size === 0 || focusId !== null ? null : (
          <button type="button" className="button ghost small" onClick={() => setPins(new Map())}>
            重新排布
          </button>
        )}
        <span className="graph-hint">滚轮缩放 · 拖空白平移 · 拖动人物摆位置 · 从 ⊕ 拖到另一个人建关系 · 双击以 TA 为中心</span>
      </div>

      {confirmedRows.length === 0 ? (
        <p className="graph-banner">还没有关系：把鼠标移到一个人身上，从 ⊕ 拖到另一个人，建立第一条关系。</p>
      ) : null}

      <div className="graph-canvas" ref={containerRef}>
        <svg
          ref={svgRef}
          className="graph-svg"
          width="100%"
          height="100%"
          onPointerDown={handlePointerDown}
          onDoubleClick={handleDoubleClick}
          role="application"
          aria-label="家庭关系图"
        >
          <defs>
            <clipPath id={clipId}>
              <circle r={NODE_RADIUS - 3} />
            </clipPath>
          </defs>
          <g ref={groupRef}>
            {edges.map((edge) => {
              const from = positionOf(edge.subject_entity_id);
              const to = positionOf(edge.object_entity_id);
              if (from === undefined || to === undefined) return null;
              const active = isActiveEdge(edge);
              const kind = edgeKind(edge);
              const faded =
                distances !== null &&
                ((distances.get(edge.subject_entity_id) ?? 99) > 1 || (distances.get(edge.object_entity_id) ?? 99) > 1) &&
                !active;
              const symmetric = canonicalKinship(edge.predicate) === canonicalKinship(edge.inverse_predicate);
              const labels = symmetric
                ? [{ text: edge.predicate, at: along(from, to, 0.5) }]
                : [
                    // 贴在谁旁边就是谁的身份：subject 是 object 的 predicate。
                    { text: edge.predicate, at: along(from, to, 0.27) },
                    { text: edge.inverse_predicate, at: along(from, to, 0.73) },
                  ];
              const labelClass = active ? "graph-edge-label active" : "graph-edge-label";
              return (
                <g
                  key={`${edge.subject_entity_id}-${edge.object_entity_id}`}
                  data-edge-id={`${edge.subject_entity_id}->${edge.object_entity_id}`}
                  className={faded ? "graph-edge-group dim" : "graph-edge-group"}
                  style={{ cursor: "pointer" }}
                  onClick={(event) => {
                    event.stopPropagation();
                    const row = confirmedRows.find((item) =>
                      item.subject_entity_id === edge.subject_entity_id &&
                      item.object_entity_id === edge.object_entity_id &&
                      item.predicate === edge.predicate &&
                      item.inverse_predicate === edge.inverse_predicate);
                    if (row === undefined) return;
                    setSelectedEdge({
                      scopeId: row.scope_id,
                      subject: edge.subject_entity_id,
                      object: edge.object_entity_id,
                      predicate: edge.predicate,
                      inverse_predicate: edge.inverse_predicate,
                    });
                  }}
                >
                  {/* 加粗的透明线：细线很难点中。 */}
                  <line className="graph-edge-hit" x1={from.x} y1={from.y} x2={to.x} y2={to.y} />
                  <line
                    className={`graph-edge ${kind}${active ? " active" : ""}`}
                    x1={from.x}
                    y1={from.y}
                    x2={to.x}
                    y2={to.y}
                  />
                  {labels.map((label) => (
                    <text key={`${label.text}-${label.at.x}`} className={labelClass} x={label.at.x} y={label.at.y + 4}>
                      <title>{`${nameOf(edge.subject_entity_id)} 是 ${nameOf(edge.object_entity_id)} 的${edge.predicate}`}</title>
                      {shortLabel(label.text)}
                    </text>
                  ))}
                </g>
              );
            })}

            {relatePreview === null ? null : <PreviewLine preview={relatePreview} layout={layout} />}

            {graphPeople.map((person) => {
              const point = positionOf(person.id);
              if (point === undefined) return null;
              const membership = membershipOf(person);
              const distance = distances?.get(person.id);
              const dim =
                (hits !== null && !hits.has(person.id)) || (distances !== null && (distance === undefined || distance > 1));
              const classes = ["graph-node", membership];
              if (person.id === selectedId) classes.push("selected");
              if (hovered === person.id) classes.push("hovered");
              if (relatePreview?.target === person.id) classes.push("drop-target");
              if (pins.has(person.id) && focusId === null) classes.push("pinned");
              if (dim) classes.push("dim");
              const faceId = person.avatar_face_instance_id;
              return (
                <g
                  key={person.id}
                  className={classes.join(" ")}
                  data-node-id={person.id}
                  transform={`translate(${point.x} ${point.y})`}
                  onPointerEnter={() => setHovered(person.id)}
                  onPointerLeave={() => setHovered((current) => (current === person.id ? null : current))}
                >
                  <circle className="graph-node-circle" r={NODE_RADIUS} />
                  {faceId === null ? (
                    <text className="graph-node-initial" y={5} textAnchor="middle">
                      {personDisplayName(person).slice(0, 1)}
                    </text>
                  ) : (
                    <image
                      href={faceInstanceCropUrl(faceId)}
                      x={-(NODE_RADIUS - 3)}
                      y={-(NODE_RADIUS - 3)}
                      width={(NODE_RADIUS - 3) * 2}
                      height={(NODE_RADIUS - 3) * 2}
                      clipPath={`url(#${clipId})`}
                      preserveAspectRatio="xMidYMid slice"
                    />
                  )}
                  <text className="graph-node-name" y={NODE_RADIUS + 16} textAnchor="middle">
                    {personDisplayName(person)}
                  </text>
                  <text className="graph-node-tag" y={NODE_RADIUS + 30} textAnchor="middle">
                    {membershipLabel(membership)}
                  </text>
                  <g
                    className="graph-relate-handle"
                    data-relate-handle={person.id}
                    transform={`translate(${NODE_RADIUS * 0.78} ${-NODE_RADIUS * 0.78})`}
                    aria-label={`从 ${personDisplayName(person)} 拖出关系`}
                  >
                    <circle r={9} />
                    <text y={4} textAnchor="middle">
                      +
                    </text>
                  </g>
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      <div className="graph-legend">
        <span className="legend-item family">家人</span>
        <span className="legend-item friend">朋友</span>
        <span className="legend-item unknown">待归类（虚线圈）</span>
        <span className="legend-line parent">亲子</span>
        <span className="legend-line partner">夫妻</span>
        <span className="legend-line other">其他关系</span>
        <span className="legend-count">
          {graphPeople.length} 人 · {edges.length} 条已确认关系
        </span>
      </div>

      {selectedEdge === null ? null : (
        <EdgeEditor
          key={`${selectedEdge.subject}-${selectedEdge.object}-${selectedEdge.predicate}`}
          edge={selectedEdge}
          subjectName={nameOf(selectedEdge.subject)}
          objectName={nameOf(selectedEdge.object)}
          suggestions={relationLabelSuggestions(edges.flatMap((item) => [item.predicate, item.inverse_predicate]))}
          onChanged={onChanged}
          onRelationsChanged={onRelationsChanged}
          onClose={() => setSelectedEdge(null)}
        />
      )}
    </div>
  );
}

/**
 * 选中一条边后的编辑条：改谓词走 `PUT /family-graph/relationships`（后端会先 supersede
 * 这一对现有的关系，再落正反两行），删除走 `DELETE`（只带两端 id，谓词不在体里）。
 *
 * **方向原样保留**：「subject 是 object 的 predicate」。称谓由用户自己写（与详情建关系
 * 共用 `RelationLabelFields`），后端对用户写的称谓只做形状校验。
 */
function EdgeEditor({
  edge,
  subjectName,
  objectName,
  suggestions,
  onChanged,
  onRelationsChanged,
  onClose,
}: {
  edge: EdgeSelection;
  subjectName: string;
  objectName: string;
  suggestions: readonly string[];
  onChanged: () => Promise<void>;
  onRelationsChanged?: (scopeId: string, a: string, b: string, rows: readonly FamilyRelationship[]) => void;
  onClose: () => void;
}) {
  // 「subject 是 object 的 predicate」：subject 是对方、object 是「我」，与详情里建关系同一种说法。
  const labels = useRelationLabels({ role: edge.predicate, reverse: edge.inverse_predicate });
  const scopeId = edge.scopeId;
  const { pending, write } = usePeopleWrites();
  const key = relationshipWriteKey(scopeId, edge.subject, edge.object);
  const busy = pending.has(key);
  const [error, setError] = useState<string | null>(null);
  const [syncResult, setSyncResult] = useState<MemorySyncResult | null>(null);
  const readOnly = busy || syncResult !== null;

  useCorrectionJobPolling(syncResult, setSyncResult);

  /** 写入；`rows` 是这对人之间写完后生效的行（删除时为空），先落本地再后台对账。 */
  function submit(action: () => Promise<{ result: MemorySyncResult; rows: readonly FamilyRelationship[] }>) {
    if (scopeId === "" || readOnly) return;
    void write(key, async () => {
      setError(null);
      try {
        const { result, rows } = await action();
        setSyncResult(result);
        if (onRelationsChanged === undefined) await onChanged();
        else onRelationsChanged(scopeId, edge.subject, edge.object, rows);
      } catch (cause) {
        setError(describeReviewError(cause));
      }
    });
  }

  function retrySync() {
    void write(key, async () => {
      setError(null);
      try {
        const jobId = syncResult?.sync_job_id;
        if (jobId) {
          const job = await retryCorrectionJob(jobId);
          setSyncResult({ sync_job_id: job.id, sync_status: job.status, sync_error: job.error });
          return;
        }
        const { error: syncError } = await retryMemoryCorrections(scopeId);
        setSyncResult({ sync_status: syncError ? "pending" : "synced", sync_error: syncError });
        if (!syncError) await onChanged();
      } catch (cause) {
        setError(describeReviewError(cause));
      }
    });
  }

  return (
    <section className="graph-toolbar" role="region" aria-label="编辑关系">
      <strong>
        {subjectName} 是 {objectName} 的{edge.predicate}
      </strong>
      <RelationLabelFields
        labels={labels}
        otherName={subjectName}
        selfName={objectName}
        suggestions={suggestions}
        disabled={readOnly}
      />
      <button
        type="button"
        className="button primary small"
        disabled={readOnly || !labels.ready || scopeId === ""}
        onClick={() => {
          if (!labels.ready) return;
          submit(async () => {
            const result = await putFamilyRelationship({
              scopeId, subjectEntityId: edge.subject, objectEntityId: edge.object,
              predicate: labels.cleanRole, inversePredicate: labels.cleanReverse,
            });
            return { result, rows: result.relationships };
          });
        }}
      >
        保存关系
      </button>
      <button
        type="button"
        className="button ghost small"
        disabled={readOnly || scopeId === ""}
        onClick={() => submit(async () => ({
          result: await deleteFamilyRelationship({
            scopeId, subjectEntityId: edge.subject, objectEntityId: edge.object,
          }),
          rows: [],
        }))}
      >
        删除关系
      </button>
      <button type="button" className="button ghost small" disabled={busy} onClick={onClose}>
        {syncResult === null ? "取消" : "完成"}
      </button>
      {syncResult === null ? null : (
        <div className={`memory-sync-feedback is-${syncResult.sync_status}`} role="status" aria-live="polite">
          <span>{syncResult.sync_status === "synced"
            ? "关系已保存，图谱与检索索引已同步。"
            : syncResult.sync_error || syncResult.sync_status === "failed"
              ? `关系已保存，但关联数据待同步${syncResult.sync_error ? `：${syncResult.sync_error}` : "。"}`
              : "关系已保存，正在后台同步图谱与检索索引。"}</span>
          {syncResult.sync_status !== "synced" && (syncResult.sync_error || syncResult.sync_status === "failed") ? (
            <button type="button" className="button ghost small" disabled={busy} onClick={retrySync}>
              {busy ? "正在重试…" : "重试同步"}
            </button>
          ) : null}
        </div>
      )}
      {error === null ? null : (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

/** 拖拽建关系时那条跟随指针的虚线：没对准人时跟着指针，对准了就吸附到那个人。 */
function PreviewLine({
  preview,
  layout,
}: {
  preview: { from: string; x: number; y: number; target: string | null };
  layout: ReadonlyMap<string, { x: number; y: number }>;
}) {
  const origin = layout.get(preview.from);
  if (origin === undefined) return null;
  const end = preview.target === null ? preview : layout.get(preview.target);
  if (end === undefined) return null;
  return (
    <line
      className="graph-relate-preview"
      x1={origin.x}
      y1={origin.y}
      x2={end.x}
      y2={end.y}
    />
  );
}
