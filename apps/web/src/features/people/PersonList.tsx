import { useState, type FormEvent } from "react";

import type { PersonPayload } from "../../shared/api/client";
import { publicPersonName } from "./reviewErrors";

export interface PersonListProps {
  people: PersonPayload[];
  busy: boolean;
  onRename: (personId: string, name: string) => Promise<boolean>;
}

/**
 * 已确认的人物。「命名」是产品硬约束的一部分：簇确认只给了一个初始名字，
 * 之后改名字走 ``PATCH /people/{id}/name``——旧名字由后端留成别名，这里只显示条数。
 */
export function PersonList({ people, busy, onRename }: PersonListProps) {
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (editing === null) {
      return;
    }
    const ok = await onRename(editing, name);
    if (ok) {
      setEditing(null);
      setName("");
    }
  }

  if (people.length === 0) {
    return <p className="empty">还没有已确认的人物。先在上面确认一位。</p>;
  }

  return (
    <ul className="person-list">
      {people.map((person) => (
        <li key={person.id} className="person-row">
          <div className="person-head">
            <strong>{publicPersonName(person.canonical_name)}</strong>
            <small>
              {person.family_role ?? "未确认家庭角色"} · {person.aliases.length} 个别名
            </small>
          </div>
          {editing === person.id ? (
            <form className="person-rename" onSubmit={submit}>
              <input
                aria-label="新的姓名"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
              <button type="submit" className="primary" disabled={busy}>
                保存
              </button>
              <button type="button" onClick={() => setEditing(null)}>
                取消
              </button>
            </form>
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setEditing(person.id);
                setName(publicPersonName(person.canonical_name));
              }}
            >
              改名
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}
