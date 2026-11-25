import { createContext, useContext, useRef, useState } from "react";

export function usePeopleWriteState() {
  const active = useRef(new Set<string>());
  const [pending, setPending] = useState<ReadonlySet<string>>(() => new Set());

  async function write(key: string, action: () => Promise<void>) {
    if (active.current.has(key)) return;
    active.current.add(key);
    setPending(new Set(active.current));
    try {
      await action();
    } finally {
      active.current.delete(key);
      setPending(new Set(active.current));
    }
  }

  return { pending, write };
}

export const PeopleWritesContext = createContext<ReturnType<typeof usePeopleWriteState> | null>(null);

export function usePeopleWrites() {
  const page = useContext(PeopleWritesContext);
  const local = usePeopleWriteState();
  return page ?? local;
}

export function relationshipWriteKey(scope: string, a: string, b: string) {
  return JSON.stringify(["relationship", scope, ...[a, b].sort()]);
}
