/**
 * 家庭记忆助手。对应 1.0 `src/app.js:619` 的 `searchView()`（`assistant-layout` 在 `:622-623`）。
 *
 * 左边会话列表、右边消息流。1.0 的 `conversationRail()`（`:607-617`）就是这个结构——
 * 把会话的选择放在消息流旁边，而不是藏进一个下拉里；重命名/删除也在每一项上
 * （`:1414-1453`），删除有二次确认。
 *
 * 「停止本轮回答」走 `POST /assistant/turns/{id}/cancel`：取消标志持久化，
 * 执行器在安全检查点收尾；界面不会因为断流把同一次提问变成两次。
 */

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";

import { useScope } from "../../app/ScopeProvider";
import {
  createConversation,
  deleteConversation,
  listConversations,
  renameConversation,
  type ConversationSummary,
} from "../../shared/api/client";
import { PageHeader } from "../../shared/ui/PageHeader";
import { AssistantPanel } from "./AssistantPanel";
import "../../styles/assistant.css";
import "./assistantDetail.css";

export function AssistantPage() {
  const { scopeId } = useScope();
  // 概览页的提问框把问题塞在 `?q=` 里交过来（1.0 的 searchBar() 是直接 push 一个
  // assistant turn）。读出来交给面板，面板问完就不再重复。
  const [searchParams] = useSearchParams();
  const initialQuestion = searchParams.get("q") ?? "";
  const [conversations, setConversations] = useState<readonly ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 会话项的「更多操作」菜单：同一时间只开一个。
  const [menuId, setMenuId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameText, setRenameText] = useState("");
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const items = await listConversations(scopeId);
      setConversations(items);
      setError(null);
      return items;
    } catch (cause) {
      setError((cause as Error).message);
      return [];
    }
  }, [scopeId]);

  // 进入这一页或换相册：拉列表；一个都没有就建一个（否则用户面对的是不能输入的输入框）。
  useEffect(() => {
    let alive = true;
    void reload().then(async (items) => {
      if (!alive) return;
      if (items.length > 0) {
        setActiveId(items[0].id);
        return;
      }
      try {
        const created = await createConversation(scopeId);
        if (!alive) return;
        setConversations([created]);
        setActiveId(created.id);
      } catch (cause) {
        if (alive) setError((cause as Error).message);
      }
    });
    return () => {
      alive = false;
    };
  }, [reload, scopeId]);

  async function newConversation() {
    try {
      const created = await createConversation(scopeId);
      setConversations((previous) => [created, ...previous]);
      setActiveId(created.id);
      setMenuId(null);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  function openConversation(id: string) {
    setActiveId(id);
    setMenuId(null);
    setRenamingId(null);
    setConfirmingId(null);
  }

  function beginRename(conversation: ConversationSummary) {
    setMenuId(null);
    setConfirmingId(null);
    setRenamingId(conversation.id);
    setRenameText(conversation.title || "");
  }

  async function submitRename(event: FormEvent) {
    event.preventDefault();
    if (renamingId === null) return;
    const title = renameText.trim();
    if (title === "") return;
    try {
      const updated = await renameConversation(renamingId, title);
      setConversations((previous) =>
        previous.map((item) => (item.id === updated.id ? updated : item)),
      );
      setRenamingId(null);
      setError(null);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  function beginDelete(id: string) {
    setMenuId(null);
    setRenamingId(null);
    setConfirmingId(id);
  }

  async function confirmDelete(id: string) {
    try {
      await deleteConversation(id);
      const remaining = conversations.filter((item) => item.id !== id);
      setConversations(remaining);
      setConfirmingId(null);
      setError(null);
      if (activeId !== id) return;
      if (remaining.length > 0) {
        setActiveId(remaining[0].id);
        return;
      }
      // 删掉的是最后一个：立刻补一个，否则右边会是一个不能提问的空面板。
      setActiveId(null);
      const created = await createConversation(scopeId);
      setConversations([created]);
      setActiveId(created.id);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  return (
    <>
      <PageHeader
        kicker="记忆组织 / 对话"
        title="你想找回哪一段记忆？"
        description="从人物、时间、地点、物体或一句描述开始，答案会带回原始证据。"
      />
      {error ? <p className="error">{error}</p> : null}
      <div className="assistant-layout">
        <aside className="conversation-rail">
          <button
            type="button"
            className="button primary full"
            onClick={() => void newConversation()}
          >
            新建对话
          </button>
          {conversations.length === 0 ? (
            <p className="rail-empty">还没有对话</p>
          ) : (
            conversations.map((conversation) => {
              const title = conversation.title || "未命名对话";
              if (renamingId === conversation.id) {
                return (
                  <form key={conversation.id} className="rail-rename" onSubmit={submitRename}>
                    <input
                      aria-label="对话名称"
                      value={renameText}
                      onChange={(event) => setRenameText(event.target.value)}
                      autoFocus
                    />
                    <button type="submit" disabled={renameText.trim() === ""}>
                      保存
                    </button>
                    <button type="button" onClick={() => setRenamingId(null)}>
                      取消
                    </button>
                  </form>
                );
              }
              if (confirmingId === conversation.id) {
                return (
                  <div key={conversation.id} className="rail-confirm">
                    <p>删除这个对话？</p>
                    <div>
                      <button
                        type="button"
                        className="danger"
                        onClick={() => void confirmDelete(conversation.id)}
                      >
                        确认删除
                      </button>
                      <button type="button" onClick={() => setConfirmingId(null)}>
                        取消
                      </button>
                    </div>
                  </div>
                );
              }
              return (
                <div key={conversation.id} className="rail-entry">
                  <button
                    type="button"
                    className={
                      conversation.id === activeId ? "rail-item active" : "rail-item"
                    }
                    onClick={() => openConversation(conversation.id)}
                  >
                    <strong>{title}</strong>
                    <small>{conversation.last_message_at?.slice(0, 16) ?? ""}</small>
                  </button>
                  <button
                    type="button"
                    className="rail-more"
                    aria-label={`更多操作：${title}`}
                    aria-expanded={menuId === conversation.id}
                    onClick={() =>
                      setMenuId((current) =>
                        current === conversation.id ? null : conversation.id,
                      )
                    }
                  >
                    •••
                  </button>
                  {menuId === conversation.id ? (
                    <div className="rail-menu">
                      <button type="button" onClick={() => beginRename(conversation)}>
                        重命名
                      </button>
                      <button
                        type="button"
                        className="danger"
                        onClick={() => beginDelete(conversation.id)}
                      >
                        删除
                      </button>
                    </div>
                  ) : null}
                </div>
              );
            })
          )}
        </aside>
        <div className="assistant-main">
          <AssistantPanel
            conversationId={activeId}
            scopeId={scopeId}
            initialQuestion={initialQuestion}
          />
        </div>
      </div>
    </>
  );
}
