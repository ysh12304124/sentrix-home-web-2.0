import { getSpeechStatus, transcribeSpeech } from "../../../shared/api/client";

/** Browser captures audio; the local API owns the shared, startup-loaded model. */
export interface SpeechRecognizer {
  load(onProgress: (message: string) => void): Promise<void>;
  transcribe(samples: Float32Array, sampleRate: number): Promise<string>;
  dispose(): void;
}

export const MAX_RECORDING_SECONDS = 60;

export class BackendSpeechRecognizer implements SpeechRecognizer {
  private controller: AbortController | null = null;

  async load(onProgress: (message: string) => void): Promise<void> {
    onProgress("正在检查本机后端语音服务…");
    await this.request(async (signal) => {
      const status = await getSpeechStatus(signal);
      if (!status.available) throw new Error(status.message || "本机后端语音模型未就绪，请检查后端日志。");
    }, 10_000);
  }

  async transcribe(samples: Float32Array, sampleRate: number): Promise<string> {
    // Explicit little endian on the wire, independent of browser architecture.
    const pcm = new ArrayBuffer(samples.length * 4);
    const view = new DataView(pcm);
    samples.forEach((sample, index) => view.setFloat32(index * 4, sample, true));
    return this.request(async (signal) => {
      const result = await transcribeSpeech(pcm, sampleRate, signal);
      if (typeof result.text !== "string") throw new Error("后端返回了无法读取的识别结果。");
      return result.text;
    }, 120_000);
  }

  private async request<T>(run: (signal: AbortSignal) => Promise<T>, timeout: number): Promise<T> {
    this.dispose();
    const controller = new AbortController();
    this.controller = controller;
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeout);
    try {
      return await run(controller.signal);
    } catch (cause) {
      if (timedOut) throw new Error("本机后端语音处理超时，请重试或使用文字输入。");
      throw cause;
    } finally {
      clearTimeout(timer);
      if (this.controller === controller) this.controller = null;
    }
  }

  dispose(): void {
    this.controller?.abort();
    this.controller = null;
  }
}
