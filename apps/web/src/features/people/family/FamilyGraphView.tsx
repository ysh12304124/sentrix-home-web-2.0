/**
 * 关系图谱：手写 SVG，不引图可视化依赖（计划书 §1 裁决①）。
 *
 * 三处与 1.0 的行为差异，都是计划书定下的：
 * 1. **待归类（unknown / membership 为 null）单独一行画在底部**，虚线圈——1.0 把它们
 *    排除在图外，实测数据下这会让整张图变空（计划书 §0）；
 * 2. **加载即自适应**：按包围盒算初始 scale/translate 让全图刚好装下，1.0 的「适配全图」
 *    只是复位到 scale=1（`app.js:1991`）；
 * 3. **缩放只改 `<g>` 的 transform 属性，不 setState**——1.0 每帧重建整页 innerHTML
 *    （`app.js:1299-1343`），这里连一次 React 重渲染都不做。
 *
 * 平移 / 拖拽建关系走 window 上的原生指针事件：指针移出 SVG 边界时事件不断流，
 * 这是「拖到另一个节点上松手」能可靠完成的前提。
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";

import {
  deleteFamilyRelationship,
  putFamilyRelationship,
  retryMemoryCorrections,
  type FamilyPerson,
  type FamilyRelationship,
  type MemorySyncResult,
} from "../../../shared/api/client";
import { EmptyState } from "../../../shared/ui/EmptyState";
import { describeReviewError } from "../reviewErrors";
import {
  confirmedRelationships,
  fitTransform,
  graphLayout,
  isConfirmedRelationship,
  layoutBounds,
  nearestNodeId,
  NODE_RADIUS,
  screenToWorld,
  zoomAt,
} from "./graphLayout";
import {
  membershipLabel,
  membershipOf,
  personDisplayName,
  relationChoices,
} from "./membership";

/** 命中另一个节点的半径：比节点本身松一点，拖拽时不必精确压中圆心。 */
const DROP_RADIUS = NODE_RADIUS * 1.8;
const DEFAULT_SIZE = { width: 960, height: 560 };

interface Props {
  readonly people: FamilyPerson[];
  readonly relationships: FamilyRelationship[];
  readonly selectedId: string | null;
  readonly onSelect: (personId: string) => void;
  readonly onRelate: (subjectId: string, objectId: string) => void;
  /** 写完关系后重拉图谱（不闪 loading）。 */
  readonly onChanged: () => Promise<void>;
}

/** 点中的边。方向保留去重后的方向；编辑时用它作为 PUT 的 subject → object。 */
interface EdgeSelection {
  readonly subject: string;
  readonly object: string;
  readonly predicate: string;
  readonly inverse_predicate: string;
}

interface DragState {
  mode: "pan" | "relate";
  fromId: string | null;
  lastX: number;
  lastY: number;
  moved: boolean;
}

export function FamilyGraphView({
  people,
  relationships,
  selectedId,
  onSelect,
  onRelate,
  onChanged,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const groupRef = useRef<SVGGElement | null>(null);

  const [size, setSize] = useState(DEFAULT_SIZE);
  const [query, setQuery] = useState("");
  const [hovered, setHovered] = useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<EdgeSelection | null>(null);
  const [relatePreview, setRelatePreview] = useState<{
    from: string;
    x: number;
    y: number;
    target: string | null;
  } | null>(null);

  // 变换与布局放 ref：原生事件处理里要读最新值，而它们不该触发重渲染。
  const transformRef = useRef({ k: 1, x: 0, y: 0 });
  const dragRef = useRef<DragState | null>(null);
  const relatePreviewRef = useRef(relatePreview);
  const onSelectRef = useRef(onSelect);
  const onRelateRef = useRef(onRelate);

  // 只画用户确认过的边；模型推断与角色补边不进图谱。
  const confirmedRows = useMemo(
    () => relationships.filter(isConfirmedRelationship),
    [relationships],
  );
  const edges = useMemo(() => confirmedRelationships(relationships), [relationships]);
  // 没有已确认关系的人也不进图——图谱是「已确认的家庭结构」，不是人物总表。
  const graphPeople = useMemo(() => {
    const ids = new Set<string>();
    for (const edge of edges) {
      ids.add(edge.subject_entity_id);
      ids.add(edge.object_entity_id);
    }
    return people.filter((person) => ids.has(person.id));
  }, [edges, people]);
  const layout = useMemo(
    () => graphLayout(graphPeople, confirmedRows, size.width, size.height),
    [confirmedRows, graphPeople, size],
  );
  const layoutRef = useRef(layout);
  const sizeRef = useRef(size);

  // 同步「处理函数要读的最新值」。**在 effect 里写而不是渲染期写**——渲染期改 ref
  // 会让 React Compiler 放弃优化，也是并发渲染下的隐患。声明位置在本组件所有
  // 读取它们的 effect 之前，所以同一次提交里它们先落地。
  useEffect(() => {
    layoutRef.current = layout;
    sizeRef.current = size;
    relatePreviewRef.current = relatePreview;
    onSelectRef.current = onSelect;
    onRelateRef.current = onRelate;
  });

  const hits = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (needle === "") return null;
    return new Set(
      graphPeople
        .filter((person) => personDisplayName(person).toLowerCase().includes(needle))
        .map((person) => person.id),
    );
  }, [graphPeople, query]);

  const applyTransform = useCallback(() => {
    const { k, x, y } = transformRef.current;
    groupRef.current?.setAttribute("transform", `translate(${x} ${y}) scale(${k})`);
  }, []);

  /** 加载即自适应：包围盒 → 初始 scale/translate，让全图刚好装下。公式在 `fitTransform`。 */
  const fit = useCallback(() => {
    const { width, height } = sizeRef.current;
    const next = fitTransform(layoutBounds(layoutRef.current), width, height);
    if (next === null) return;
    transformRef.current = next;
    applyTransform();
  }, [applyTransform]);

  // 量容器。jsdom 里没有 ResizeObserver，兜底用默认尺寸——图谱不在冒烟测试的路径上。
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

  // 布局变化（换相册、写关系后重拉）就重新自适应一次。
  useEffect(() => {
    fit();
  }, [fit, layout]);

  // 滚轮缩放。原生监听 + passive:false：React 的 onWheel 是 passive 的，preventDefault 会被忽略。
  useEffect(() => {
    const svg = svgRef.current;
    if (svg === null) return;
    // 收窄后另存一个非空常量：闭包里读外层 const 的收窄结果 TS 不认。
    const element: SVGSVGElement = svg;
    function handleWheel(event: WheelEvent) {
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      const pointerX = event.clientX - rect.left;
      const pointerY = event.clientY - rect.top;
      // 不动点缩放：光标下的世界坐标在缩放前后必须不变（公式在 `zoomAt`）。
      transformRef.current = zoomAt(
        transformRef.current,
        pointerX,
        pointerY,
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

  const nearestNode = useCallback(
    (world: { x: number; y: number }, excludeId: string) =>
      nearestNodeId(layoutRef.current, world, excludeId, DROP_RADIUS),
    [],
  );

  // 平移 / 拖拽建关系。挂 window 而不是挂 SVG：指针移出边界后事件还能继续。
  useEffect(() => {
    function handleMove(event: PointerEvent) {
      const drag = dragRef.current;
      if (drag === null) return;
      if (drag.mode === "pan") {
        const dx = event.clientX - drag.lastX;
        const dy = event.clientY - drag.lastY;
        drag.lastX = event.clientX;
        drag.lastY = event.clientY;
        if (dx !== 0 || dy !== 0) drag.moved = true;
        const current = transformRef.current;
        transformRef.current = { k: current.k, x: current.x + dx, y: current.y + dy };
        applyTransform();
        return;
      }
      drag.moved = true;
      const world = clientToWorld(event.clientX, event.clientY);
      setRelatePreview({
        from: drag.fromId ?? "",
        x: world.x,
        y: world.y,
        target: drag.fromId === null ? null : nearestNode(world, drag.fromId),
      });
    }
    function handleUp() {
      const drag = dragRef.current;
      dragRef.current = null;
      if (drag === null) return;
      if (drag.mode === "pan") return;
      const preview = relatePreviewRef.current;
      setRelatePreview(null);
      if (drag.fromId === null) return;
      // 没拖动 = 单击 → 选中；拖动到另一个人 → 建关系。
      if (!drag.moved) {
        onSelectRef.current(drag.fromId);
        return;
      }
      if (preview !== null && preview.target !== null && preview.target !== drag.fromId) {
        onRelateRef.current(drag.fromId, preview.target);
      }
    }
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
    return () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
    };
  }, [applyTransform, clientToWorld, nearestNode]);

  function handlePointerDown(event: ReactPointerEvent<SVGSVGElement>) {
    const nodeElement = (event.target as Element).closest("[data-node-id]");
    if (nodeElement !== null) {
      event.stopPropagation();
      dragRef.current = {
        mode: "relate",
        fromId: nodeElement.getAttribute("data-node-id"),
        lastX: event.clientX,
        lastY: event.clientY,
        moved: false,
      };
      return;
    }
    dragRef.current = {
      mode: "pan",
      fromId: null,
      lastX: event.clientX,
      lastY: event.clientY,
      moved: false,
    };
  }

  // 编辑要写回的 scope 以**那条关系行自己的 scope_id** 为准：合并过相册时整张图跨多个
  // scope，拿入参 scope 会写错册。
  const selectedRow =
    selectedEdge === null
      ? undefined
      : relationships.find(
          (row) =>
            row.subject_entity_id === selectedEdge.subject &&
            row.object_entity_id === selectedEdge.object &&
            row.predicate === selectedEdge.predicate,
        );
  const nameOf = (id: string): string => {
    const person = people.find((item) => item.id === id);
    return person === undefined ? id : personDisplayName(person);
  };

  if (people.length === 0) {
    return (
      <EmptyState
        symbol="◎"
        title="还没有可以成图的人"
        description="确认人物并建立关系后，家庭关系会画在这里。"
      />
    );
  }

  if (confirmedRows.length === 0) {
    return <p className="empty graph-empty">当前无已确认关系</p>;
  }

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
        <button type="button" className="button ghost small" onClick={fit}>
          适配全图
        </button>
        <span className="graph-hint">滚轮缩放 · 拖空白平移 · 从一个人拖到另一个人建关系</span>
      </div>

      <div className="graph-canvas" ref={containerRef}>
        <svg
          ref={svgRef}
          className="graph-svg"
          width="100%"
          height="100%"
          onPointerDown={handlePointerDown}
          role="application"
          aria-label="家庭关系图"
        >
          <g ref={groupRef}>
            {edges.map((edge) => {
              const from = layout.get(edge.subject_entity_id);
              const to = layout.get(edge.object_entity_id);
              if (from === undefined || to === undefined) return null;
              const edgeSelected =
                selectedEdge !== null &&
                selectedEdge.subject === edge.subject_entity_id &&
                selectedEdge.object === edge.object_entity_id;
              const active =
                edgeSelected ||
                selectedId === edge.subject_entity_id ||
                selectedId === edge.object_entity_id ||
                hovered === edge.subject_entity_id ||
                hovered === edge.object_entity_id;
              return (
                <g
                  key={`${edge.subject_entity_id}-${edge.object_entity_id}-${edge.predicate}`}
                  data-edge-id={`${edge.subject_entity_id}->${edge.object_entity_id}`}
                  style={{ cursor: "pointer" }}
                  onClick={(event) => {
                    event.stopPropagation();
                    setSelectedEdge({
                      subject: edge.subject_entity_id,
                      object: edge.object_entity_id,
                      predicate: edge.predicate,
                      inverse_predicate: edge.inverse_predicate,
                    });
                  }}
                >
                  <line
                    className={active ? "graph-edge active" : "graph-edge"}
                    x1={from.x}
                    y1={from.y}
                    x2={to.x}
                    y2={to.y}
                  />
                  {active ? (
                    <text className="graph-edge-label" x={(from.x + to.x) / 2} y={(from.y + to.y) / 2 - 6}>
                      {edge.predicate}
                    </text>
                  ) : null}
                </g>
              );
            })}

            {relatePreview === null || relatePreview.target === null ? null : (
              <PreviewLine preview={relatePreview} layout={layout} />
            )}

            {graphPeople.map((person) => {
              const point = layout.get(person.id);
              if (point === undefined) return null;
              const membership = membershipOf(person);
              const dim = hits !== null && !hits.has(person.id);
              const classes = ["graph-node", membership];
              if (person.id === selectedId) classes.push("selected");
              if (hovered === person.id) classes.push("hovered");
              if (dim) classes.push("dim");
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
                  <text className="graph-node-initial" y={5} textAnchor="middle">
                    {personDisplayName(person).slice(0, 1)}
                  </text>
                  <text className="graph-node-name" y={NODE_RADIUS + 16} textAnchor="middle">
                    {personDisplayName(person)}
                  </text>
                  <text className="graph-node-tag" y={NODE_RADIUS + 30} textAnchor="middle">
                    {membershipLabel(membership)}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      <div className="graph-legend">
        <span className="legend-item family">家人</span>
        <span className="legend-item friend">朋友</span>
        <span className="legend-item unknown">待归类（虚线圈，单独一行）</span>
        <span className="legend-count">
          {graphPeople.length} 人 · {edges.length} 条已确认关系
        </span>
      </div>

      {selectedEdge === null ? null : (
        <EdgeEditor
          key={`${selectedEdge.subject}-${selectedEdge.object}-${selectedEdge.predicate}`}
          edge={selectedEdge}
          scopeId={selectedRow?.scope_id ?? ""}
          subjectName={nameOf(selectedEdge.subject)}
          objectName={nameOf(selectedEdge.object)}
          onChanged={onChanged}
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
 * **方向原样保留**：编辑条里的 subject → object 就是用户在图上看到的方向；选择表里的
 * 每一项都同时带谓词与反向谓词，后端 `validate_relationship_pair` 只接受成对的标签。
 * 当前谓词不在词表里时（模型可能写出别的关系）仍能删除，但保存需要重新选一项。
 */
function EdgeEditor({
  edge,
  scopeId,
  subjectName,
  objectName,
  onChanged,
  onClose,
}: {
  edge: EdgeSelection;
  scopeId: string;
  subjectName: string;
  objectName: string;
  onChanged: () => Promise<void>;
  onClose: () => void;
}) {
  const choices = relationChoices();
  const currentIndex = choices.findIndex(
    (choice) =>
      choice.predicate === edge.predicate &&
      choice.inversePredicate === edge.inverse_predicate,
  );
  const [choiceIndex, setChoiceIndex] = useState(currentIndex);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncResult, setSyncResult] = useState<MemorySyncResult | null>(null);
  const [syncRetrying, setSyncRetrying] = useState(false);

  async function save() {
    const choice = choices[choiceIndex];
    if (choice === undefined || scopeId === "") return;
    setBusy(true);
    setError(null);
    setSyncResult(null);
    try {
      const result = await putFamilyRelationship({
        scopeId,
        subjectEntityId: edge.subject,
        predicate: choice.predicate,
        objectEntityId: edge.object,
        inversePredicate: choice.inversePredicate,
      });
      setSyncResult(result);
      await onChanged();
    } catch (cause) {
      setError(describeReviewError(cause));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (scopeId === "") return;
    setBusy(true);
    setError(null);
    setSyncResult(null);
    try {
      const result = await deleteFamilyRelationship({
        scopeId,
        subjectEntityId: edge.subject,
        objectEntityId: edge.object,
      });
      setSyncResult(result);
      await onChanged();
    } catch (cause) {
      setError(describeReviewError(cause));
    } finally {
      setBusy(false);
    }
  }

  async function retrySync() {
    setSyncRetrying(true);
    setError(null);
    try {
      const result = await retryMemoryCorrections(scopeId);
      setSyncResult({
        sync_status: result.error ? "pending" : "synced",
        sync_error: result.error,
      });
      if (!result.error) await onChanged();
    } catch (cause) {
      setError(describeReviewError(cause));
    } finally {
      setSyncRetrying(false);
    }
  }

  return (
    <section className="graph-toolbar" role="region" aria-label="编辑关系">
      <strong>
        {subjectName} · {edge.predicate} → {objectName}
      </strong>
      <select
        aria-label="关系类型"
        value={choiceIndex}
        onChange={(event) => setChoiceIndex(Number(event.target.value))}
      >
        {choiceIndex < 0 ? <option value={-1}>当前关系不在词表里，请重新选择</option> : null}
        {choices.map((choice, index) => (
          <option key={choice.label} value={index}>
            {choice.label}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="button primary small"
        disabled={busy || choiceIndex < 0 || scopeId === ""}
        onClick={() => void save()}
      >
        保存谓词
      </button>
      <button
        type="button"
        className="button ghost small"
        disabled={busy || scopeId === ""}
        onClick={() => void remove()}
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
            : `关系已保存，但关联数据待同步${syncResult.sync_error ? `：${syncResult.sync_error}` : "。"}`}</span>
          {syncResult.sync_status === "pending" ? (
            <button type="button" className="button ghost small" disabled={syncRetrying || busy} onClick={() => void retrySync()}>
              {syncRetrying ? "正在重试…" : "重试同步"}
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

/** 拖拽建关系时那条跟随指针的虚线。单独成组件是为了 `layout` 变化时不必重算整图。 */
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
