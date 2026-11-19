# Speech capture

The browser AudioWorklet is Sentrix code. No ASR model or WASM runtime is served here.

Recognition runs in the local Python API with sherpa-onnx 1.12.40 (Apache-2.0).
The original sherpa-onnx license is retained in LICENSE-sherpa-onnx.txt.
Source: https://github.com/k2-fsa/sherpa-onnx/tree/v1.12.40

SenseVoice-Small INT8 weights are installed separately under SENTRIX_DATA_DIR.
Source and checksums: scripts/deploy/sensevoice-model.lock.json.
The preparation script preserves the official model bundle's LICENSE and README.
Model license: https://github.com/FunAudioLLM/SenseVoice#license
