import { afterEach, expect, it, vi } from "vitest";
import { MicrophoneRecorder } from "./recorder";

afterEach(() => vi.unstubAllGlobals());

it("用户取消后才批准麦克风，也立即关闭迟到的音轨", async () => {
  let allow!: (stream: MediaStream) => void;
  const permission = new Promise<MediaStream>((resolve) => { allow = resolve; });
  const stop = vi.fn();
  const close = vi.fn().mockResolvedValue(undefined);
  const addModule = vi.fn();
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: () => permission } });
  vi.stubGlobal("AudioContext", class {
    state = "running"; sampleRate = 48000;
    resume = vi.fn().mockResolvedValue(undefined); close = close;
    audioWorklet = { addModule };
  });
  const recorder = new MicrophoneRecorder();
  const started = recorder.start();
  await Promise.resolve();
  recorder.dispose();
  allow({ getTracks: () => [{ stop }] } as unknown as MediaStream);
  await expect(started).rejects.toMatchObject({ name: "AbortError" });
  expect(stop).toHaveBeenCalledTimes(1);
  expect(close).toHaveBeenCalledTimes(1);
  expect(addModule).not.toHaveBeenCalled();
});

it("停止时保留最后一段 PCM 和实际采样率，音轨、节点与音频上下文均释放", async () => {
  const stopTrack = vi.fn();
  const disconnect = vi.fn();
  const close = vi.fn().mockResolvedValue(undefined);
  const closePort = vi.fn();
  let onmessage: ((event: { data: object }) => void) | null = null;
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop: stopTrack }] }) } });
  vi.stubGlobal("AudioContext", class {
    state = "running"; sampleRate = 48000; destination = {};
    resume = vi.fn().mockResolvedValue(undefined); close = close;
    audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
    createMediaStreamSource = () => ({ connect: vi.fn(), disconnect });
  });
  vi.stubGlobal("AudioWorkletNode", class {
    connect = vi.fn(); disconnect = disconnect;
    port = {
      set onmessage(callback: typeof onmessage) { onmessage = callback; },
      close: closePort,
      postMessage: () => {
        onmessage!({ data: { type: "samples", samples: new Float32Array([0.25, -0.25]) } });
        onmessage!({ data: { type: "stopped" } });
      },
    };
  });
  const recorder = new MicrophoneRecorder();
  await recorder.start();
  const audio = await recorder.stop();
  expect(audio.sampleRate).toBe(48000);
  expect([...audio.samples]).toEqual([0.25, -0.25]);
  expect(stopTrack).toHaveBeenCalled();
  expect(disconnect).toHaveBeenCalledTimes(2);
  expect(close).toHaveBeenCalledTimes(1);
  expect(closePort).toHaveBeenCalledTimes(1);
});
