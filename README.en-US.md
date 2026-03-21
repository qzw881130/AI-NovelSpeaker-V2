# AI-NovelSpeaker-V2

[简体中文](README.md) | [繁體中文](README.zh-TW.md) | [English](README.en-US.md) | [日本語](README.ja-JP.md) | [한국어](README.ko-KR.md)

Multi-novel management and audio generation toolkit (SQLite + local file storage + ComfyUI + LLM).

## Novel To Audiobook Flow

![Novel to audiobook flow](docs/flow-en.jpg)

## Video Introduction

- Bilibili: https://www.bilibili.com/video/BV136AKzcE2c
- YouTube: https://youtu.be/pVB0qMpFdqg

## Features

- Novel management: create/edit/delete, statistics, ZIP bundle export
- Chapter management: chapter CRUD, JSON conversion, audio generation, playback/download
- Queues: JSON task queue, audio task queue, cancel-all, delete non-running audio tasks
- Prompts & workflows: system/user template management, copy system templates into user templates
- Settings: ComfyUI URL, LLM parameters, batch character limit, UI language/timezone
- Multilingual UI: `zh-CN` / `zh-TW` / `en-US` / `ja-JP` / `ko-KR`

## Important Paths

- `app_server.py`: app entry
- `server/startup.py`: startup flow
- `server/http_handler.py`: HTTP routing
- `server/services.py`: core services
- `scripts/init_storage.py`: DB/folder initialization
- `prompts/xhz_system_prompt.txt`: system prompt file
- `workflows/*.json`: built-in ComfyUI workflow files
- `debug/qwen3_tts_workflow_debug.json`: ComfyUI debug workflow
- `output/`: local export directory (directory is tracked, generated files are ignored)

## Startup

### Prerequisites

- Python 3.10+ (3.11/3.12/3.13 recommended)
- Optional: ComfyUI (for audio generation)

### Clone Repository

```bash
 git clone git@github.com:qzw881130/AI-NovelSpeaker-V2.git
 # or: git clone https://github.com/qzw881130/AI-NovelSpeaker-V2.git
 cd AI-NovelSpeaker-V2
```

### macOS / Linux

```bash
chmod +x start.sh
./start.sh
```

```bash
./start.sh --help
./start.sh --port=8081
```

### Windows

Double-click `start.bat`, or run:

```bat
start.bat
start.bat --help
start.bat --port=8081
```

## Bundle Export Rules (Download Bundle)

- ZIP files are generated in local `output/`
- ZIP filename format: `{english_dir}-{YYYY-MM-dd_HHmm}.zip`
- Extracted structure does **not** include the `output/` prefix:
  - `{english_dir}/audio/*.flac`
  - `{english_dir}/text/*.txt`
- File naming format: `chapterNo_title`
  - Audio: `001_Chapter_One_Sunset.flac`
  - Text: `001_Chapter_One_Sunset.txt`

## ComfyUI Dependencies (Qwen3 TTS)

Required third-party nodes:

- [AICoderTudou/ComfyUI-TD-Qwen3TTS](https://github.com/AICoderTudou/ComfyUI-TD-Qwen3TTS)
- [jamesWalker55/comfyui-various](https://github.com/jamesWalker55/comfyui-various)
- [Suzie1/ComfyUI_Comfyroll_CustomNodes](https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes)
- [LAOGOU-666/Comfyui-Memory_Cleanup](https://github.com/LAOGOU-666/Comfyui-Memory_Cleanup)

TTS models used in workflow:

- `Qwen/Qwen3-TTS-12Hz-1.7B-Base`
- `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`

## Pages

- `index.html`: novels
- `chapters.html`: chapters
- `json-tasks.html`: JSON tasks
- `audio-queue.html`: audio queue
- `prompts.html`: prompts
- `workflows.html`: workflows
- `settings.html`: settings
- `novel-capture.html`: novel capture
