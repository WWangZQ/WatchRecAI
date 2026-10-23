# Third-party components

The Next desktop uses Tauri, Rust and a TypeScript/Vite frontend. Dependency versions are recorded in `watchrec-desktop/Cargo.lock` and `watchrec-desktop/app/package-lock.json`. Upstream license texts apply to their respective components:

- Tauri and plugins: https://github.com/tauri-apps/tauri and https://github.com/tauri-apps/plugins-workspace (MIT / Apache-2.0).
- CPAL: https://github.com/RustAudio/cpal (Apache-2.0); Rubato: https://github.com/HEnquist/rubato (MIT); Symphonia: https://github.com/pdeljanov/Symphonia (MPL-2.0).
- Vite: https://github.com/vitejs/vite (MIT); TypeScript: https://github.com/microsoft/TypeScript (Apache-2.0).
- The separately installed Python runtime and packages retain their upstream licenses: Python, requests, PyTorch, torchaudio, FunASR, ModelScope, SoundFile and tzdata.
- SenseVoiceSmall and FSMN VAD download from ModelScope on first transcription. The app/source archive contains no model weights; the model repositories publish their usage terms.
- FFmpeg/ffprobe are separately installed programs. Their licensing depends on the installed build; consult https://ffmpeg.org/legal.html and the binary provider's source/build recipes before redistributing them.
- AndroidX, Kotlin and Android tools retain their upstream licenses. The watch/VPS source remains the configurable public implementation.

The previous v1.1.0-preview Python Windows bundle has its own collected notices inside that release. The current source migration does not repackage that runtime.

This document does not assign a new license to original WatchRec source. The original repository did not include a project license; the maintainer should select one before a formal release.
