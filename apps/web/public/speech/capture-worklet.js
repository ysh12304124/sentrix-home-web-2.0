/* global AudioWorkletProcessor, registerProcessor */
class SpeechCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(2048);
    this.offset = 0;
    this.stopped = false;
    this.port.onmessage = ({ data }) => {
      if (data === "stop") {
        this.flush();
        this.stopped = true;
        this.port.postMessage({ type: "stopped" });
      }
    };
  }
  flush() {
    if (this.offset) {
      const samples = this.buffer.slice(0, this.offset);
      this.port.postMessage({ type: "samples", samples }, [samples.buffer]);
      this.offset = 0;
    }
  }
  process(inputs) {
    if (this.stopped) return false;
    const channels = inputs[0];
    if (!channels?.length) return true;
    for (let i = 0; i < channels[0].length; i++) {
      let value = 0;
      for (const channel of channels) value += channel[i];
      this.buffer[this.offset++] = value / channels.length;
      if (this.offset === this.buffer.length) this.flush();
    }
    // Outputs stay silent; the microphone is never played through speakers.
    return true;
  }
}
registerProcessor("sentrix-speech-capture", SpeechCapture);
