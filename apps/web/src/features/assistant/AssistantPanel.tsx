import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import {
  deliveredPhotoUrl,
  getConversation,
  type AssistantAttachment,
  type AssistantProgressEvent,
  type ConversationDetail,
} from "../../shared/api/client";
import { Lightbox } from "../../shared/ui/Lightbox";
import { ResultSetWall } from "./ResultSetWall";
import { TurnTimeline, isTerminalStatus } from "./TurnTimeline";
import { useTurnStream } from "./useTurnStream";

interface Exchange {
  key: string;
  question: string;
  answer: string;
  attachments: AssistantAttachment[];
  status: string;
  reason: string;
  turnId: string;
  /** 已拿到的过程事件。历史回合这里是空的，展开时间线时才去读。 */
  events: AssistantProgressEvent[];
}

/**
 * 把会话详情摊成「一问一答」。
 *
 * 消息与回合是两个数组：`messages` 给顺序与文本，`turns` 给附件与终态。用
 * `turn_id` 对起来——**交付的照片只存在回合那一侧**，只看 messages 会全部丢掉。
 */
function replayExchanges(detail: ConversationDetail): Exchange[] {
  const turns = new Map(detail.turns.map((turn) => [turn.id, turn]));
  const exchanges: Exchange[] = [];
  const byTurn = new Map<string, Exchange>();

  for (const message of detail.messages) {
    if (message.role === "user") {
      const exchange: Exchange = {
        key: message.id,
        question: message.content,
        answer: "",
        attachments: [],
        status: "running",
        reason: "",
        turnId: message.turn_id,
        events: [],
      };
      exchanges.push(exchange);
      if (message.turn_id !== "") byTurn.set(message.turn_id, exchange);
      continue;
    }

    const exchange = byTurn.get(message.turn_id);
    if (exchange === undefined) continue;
    const turn = turns.get(message.turn_id);
    exchange.answer = message.content || (turn?.answer ?? "");
    exchange.attachments = turn?.attachments ?? [];
    exchange.status = turn?.status ?? "complete";
    exchange.reason = turn?.termination_reason ?? "";
  }

  // 回合已落库但 assistant 消息还没写（进行中/异常退出），也要出现在回放里。
  for (const turn of detail.turns) {
    const exchange = byTurn.get(turn.id);
    if (exchange !== undefined) {
      exchange.status = turn.status;
      exchange.reason = turn.termination_reason;
      exchange.attachments = turn.attachments;
      if (exchange.answer === "") exchange.answer = turn.answer;
      continue;
    }
    exchanges.push({
      key: turn.id,
      question: turn.question,
      answer: turn.answer,
      attachments: turn.attachments,
      status: turn.status,
      reason: turn.termination_reason,
      turnId: turn.id,
      events: [],
    });
  }

  return exchanges;
}

/**
 * 助手对话。问一句「给我看看在海边的照片」，回答下面就是能直接点开的照片。
 *
 * 交付的照片只来自后端 ``complete`` 帧里的 ``attachments``——那是**模型在终止动作
 * （``final_answer``）的 ``selected_image_handles`` 字段里显式选的那些**。前端不自己从
 * 候选里挑图，也不做任何相关性判断。
 *
 * ``scopeId`` **只用于拼结果集照片的授权 URL**（端点要求 scope 匹配）；会话的创建与
 * 切换仍然在 AssistantPage，面板不参与 scope 决策。
 */
export function AssistantPanel({
  conversationId,
  scopeId,
  initialQuestion = "",
}: {
  conversationId: string | null;
  scopeId: string;
  /** 概览页交过来的问题；非空时自动问一次，问过就不再重复。 */
  initialQuestion?: string;
}) {
  // 「问过就不再重复」用一个 ref 记已消费的问题，而不是清 URL：清 URL 会触发
  // 一次导航，而用户按「后退」时那条 URL 又是对的——两件事会打架。
  const consumed = useRef<string | null>(null);
  const [text, setText] = useState("");
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const {
    progress,
    result,
    busy,
    error,
    ask,
    cancel,
    cancelled,
    executionState,
    attemptNo,
  } = useTurnStream();

  const settled = useRef<string | null>(null);
  /** 当前这一轮是哪个会话的哪条 exchange——切换会话后到达的结果不该串台。 */
  const [live, setLive] = useState<{ conversationId: string; key: string } | null>(null);
  // 交付照片点开是全屏看图，不是新标签打开原文件。
  const [lightbox, setLightbox] = useState<{ src: string; caption: string } | null>(null);
  const progressRef = useRef<AssistantProgressEvent[]>([]);
  const historyToken = useRef(0);
  const sequence = useRef(0);

  useEffect(() => {
    progressRef.current = progress;
  }, [progress]);

  // 切会话：加载并回放历史消息。换了会话就作废上一次未完成的读取。
  useEffect(() => {
    const token = ++historyToken.current;
    settled.current = null;
    setLive(null);
    setHistoryError(null);

    if (conversationId === null) {
      setExchanges([]);
      setHistoryLoading(false);
      return;
    }

    setExchanges([]);
    setHistoryLoading(true);
    getConversation(conversationId)
      .then((detail) => {
        if (token !== historyToken.current) return;
        setExchanges(replayExchanges(detail));
      })
      .catch((cause: Error) => {
        if (token !== historyToken.current) return;
        setExchanges([]);
        setHistoryError(cause.message);
      })
      .finally(() => {
        if (token === historyToken.current) setHistoryLoading(false);
      });
  }, [conversationId]);

  const runQuestion = useCallback(
    (question: string) => {
      if (conversationId === null) return;
      const key = `live-${(sequence.current += 1)}`;
      setLive({ conversationId, key });
      setExchanges((previous) => [
        ...previous,
        {
          key,
          question,
          answer: "",
          attachments: [],
          status: "running",
          reason: "",
          turnId: "",
          events: [],
        },
      ]);
      void ask(conversationId, question);
    },
    [ask, conversationId],
  );

  // ``complete`` 到了就补到发起它的那条提问上。**按 key 定位**，不按「最后一条」——
  // 中途切换会话时，最后一条已经不属于这一轮了。
  useEffect(() => {
    if (result === null || live === null || live.conversationId !== conversationId) return;
    if (settled.current === result.turn_id) return;
    settled.current = result.turn_id;
    const key = live.key;
    const events = [...progressRef.current];
    setExchanges((previous) =>
      previous.map((item) =>
        item.key === key
          ? {
              ...item,
              answer: result.answer,
              attachments: result.attachments,
              status: result.status,
              reason: result.termination_reason,
              turnId: result.turn_id,
              events,
            }
          : item,
      ),
    );
  }, [conversationId, live, result]);

  // 建回合/轮询失败时 hook 只给一句 error；把那条提问标成失败，别一直停在「处理中」。
  useEffect(() => {
    if (error === null || live === null) return;
    setExchanges((previous) =>
      previous.map((item) =>
        item.key === live.key && item.status === "running"
          ? { ...item, status: "failed", reason: error }
          : item,
      ),
    );
  }, [error, live]);

  // 用户点了停止：就地收尾这一条，不再等一个不会到达的 complete。
  useEffect(() => {
    if (!cancelled || live === null) return;
    setExchanges((previous) =>
      previous.map((item) =>
        item.key === live.key && item.status === "running"
          ? { ...item, status: "cancelled", reason: "已停止" }
          : item,
      ),
    );
  }, [cancelled, live]);

  // 自动提问：等会话历史读完、且这个问题还没被消费过。
  useEffect(() => {
    const trimmed = initialQuestion.trim();
    if (trimmed === "" || conversationId === null || busy || historyLoading) return;
    if (consumed.current === `${conversationId}\u0000${trimmed}`) return;
    consumed.current = `${conversationId}\u0000${trimmed}`;
    runQuestion(trimmed);
  }, [busy, conversationId, historyLoading, initialQuestion, runQuestion]);

  const storeEvents = useCallback((key: string, events: AssistantProgressEvent[]) => {
    setExchanges((previous) =>
      previous.map((item) => (item.key === key ? { ...item, events } : item)),
    );
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const question = text.trim();
    if (question === "" || conversationId === null || busy) {
      return;
    }
    setText("");
    runQuestion(question);
  }

  const lastKey = exchanges[exchanges.length - 1]?.key ?? "";

  return (
    <section className="assistant">
      <h2>问一句</h2>
      <form className="assistant-ask" onSubmit={submit}>
        <input
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="例如：给我看看在海边的照片"
          aria-label="向助手提问"
          disabled={conversationId === null || historyLoading}
        />
        <button
          type="submit"
          disabled={busy || historyLoading || conversationId === null || text.trim() === ""}
        >
          {busy ? "处理中…" : "发送"}
        </button>
        {busy ? (
          <button type="button" className="ghost-action" onClick={() => void cancel()}>
            停止回答
          </button>
        ) : null}
      </form>

      {error !== null ? <p className="error">{error}</p> : null}
      {historyError !== null ? (
        <p className="error">历史消息加载失败：{historyError}</p>
      ) : null}
      {historyLoading ? <p className="assistant-history">正在加载历史消息…</p> : null}

      <div className="assistant-log">
        {exchanges.map((exchange) => {
          const liveExchange =
            busy && live !== null && live.key === exchange.key && live.conversationId === conversationId;
          const events =
            exchange.events.length > 0 ? exchange.events : liveExchange ? progress : [];
          return (
            <article key={exchange.key} className="assistant-exchange">
              {exchange.question ? <p className="question">{exchange.question}</p> : null}
              {liveExchange && executionState !== "" ? (
                <p className="note" role="status">
                  {executionLabel(executionState, attemptNo)}
                </p>
              ) : null}
              {exchange.answer ? <p className="answer">{exchange.answer}</p> : null}
              {["cancelled", "failed", "blocked"].includes(exchange.status) ? (
                <p className="note">
                  {exchange.status === "cancelled"
                    ? "已停止回答。"
                    : "这次回答没有完成，可以再问一次。"}
                </p>
              ) : null}
              {exchange.attachments.length > 0 ? (
                <ul className="assistant-photos">
                  {exchange.attachments.map((photo) => (
                    <li key={photo.handle}>
                      {/* **点图片是放大**，不是新标签打开原文件——后者遇到 HEIC
                          或不认识的 MIME 会被浏览器直接下载。 */}
                      <button
                        type="button"
                        className="assistant-photo-open"
                        onClick={() =>
                          setLightbox({
                            src: deliveredPhotoUrl(photo.asset_id, photo.file_name),
                            caption: "相关照片",
                          })
                        }
                      >
                        <img
                          src={deliveredPhotoUrl(photo.asset_id, photo.file_name)}
                          alt="相关照片"
                          loading="lazy"
                        />
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}

              {events.length > 0 || (exchange.turnId !== "" && isTerminalStatus(exchange.status)) ? (
                <TurnTimeline
                  events={events}
                  turnId={exchange.turnId}
                  status={exchange.status}
                  defaultOpen={liveExchange}
                  onLoaded={(loaded) => storeEvents(exchange.key, loaded)}
                />
              ) : null}

              <ResultSetWall
                events={events}
                scopeId={scopeId}
                busy={busy}
                onNextPage={
                  exchange.key === lastKey ? () => runQuestion("下一页") : undefined
                }
              />
            </article>
          );
        })}
      </div>
      {lightbox === null ? null : (
        <Lightbox
          src={lightbox.src}
          caption={lightbox.caption}
          onClose={() => setLightbox(null)}
        />
      )}
    </section>
  );
}

function executionLabel(state: string, attemptNo: number): string {
  const suffix = attemptNo > 1 ? `（第 ${attemptNo} 次尝试）` : "";
  if (state === "recovering") return `正在恢复这次回答${suffix}…`;
  if (state === "waiting_dependency") return "模型服务暂不可用，恢复后会自动继续，不需要重新提问。";
  if (state === "retry_scheduled") return `这次回答稍后会自动重试${suffix}…`;
  return "";
}
