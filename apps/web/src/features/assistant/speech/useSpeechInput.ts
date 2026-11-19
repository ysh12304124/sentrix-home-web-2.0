import { useCallback, useEffect, useRef, useState } from "react";
import { MAX_RECORDING_SECONDS, BackendSpeechRecognizer, type SpeechRecognizer } from "./recognizer";
import { MicrophoneRecorder, speechUnavailableReason, type SpeechRecorder } from "./recorder";

type Phase = "idle" | "loading" | "permission" | "recording" | "transcribing";

function errorMessage(error: unknown): string {
  if (error instanceof Error || error instanceof DOMException) {
    if (error.name === "NotAllowedError") return "麦克风权限未开启，请在浏览器的网站设置中允许麦克风后重试。";
    if (error.name === "NotFoundError") return "未找到麦克风，请连接麦克风后重试。";
    if (error.name === "NotReadableError") return "无法使用麦克风，请检查是否被其他应用占用。";
    return error.message;
  }
  return "语音输入失败，请重试或使用文字输入。";
}

export function useSpeechInput({ contextKey, enabled, onTranscript }: {
  contextKey: string;
  enabled: boolean;
  onTranscript: (text: string, autoSend: boolean) => Promise<string> | string | void;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const currentPhase = useRef<Phase>("idle");
  const recognizer = useRef<SpeechRecognizer | null>(null);
  const serviceReady = useRef(false);
  const recorder = useRef<SpeechRecorder | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const transcript = useRef(onTranscript);
  useEffect(() => { transcript.current = onTranscript; }, [onTranscript]);
  const unavailable = speechUnavailableReason();

  const transition = useCallback((next: Phase) => {
    currentPhase.current = next;
    setPhase(next);
  }, []);

  const release = useCallback((all = false) => {
    generation.current++;
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    recorder.current?.dispose();
    recorder.current = null;
    if (all || currentPhase.current === "loading" || currentPhase.current === "transcribing") {
      recognizer.current?.dispose();
      recognizer.current = null;
      serviceReady.current = false;
    }
    currentPhase.current = "idle";
  }, []);

  const cancel = useCallback(() => {
    release();
    setPhase("idle");
    setMessage("已取消语音输入。");
    setError(null);
  }, [release]);

  useEffect(() => {
    release();
    setPhase("idle");
    setMessage("");
    setError(null);
    return () => release();
  }, [contextKey, release]);

  useEffect(() => () => release(true), [release]);

  const fail = useCallback((cause: unknown, token: number) => {
    if (token !== generation.current) return;
    release();
    setPhase("idle");
    setMessage("");
    setError(errorMessage(cause));
  }, [release]);

  const stop = useCallback(async (reason: "manual" | "timeout" = "manual") => {
    if (currentPhase.current !== "recording") return;
    const token = generation.current;
    transition("transcribing");
    if (timer.current) clearTimeout(timer.current);
    setMessage("正在本机后端识别…");
    try {
      const audio = await recorder.current!.stop();
      if (token !== generation.current) return;
      recorder.current = null;
      if (audio.samples.length < audio.sampleRate * 0.2) {
        transition("idle");
        throw new Error("录音太短，请再说一次。");
      }
      const text = (await recognizer.current!.transcribe(audio.samples, audio.sampleRate)).trim();
      if (token !== generation.current) return;
      transition("idle");
      if (!text) throw new Error("未识别到语音，请靠近麦克风再试一次。");
      const message = await transcript.current(text, reason === "manual");
      if (token !== generation.current) return;
      setMessage(message || (reason === "timeout" ? "已达到录音时限，请确认文字后发送。" : "识别完成。"));
    } catch (cause) { fail(cause, token); }
  }, [fail, transition]);

  const start = useCallback(async () => {
    if (!enabled || unavailable || currentPhase.current !== "idle") return;
    const token = ++generation.current;
    transition(serviceReady.current ? "permission" : "loading");
    setError(null);
    setMessage(serviceReady.current ? "正在开启麦克风…" : "正在检查本机后端语音服务…");
    try {
      recorder.current = new MicrophoneRecorder();
      recognizer.current ??= new BackendSpeechRecognizer();
      if (!serviceReady.current) await recognizer.current.load((progress) => {
        if (token === generation.current) setMessage(progress);
      });
      if (token !== generation.current) return;
      serviceReady.current = true;
      transition("permission");
      setMessage("请允许浏览器使用麦克风…");
      await recorder.current.start();
      if (token !== generation.current) return;
      transition("recording");
      setMessage(`正在录音，点击“停止并识别”结束（最长 ${MAX_RECORDING_SECONDS} 秒）。`);
      timer.current = setTimeout(() => { void stop("timeout"); }, MAX_RECORDING_SECONDS * 1000);
    } catch (cause) { fail(cause, token); }
  }, [enabled, fail, stop, transition, unavailable]);

  return { phase, message, error, unavailable, active: phase !== "idle", start, stop, cancel };
}
