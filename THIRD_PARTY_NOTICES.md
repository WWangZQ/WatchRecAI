# Third-party components

The Windows preview bundles a Python interpreter and third-party libraries. Collected license and notice files are in `third-party-licenses/` next to the executable. The build environment package versions are listed there as well; some are build tools rather than runtime dependencies.

- Python: <https://www.python.org/psf/license/>
- PyTorch and torchaudio: <https://github.com/pytorch/pytorch> and <https://github.com/pytorch/audio>
- FunASR: <https://github.com/modelscope/FunASR>
- ModelScope: <https://github.com/modelscope/modelscope>
- SenseVoiceSmall and FSMN VAD models download on first transcription; they are not inside the executable. Model usage terms are published by their respective ModelScope repositories.
- FFmpeg and ffprobe: <https://ffmpeg.org/>. Windows build origin: <https://www.gyan.dev/ffmpeg/builds/>. Build options and exact version are recorded in `third-party-licenses/ffmpeg-version.txt`; see that provider for the corresponding source and build recipes. FFmpeg has its own license and is a separate program invoked by WatchRec.
- AndroidX, Kotlin and Android tools retain their upstream licenses. Android build tools are not part of the source or VPS archives.

This file describes third-party components. It does not assign a new license to the original WatchRec source. The original repository did not include a project license file in the snapshot used for this preview; the maintainer should select the project's license before a formal release.
