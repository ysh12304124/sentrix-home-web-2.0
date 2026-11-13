"""会话与消息的 SQLite 仓储。SQL 只出现在这一层。"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Sequence
from datetime import datetime

from sentrix.domain.assistant.models import ChatRole, Conversation, Message
from sentrix.infrastructure.persistence.database import Database

_CONVERSATION_COLUMNS = "id, scope_id, title, state, created_at, updated_at, last_message_at"
_MESSAGE_COLUMNS = "id, conversation_id, scope_id, turn_id, role, content, created_at"


def _row_to_conversation(row: sqlite3.Row) -> Conversation:
    return Conversation(
        id=row["id"],
        scope_id=row["scope_id"],
        title=row["title"],
        state=row["state"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        last_message_at=datetime.fromisoformat(row["last_message_at"]),
    )


def _row_to_message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        scope_id=row["scope_id"],
        turn_id=row["turn_id"],
        role=ChatRole(row["role"]),
        content=row["content"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class SqliteConversationRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, *, scope_id: str, title: str, created_at: datetime) -> Conversation:
        conversation = Conversation(
            id=f"conv_{uuid.uuid4().hex[:12]}",
            scope_id=scope_id,
            title=title or "新对话",
            state="active",
            created_at=created_at,
            updated_at=created_at,
            last_message_at=created_at,
        )
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO agent_conversations"
                " (id, scope_id, title, state, created_at, updated_at, last_message_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    conversation.id,
                    conversation.scope_id,
                    conversation.title,
                    conversation.state,
                    conversation.created_at.isoformat(),
                    conversation.updated_at.isoformat(),
                    conversation.last_message_at.isoformat(),
                ),
            )
        return conversation

    def get(self, conversation_id: str) -> Conversation | None:
        # 软删的会话对外就是「不存在」：``state = 'active'`` 是查询条件而不是调用方的
        # 事后判断——1.0 的 ``get_conversation`` 也是这么做的（``agent_conversation.py:79``）。
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_CONVERSATION_COLUMNS} FROM agent_conversations WHERE id = ? AND state = 'active'",
                (conversation_id,),
            ).fetchone()
        return _row_to_conversation(row) if row else None

    def list_for_scope(self, scope_id: str, limit: int) -> Sequence[Conversation]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_CONVERSATION_COLUMNS} FROM agent_conversations"
                " WHERE scope_id = ? AND state = 'active'"
                " ORDER BY last_message_at DESC, id DESC LIMIT ?",
                (scope_id, max(1, limit)),
            ).fetchall()
        return [_row_to_conversation(row) for row in rows]

    def rename(self, conversation_id: str, title: str, updated_at: datetime) -> bool:
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE agent_conversations SET title = ?, updated_at = ? WHERE id = ? AND state = 'active'",
                (title, updated_at.isoformat(), conversation_id),
            )
        return cursor.rowcount > 0

    def delete(self, conversation_id: str, updated_at: datetime) -> bool:
        """软删会话、硬删消息与回合。

        1.0 的 ``delete_conversation``（``agent_conversation.py:134``）就是这个口径。
        照片、观察与原始资产一律不动——「导入源不得写入记忆」的另一面是「删对话不得删记忆」。
        """
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE agent_conversations SET state = 'deleted', updated_at = ?"
                " WHERE id = ? AND state = 'active'",
                (updated_at.isoformat(), conversation_id),
            )
            if cursor.rowcount == 0:
                return False
            connection.execute(
                "DELETE FROM agent_turn_events WHERE turn_id IN"
                " (SELECT id FROM agent_turns WHERE conversation_id = ?)",
                (conversation_id,),
            )
            connection.execute("DELETE FROM agent_turns WHERE conversation_id = ?", (conversation_id,))
            connection.execute(
                "DELETE FROM agent_conversation_messages WHERE conversation_id = ?",
                (conversation_id,),
            )
        return True

    def add_message(
        self,
        *,
        conversation_id: str,
        scope_id: str,
        turn_id: str,
        role: str,
        content: str,
        created_at: datetime,
    ) -> Message:
        message = Message(
            id=f"msg_{uuid.uuid4().hex[:12]}",
            conversation_id=conversation_id,
            scope_id=scope_id,
            turn_id=turn_id,
            role=ChatRole(role),
            content=content,
            created_at=created_at,
        )
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO agent_conversation_messages"
                " (id, conversation_id, scope_id, turn_id, role, content, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    message.id,
                    message.conversation_id,
                    message.scope_id,
                    message.turn_id,
                    message.role.value,
                    message.content,
                    message.created_at.isoformat(),
                ),
            )
            connection.execute(
                "UPDATE agent_conversations SET last_message_at = ?, updated_at = ? WHERE id = ?",
                (created_at.isoformat(), created_at.isoformat(), conversation_id),
            )
        return message

    def list_messages(self, conversation_id: str, limit: int) -> Sequence[Message]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_MESSAGE_COLUMNS} FROM readable_agent_conversation_messages"
                " WHERE conversation_id = ? ORDER BY created_at ASC, rowid ASC LIMIT ?",
                (conversation_id, max(1, limit)),
            ).fetchall()
        return [_row_to_message(row) for row in rows]

    def list_messages_for_turns(self, conversation_id: str, turn_ids: Sequence[str]) -> Sequence[Message]:
        unique = list(dict.fromkeys(turn_ids))
        if not unique:
            return []
        placeholders = ",".join("?" for _ in unique)
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_MESSAGE_COLUMNS} FROM readable_agent_conversation_messages"
                f" WHERE conversation_id = ? AND turn_id IN ({placeholders})"
                " ORDER BY created_at ASC, rowid ASC",
                (conversation_id, *unique),
            ).fetchall()
        return [_row_to_message(row) for row in rows]

    def history_for_prompt(self, conversation_id: str, limit: int) -> Sequence[Message]:
        """最近 ``limit`` 条，按时间正序返回。

        1.0 取 8 条（``app.py:2913``），但把它们拼成一段文本折进 system 消息——理由是
        严格 chat template 要求 system 必须在开头。2.0 用原生消息角色，历史就是历史，
        不需要这个绕法。
        """
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_MESSAGE_COLUMNS} FROM readable_agent_conversation_messages"
                " WHERE conversation_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (conversation_id, max(1, limit)),
            ).fetchall()
        return [_row_to_message(row) for row in reversed(rows)]
