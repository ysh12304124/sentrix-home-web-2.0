import { MAX_RECORDING_SECONDS } from "./recognizer";

export interface SpeechRecorder {
  start(): Promise<void>;
  stop(): Promise<{ samples: Float32Array; sampleRate: number }>;
  dispose(): void;
}

export function speechUnavailableReason(): string | null {
  if (!globalThis.isSecureContext) return "语音输入需要 HTTPS 或 localhost，请使用安全地址打开。";
  if (!navigator.mediaDevices?.getUserMedia || !globalThis.AudioContext || !globalThis.AudioWorkletNode) {
    return "当前浏览器不支持语音录音，请使用新版 Safari、Chrome 或 Edge。";
  }
  return null;
}

/** Owns all microphone resources, including permission requests that resolve after cancellation. */
export class MicrophoneRecorder implements SpeechRecorder {
  private context: AudioContext;
  private resumed: Promise<void>;
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private node: AudioWorkletNode | null = null;
  private disposed = false;
  private chunks: Float32Array[] = [];
  private length = 0;
  private stopped: (() => void) | null = null;

  constructor() {
    // Must resume during the click gesture, before service checks/permission awaits (Safari).
    this.context = new AudioContext();
    this.resumed = this.context.resume();
    void this.resumed.catch(() => {});
  }

  async start(): Promise<void> {
    await this.resumed;
    if (this.disposed) throw new DOMException("Cancelled", "AbortError");
    const stream = await navigator.mediaDevices.getUserMedia({ audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true,
    } });
    if (this.disposed) {
      stream.getTracks().forEach((track) => track.stop());
      throw new DOMException("Cancelled", "AbortError");
    }
    this.stream = stream;
    await this.context.audioWorklet.addModule("/speech/capture-worklet.js");
    if (this.disposed) throw new DOMException("Cancelled", "AbortError");
    this.node = new AudioWorkletNode(this.context, "sentrix-speech-capture");
    this.node.port.onmessage = ({ data }) => {
      if (data.type === "stopped") { this.stopped?.(); return; }
      if (this.disposed || data.type !== "samples") return;
      const remaining = MAX_RECORDING_SECONDS * this.context.sampleRate - this.length;
      if (remaining <= 0) return;
      const samples = (data.samples as Float32Array).subarray(0, remaining);
      this.chunks.push(samples);
      this.length += samples.length;
    };
    this.source = this.context.createMediaStreamSource(stream);
    this.source.connect(this.node);
    this.node.connect(this.context.destination);
  }

  async stop(): Promise<{ samples: Float32Array; sampleRate: number }> {
    if (this.disposed || !this.node) throw new Error("录音尚未开始。请重试。");
    this.stream?.getTracks().forEach((track) => track.stop());
    try {
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("录音结束失败，请重试。")), 1500);
        this.stopped = () => { clearTimeout(timer); resolve(); };
        this.node!.port.postMessage("stop");
      });
      const samples = new Float32Array(this.length);
      let offset = 0;
      for (const chunk of this.chunks) { samples.set(chunk, offset); offset += chunk.length; }
      return { samples, sampleRate: this.context.sampleRate };
    } finally { this.dispose(); }
  }

  dispose(): void {
    this.disposed = true;
    this.stopped?.();
    this.stopped = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.source?.disconnect();
    this.node?.disconnect();
    this.node?.port.close();
    this.chunks = [];
    this.length = 0;
    if (this.context.state !== "closed") void this.context.close().catch(() => {});
  }
}
