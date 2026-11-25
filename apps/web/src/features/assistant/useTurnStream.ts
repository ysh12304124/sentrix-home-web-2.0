import { useCallback, useEffect, useRef, useState } from "react";

import { newClientId } from "../../shared/id";
import { errorCodeFromStatus, reportClientEvent } from "../../shared/clientEvents";
import {
  ApiError,
  cancelTurn as cancelTurnRequest,
  pollTurn,
  startTurn,
  turnEventsUrl,
  type AssistantCompleteEvent,
  type AssistantProgressEvent,
} from "../../shared/api/client";

type Accepted = Awaited<ReturnType<typeof startTurn>>;
type Submission = {
  message: string;
  /** 推理模式按回合选择（main a5daf519）；同一句话换了模式就是另一次提交。 */
  reasoning: boolean;
  key: string;
  pending: Promise<Accepted> | null;
  stopRequested: boolean;
};
type Subscription = {
  cursor: number;
  source: EventSource | null;
  timer: number | null;
  stopRequested: boolean;
};

const submissions = new Map<string, Submission>();

/** A route change during the POST must not race the next view's history read. */
export async function waitForTurnAcceptance(conversationId: string): Promise<void> {
  try {
    await submissions.get(conversationId)?.pending;
  } catch {
    // History is authoritative even if the client never received the receipt.
  }
}

export function useTurnStream() {
  const [progress, setProgress] = useState<AssistantProgressEvent[]>([]);
  const [result, setResult] = useState<AssistantCompleteEvent | null>(null);
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [executionState, setExecutionState] = useState("");
  const [attemptNo, setAttemptNo] = useState(0);
  const [turnId, setTurnId] = useState<string | null>(null);
  const [cancelled, setCancelled] = useState(false);
  const subscription = useRef<Subscription | null>(null);
  const submitting = useRef<Submission | null>(null);

  const detach = useCallback(() => {
    const current = subscription.current;
    subscription.current = null;
    current?.source?.close();
    if (current?.timer != null) window.clearTimeout(current.timer);
    submitting.current = null;
  }, []);

  useEffect(() => detach, [detach]);

  const reset = useCallback(() => {
    detach();
    setProgress([]);
    setResult(null);
    setError(null);
    setExecutionState("");
    setAttemptNo(0);
    setTurnId(null);
    setCancelled(false);
    setStopping(false);
    setBusy(false);
  }, [detach]);

  const subscribe = useCallback((id: string, operationId?: string) => {
    const current: Subscription = { cursor: 0, source: null, timer: null, stopRequested: false };
    subscription.current = current;
    setTurnId(id);
    setBusy(true);

    const isCurrent = () => subscription.current === current;
    const pushProgress = (payload: AssistantProgressEvent) => {
      if (!isCurrent() || payload.step_index <= current.cursor) return;
      current.cursor = payload.step_index;
      setProgress((previous) => [...previous, payload]);
    };
    const updateStatus = (payload: { execution_state?: string; attempt_no?: number }) => {
      if (!isCurrent()) return;
      if (payload.execution_state !== undefined) setExecutionState(payload.execution_state);
      if (payload.attempt_no !== undefined) setAttemptNo(payload.attempt_no);
    };
    const finish = (payload: AssistantCompleteEvent) => {
      if (!isCurrent()) return;
      detach();
      setResult(payload);
      setCancelled(payload.status === "cancelled");
      setStopping(false);
      setBusy(false);
      if (operationId === undefined) return;
      const result = payload.status === "partial" ? "partial"
        : payload.status === "failed" || payload.status === "blocked" ? "failed"
        : payload.status === "cancelled" ? "cancelled" : "ok";
      void reportClientEvent({
        eventName: "assistant.complete", result, operationId, turnId: id,
        errorCode: result === "failed" ? "unknown" : undefined,
      });
    };

    const schedule = (work: () => void, delay: number) => {
      if (isCurrent()) current.timer = window.setTimeout(work, delay);
    };
    const poll = async () => {
      if (!isCurrent()) return;
      try {
        const state = await pollTurn(id);
        if (!isCurrent()) return;
        state.public_progress.forEach(pushProgress);
        updateStatus(state);
        if (state.result) {
          finish(state.result);
          return;
        }
      } catch {
        // Temporary network loss must leave the existing turn subscribed.
      }
      schedule(() => void poll(), 2000);
    };

    let failures = 0;
    const connect = () => {
      if (!isCurrent()) return;
      let source: EventSource;
      try {
        source = new EventSource(turnEventsUrl(id, current.cursor));
      } catch {
        void poll();
        return;
      }
      current.source = source;
      const read = <T>(type: string, apply: (payload: T) => void) => {
        source.addEventListener(type, (event) => {
          if (!isCurrent() || current.source !== source) return;
          try {
            apply(JSON.parse((event as MessageEvent<string>).data) as T);
          } catch {
            if (type === "complete") {
              source.close();
              current.source = null;
              void poll();
            }
          }
        });
      };
      read("progress", pushProgress);
      read("status", updateStatus);
      read("complete", finish);
      source.onerror = () => {
        if (!isCurrent() || current.source !== source) return;
        source.close();
        current.source = null;
        failures += 1;
        if (failures >= 3) {
          void poll();
        } else {
          schedule(connect, 800 * 2 ** failures);
        }
      };
    };
    connect();
  }, [detach]);

  const resume = useCallback((id: string) => {
    reset();
    subscribe(id);
  }, [reset, subscribe]);

  const ask = useCallback(async (conversationId: string, message: string, reasoning = false) => {
    reset();
    setBusy(true);
    const previous = submissions.get(conversationId);
    const submission: Submission = previous?.message === message && previous.reasoning === reasoning ? previous : {
      message, reasoning, key: newClientId("turn"), pending: null, stopRequested: false,
    };
    submitting.current = submission;
    submissions.set(conversationId, submission);
    const operationId = submission.key;
    void reportClientEvent({ eventName: "assistant.ask", result: "started", operationId });
    try {
      submission.pending ??= startTurn(conversationId, message, operationId, reasoning);
      const accepted = await submission.pending;
      if (submissions.get(conversationId) === submission) submissions.delete(conversationId);
      void reportClientEvent({
        eventName: "assistant.accepted", result: "accepted", operationId, turnId: accepted.turn_id,
      });
      const ownsSubmission = submitting.current === submission;
      if (ownsSubmission) {
        submitting.current = null;
        subscribe(accepted.turn_id, operationId);
      }
      if (submission.stopRequested) {
        const current = ownsSubmission ? subscription.current : null;
        if (current) current.stopRequested = true;
        try {
          await cancelTurnRequest(accepted.turn_id);
        } catch (cause) {
          if (current && subscription.current === current) {
            current.stopRequested = false;
            setError((cause as Error).message);
            setStopping(false);
          }
        }
      }
      return true;
    } catch (cause) {
      submission.pending = null;
      submission.stopRequested = false;
      if (cause instanceof ApiError && cause.status < 500 && submissions.get(conversationId) === submission) {
        submissions.delete(conversationId);
      }
      if (submitting.current === submission) {
        submitting.current = null;
        setError((cause as Error).message);
        setBusy(false);
        setStopping(false);
      }
      void reportClientEvent({
        eventName: "assistant.ask", result: "failed", operationId,
        errorCode: cause instanceof ApiError ? errorCodeFromStatus(cause.status) : "network",
      });
      return false;
    }
  }, [reset, subscribe]);

  const cancel = useCallback(async () => {
    if (submitting.current) {
      submitting.current.stopRequested = true;
      setStopping(true);
      return;
    }
    const current = subscription.current;
    if (!current || turnId === null || current.stopRequested) return;
    current.stopRequested = true;
    setStopping(true);
    try {
      await cancelTurnRequest(turnId);
      void reportClientEvent({
        eventName: "assistant.stop", result: "cancelled", operationId: turnId, turnId,
      });
    } catch (cause) {
      if (subscription.current !== current) return;
      current.stopRequested = false;
      setError((cause as Error).message);
      setStopping(false);
    }
  }, [turnId]);

  return { progress, result, busy, stopping, error, executionState, attemptNo, turnId, cancelled, ask, cancel, resume };
}
