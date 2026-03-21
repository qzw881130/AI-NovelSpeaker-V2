# AI-NovelSpeaker-V2

[简体中文](README.md) | [繁體中文](README.zh-TW.md) | [English](README.en-US.md) | [日本語](README.ja-JP.md) | [한국어](README.ko-KR.md)

多小說管理與有聲生成工具（SQLite + 本地檔案儲存 + ComfyUI + LLM）。

## 小說轉有聲小說流程

![Novel to audiobook flow](docs/flow-en.jpg)

## 影片介紹

- B站：https://www.bilibili.com/video/BV136AKzcE2c
- YouTube：https://youtu.be/pVB0qMpFdqg

## 功能概覽

- 小說管理：建立/編輯/刪除、統計、ZIP 打包下載
- 章節管理：章節 CRUD、JSON 轉換、音訊生成、播放/下載
- 任務佇列：JSON 任務、有聲任務、全部終止、刪除非 running 任務
- 提示詞與工作流：系統/使用者模板管理，可複製系統模板
- 系統設定：ComfyUI 位址、LLM 參數、批次字數上限、UI 語言/時區
- 多語介面：`zh-CN` / `zh-TW` / `en-US` / `ja-JP` / `ko-KR`

## 重要路徑

- `app_server.py`：服務入口
- `server/startup.py`：啟動流程
- `server/http_handler.py`：HTTP 路由
- `server/services.py`：核心服務
- `scripts/init_storage.py`：資料庫/目錄初始化
- `prompts/xhz_system_prompt.txt`：系統提示詞檔案
- `workflows/*.json`：系統內建 ComfyUI 工作流檔案
- `debug/qwen3_tts_workflow_debug.json`：ComfyUI 除錯工作流
- `output/`：本地匯出目錄（保留目錄，忽略生成檔）

## 啟動

### 前置需求

- Python 3.10+（建議 3.11/3.12/3.13）
- 可選：ComfyUI（用於音訊生成）

### 取得程式碼

```bash
 git clone git@github.com:qzw881130/AI-NovelSpeaker-V2.git
 # 或者: git clone https://github.com/qzw881130/AI-NovelSpeaker-V2.git
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

雙擊 `start.bat`，或執行：

```bat
start.bat
start.bat --help
start.bat --port=8081
```

## 打包下載規則（Download Bundle）

- ZIP 會先產生在本地 `output/`
- ZIP 命名格式：`{小說英文名}-{YYYY-MM-dd_HHmm}.zip`
- 解壓後不包含 `output/` 目錄層級：
  - `{小說英文名}/audio/*.flac`
  - `{小說英文名}/text/*.txt`
- 檔名格式：`章節編號_章節名`
  - 音訊：`001_第一章_落日.flac`
  - 文字：`001_第一章_落日.txt`

## ComfyUI 依賴（Qwen3 TTS）

第三方節點：

- [AICoderTudou/ComfyUI-TD-Qwen3TTS](https://github.com/AICoderTudou/ComfyUI-TD-Qwen3TTS)
- [jamesWalker55/comfyui-various](https://github.com/jamesWalker55/comfyui-various)
- [Suzie1/ComfyUI_Comfyroll_CustomNodes](https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes)
- [LAOGOU-666/Comfyui-Memory_Cleanup](https://github.com/LAOGOU-666/Comfyui-Memory_Cleanup)

工作流使用模型：

- `Qwen/Qwen3-TTS-12Hz-1.7B-Base`
- `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`

## 頁面入口

- `index.html`：小說管理
- `chapters.html`：章節管理
- `json-tasks.html`：JSON 任務
- `audio-queue.html`：有聲佇列
- `prompts.html`：提示詞管理
- `workflows.html`：工作流管理
- `settings.html`：系統設定
- `novel-capture.html`：小說抓取
