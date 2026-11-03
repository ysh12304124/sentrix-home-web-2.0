import { useState } from "react";

import {
  fetchTurnEvents,
  type AssistantProgressEvent,
} from "../../shared/api/client";

/**
 * 一轮回答的「证据 / 推理时间线」。
 *
 * 1.0 把同一组 `public_progress` 事件渲染成一行行卡片（`src/app.js:359-527`）。
 * 2.0 的进度事件字段完全一致，这里只改呈现：按**事件自己声明的** `stage` / `kind`
 * 分组，每组一张可折叠卡片。没有新字段——卡片里出现的每个键都来自事件的
 * `payload`（`run_turn.py` 的 `_tool_event` / `plan` / `guard` / `terminal`）。
 *
 * 历史回合的过程事件不在会话详情里，只在 `GET /assistant/turns/{id}/events`
 * 这条 SSE 流里。所以时间线默认收起，用户展开且手上没有事件时才去读一次。
 */

type CardKind =
  | "planning"
  | "thinking"
  | "tool"
  | "observation"
  | "inspecting"
  | "recovering"
  | "final";

interface TimelineCard {
  key: string;
  kind: CardKind;
  toolCallId: string;
  events: AssistantProgressEvent[];
}

const CARD_LABELS: Record<CardKind, string> = {
  planning: "先想一想",
  thinking: "正在理解",
  tool: "查找资料",
  observation: "看到的内容",
  inspecting: "仔细查看",
  recovering: "换个办法",
  final: "整理回答",
};

/** 后端的阶段名（`domain/assistant/models.py` 的 `ProgressStage`）→ 卡片类别。 */
const STAGE_KIND: Record<string, CardKind> = {
  thinking: "thinking",
  planning: "planning",
  action: "tool",
  tool_result: "observation",
  inspecting: "inspecting",
  recovering: "recovering",
  finalizing: "final",
  terminal: "final",
};

function payloadOf(event: AssistantProgressEvent): Record<string, unknown> {
  return event.payload ?? {};
}

function stringOf(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * 一次工具调用的两条事件（`action` 发参数、`tool_result` 发结果）用
 * `payload.tool_call_id` 对上，合成同一张卡片。对不上的 `tool_result` 单独成卡。
 */
export function buildCards(events: AssistantProgressEvent[]): TimelineCard[] {
  const cards: TimelineCard[] = [];
  const openTools = new Map<string, TimelineCard>();

  for (const event of events) {
    const payload = payloadOf(event);
    const toolCallId = stringOf(payload.tool_call_id);

    if (event.stage === "action") {
      const card: TimelineCard = {
        key: `tool-${event.step_index}`,
        kind: "tool",
        toolCallId,
        events: [event],
      };
      cards.push(card);
      if (toolCallId !== "") openTools.set(toolCallId, card);
      continue;
    }

    if (event.stage === "tool_result") {
      const open = toolCallId !== "" ? openTools.get(toolCallId) : undefined;
      if (open !== undefined) {
        open.events.push(event);
        openTools.delete(toolCallId);
        continue;
      }
      // 终止动作（`final_answer`）只发一条 tool_result，没有前置的 action。
      const kind: CardKind = payload.tool === "final_answer" ? "final" : "observation";
      cards.push({
        key: `${event.stage}-${event.step_index}`,
        kind,
        toolCallId,
        events: [event],
      });
      continue;
    }

    // 连续的「思考」「纠正」合成一张卡；其余阶段各自成卡。
    const kind = STAGE_KIND[event.stage] ?? "thinking";
    const previous = cards[cards.length - 1];
    if (
      previous !== undefined &&
      previous.kind === kind &&
      (kind === "thinking" || kind === "recovering")
    ) {
      previous.events.push(event);
      continue;
    }
    cards.push({
      key: `${event.stage}-${event.step_index}`,
      kind,
      toolCallId,
      events: [event],
    });
  }

  return cards;
}

/** 只挑用户能理解的结果，不显示内部字段名、结果集 ID 或检索后端。 */
export function summarizeResult(value: unknown): string[] {
  if (typeof value !== "object" || value === null) return [];
  const data = value as Record<string, unknown>;
  const parts: string[] = [];
  if (typeof data.total === "number") parts.push(`找到 ${data.total} 张相关照片`);
  if (Array.isArray(data.asset_ids) && data.asset_ids.length > 0) {
    parts.push(`候选 ${data.asset_ids.length} 张`);
  }
  if (typeof data.delivered === "number") parts.push(`带回 ${data.delivered} 张照片`);
  if (data.found === true) parts.push("找到了相关内容");
  if (data.found === false) parts.push("没有找到相关内容");
  if (typeof data.full_text === "string" && data.full_text !== "") parts.push("读完了这段文字");
  return parts;
}

const TOOL_LABELS: Record<string, string> = {
  search: "正在查找相关照片…",
  search_photos: "正在查找相关照片…",
  search_memories: "正在查找相关照片…",
  recall: "正在回忆相关内容…",
  retrieve: "正在回忆相关内容…",
  inspect_photo: "正在仔细查看这张照片…",
  view_photo: "正在仔细查看这张照片…",
  deliver_photos: "正在挑选要展示的照片…",
  deliver: "正在挑选要展示的照片…",
  final_answer: "正在整理回答…",
};

function toolLabel(tool: string): string {
  return TOOL_LABELS[tool] ?? "正在处理这一步…";
}

function terminalLabel(status: string): string {
  if (status === "complete") return "回答完成。";
  if (status === "partial") return "回答完成，但有一小部分内容暂时没找到。";
  if (status === "cancelled") return "已停止回答。";
  if (status === "blocked") return "这一步被安全规则拦住了。";
  return "这次没有完成，可以再问一次。";
}

/** 把一条过程事件翻成日常语言；同一张卡里连续重复的句子只保留一次。 */
function userStepText(event: AssistantProgressEvent): string {
  const payload = payloadOf(event);
  if (event.stage === "action" || event.kind === "model_action") {
    return toolLabel(stringOf(payload.tool));
  }
  if (event.stage === "tool_result") {
    const summary = summarizeResult(payload.result);
    return summary.length > 0 ? summary.join("；") : "这一步完成了。";
  }
  if (event.kind === "tool_error") return "这一步没有成功，换一种方式继续。";
  if (event.kind === "guard" || event.kind === "judge") {
    return payload.hard === true
      ? "有一项要求暂时无法满足，正在换一种方式。"
      : "正在检查回答是否符合要求。";
  }
  if (event.kind === "terminal") return terminalLabel(stringOf(payload.status));
  if (event.kind === "plan") return "先把问题拆成几步。";
  const text = event.text.trim();
  return text !== "" ? text : "正在继续思考。";
}

function RequirementList({ requirements }: { requirements: unknown }) {
  if (!Array.isArray(requirements)) return null;
  const rows = requirements.filter(
    (item): item is Record<string, unknown> => typeof item === "object" && item !== null,
  );
  if (rows.length === 0) return null;
  return (
    <ul className="phase-requirements">
      {rows.map((item, index) => (
        <li key={stringOf(item.id) || String(index)}>
          <span>{stringOf(item.description) || "需要确认的内容"}</span>
          <small>
            {stringOf(item.status) === "satisfied"
              ? "已找到"
              : stringOf(item.status) === "unmet"
                ? "暂时没找到"
                : "正在确认"}
          </small>
        </li>
      ))}
    </ul>
  );
}

function PhaseEvent({ event, previous }: { event: AssistantProgressEvent; previous: string }) {
  const payload = payloadOf(event);
  const text = userStepText(event);
  if (text === previous) return null;
  return (
    <div className={`phase-event phase-status-${event.status}`}>
      <p>{text}</p>
      {event.kind === "plan" ? <RequirementList requirements={payload.requirements} /> : null}
    </div>
  );
}

/**
 * 一张卡 = 一条 1.0 的 `timeline-card`：**垂直时间轴上的一个节点**，不是折叠分组。
 *
 * 1.0 `app.js:399-421` 的形状是 `<article class="timeline-card {running|blocked|complete}">`
 * + `<span class="timeline-dot">` + 标题/正文/artifact，**始终展开**——用户扫一眼就知道
 * 助手刚才做了什么，不需要再点一次。2.0 之前做成 `<details>` 折叠分组，形态与 1.0 不同，
 * 而且默认收起时整条推理链是「空白」的。
 *
 * 状态着色沿用 1.0 的三态：running（进行中）/ blocked（被拦、报错、取消）/ complete。
 */
const BLOCKED_STATUSES = new Set(["blocked", "denied", "error", "cancelled", "partial"]);

function PhaseCard({ card }: { card: TimelineCard }) {
  const running = card.events.some((event) => event.status === "running");
  const blocked = card.events.some((event) => BLOCKED_STATUSES.has(event.status));
  const state = running ? "running" : blocked ? "blocked" : "complete";
  let previous = "";
  return (
    <article className={`timeline-card ${state}`}>
      <span className="timeline-dot" aria-hidden="true" />
      <div>
        <strong>{CARD_LABELS[card.kind]}</strong>
        {card.events.map((event) => {
          const current = userStepText(event);
          const node = <PhaseEvent key={event.step_index} event={event} previous={previous} />;
          previous = current;
          return node;
        })}
      </div>
    </article>
  );
}

/** `pending` / `running` 之外都是终态——只有终态才值得/才能回放事件。 */
export function isTerminalStatus(status: string): boolean {
  return status !== "" && status !== "pending" && status !== "running";
}

export interface TurnTimelineProps {
  events: AssistantProgressEvent[];
  /** 历史回合的 id；有它且手上没有事件时，展开会去读那一条 SSE。 */
  turnId?: string;
  status?: string;
  /** 测试注入点；生产走 `client.fetchTurnEvents`。 */
  loadEvents?: (turnId: string) => Promise<AssistantProgressEvent[]>;
  defaultOpen?: boolean;
  onLoaded?: (events: AssistantProgressEvent[]) => void;
}

export function TurnTimeline({
  events,
  turnId = "",
  status = "",
  loadEvents = fetchTurnEvents,
  defaultOpen = false,
  onLoaded,
}: TurnTimelineProps) {
  const [open, setOpen] = useState(defaultOpen);
  const [loaded, setLoaded] = useState<AssistantProgressEvent[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const available = events.length > 0 ? events : (loaded ?? []);
  const cards = buildCards(available);
  const canLoad = events.length === 0 && turnId !== "" && isTerminalStatus(status);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next || !canLoad || loading) return;
    setLoading(true);
    setLoadError(null);
    try {
      const fetched = await loadEvents(turnId);
      setLoaded(fetched);
      onLoaded?.(fetched);
    } catch (cause) {
      setLoadError((cause as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="turn-timeline">
      <button
        type="button"
        className="turn-timeline-toggle"
        aria-expanded={open}
        onClick={() => void toggle()}
      >
        思考过程{available.length > 0 ? ` · ${available.length} 步` : ""}
      </button>
      {open ? (
        <div className="turn-timeline-body">
          {loading ? <p className="turn-timeline-note">正在读取回合记录…</p> : null}
          {loadError !== null ? (
            <p className="error">读取回合记录失败：{loadError}</p>
          ) : null}
          {/* `.timeline-list` 才是那条竖线的容器（1.0 `:628` 的 `::before`）；
              卡片自己只负责圆点与底色。 */}
          <div className="timeline-list">
            {cards.map((card) => (
              <PhaseCard key={card.key} card={card} />
            ))}
          </div>
          {!loading && loadError === null && cards.length === 0 ? (
            <p className="turn-timeline-note">没有可展示的过程记录。</p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
