import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BackendSpeechRecognizer } from "./recognizer";

const fetchMock = vi.fn();
beforeEach(() => { vi.stubGlobal("fetch", fetchMock); fetchMock.mockReset(); });
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it("只检查后端状态，提交 little-endian PCM 和实际采样率", async () => {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ available: true, message: "" })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ text: "你好" })));
  document.cookie = "sentrix_control_csrf=speech-test";
  const recognizer = new BackendSpeechRecognizer();
  await recognizer.load(vi.fn());
  expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/assistant/speech/status");
  expect(await recognizer.transcribe(new Float32Array([0.25, -0.5]), 48000)).toBe("你好");
  const [url, init] = fetchMock.mock.calls[1];
  expect(url).toBe("/api/v1/assistant/speech/transcribe?sample_rate=48000");
  expect(init.method).toBe("POST");
  expect(init.headers.get("x-csrf-token")).toBe("speech-test");
  document.cookie = "sentrix_control_csrf=; Max-Age=0";
  expect(init.credentials).toBe("same-origin");
  expect(init.headers.get("Content-Type")).toBe("application/octet-stream");
  const view = new DataView(init.body);
  expect(view.getFloat32(0, true)).toBe(0.25);
  expect(view.getFloat32(4, true)).toBe(-0.5);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("后端未就绪显示具体原因", async () => {
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ available: false, message: "请准备模型" })));
  await expect(new BackendSpeechRecognizer().load(vi.fn())).rejects.toThrow("请准备模型");
});

it("忙碌和识别失败直接显示后端错误，不自动重试", async () => {
  fetchMock.mockResolvedValue(new Response(JSON.stringify({
    code: "speech_busy", message: "正在识别另一段语音", detail: {},
  }), { status: 429 }));
  await expect(new BackendSpeechRecognizer().transcribe(new Float32Array(3200), 16000))
    .rejects.toThrow("正在识别另一段语音");
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

function abortableFetch() {
  fetchMock.mockImplementation((_url, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener("abort", () => reject(new DOMException("Cancelled", "AbortError")));
  }));
}

it("取消终止 HTTP 请求，不调用后端释放模型接口", async () => {
  abortableFetch();
  const recognizer = new BackendSpeechRecognizer();
  const task = recognizer.transcribe(new Float32Array(3200), 16000);
  const rejection = expect(task).rejects.toMatchObject({ name: "AbortError" });
  recognizer.dispose();
  await rejection;
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("识别超时终止请求并明确提示", async () => {
  vi.useFakeTimers();
  abortableFetch();
  const task = new BackendSpeechRecognizer().transcribe(new Float32Array(3200), 16000);
  const rejection = expect(task).rejects.toThrow("超时");
  await vi.advanceTimersByTimeAsync(120_000);
  await rejection;
});
