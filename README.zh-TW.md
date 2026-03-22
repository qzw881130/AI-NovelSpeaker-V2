# AI-NovelSpeaker-V2

[简体中文](README.md) | [繁體中文](README.zh-TW.md) | [English](README.en-US.md) | [日本語](README.ja-JP.md) | [한국어](README.ko-KR.md)

多小說管理與有聲生成工具（SQLite + 本地檔案儲存 + ComfyUI + LLM）。

## 小說轉有聲小說流程

![Novel to audiobook flow](docs/flow-en.jpg)

## 影片介紹

- B站：https://www.bilibili.com/video/BV136AKzcE2c
- YouTube：https://youtu.be/pVB0qMpFdqg

## 功能概覽

- 小說管理：建立/編輯/刪除小說，專案統計，自動刷新，打包下載真實 ZIP，支援小說級提示詞與工作流綁定
- 章節管理：章節 CRUD，正文查看與複製，章節搜尋，AI 轉 JSON，JSON 查看/編輯/查找替換，角色查看，台詞預覽與生成，章節音訊播放/下載/合併
- 台詞音訊：支援單條生成、批量生成並進入台詞任務佇列，可立即或定時執行；任務頁支援自動刷新、詳情、播放、刪除與 pending 數量顯示
- 角色庫：角色管理、等級篩選、章節角色對比，支援加入/替換角色庫、示例音訊上傳/生成、聲音文本提取
- 任務佇列：JSON 任務佇列與台詞音訊任務佇列，支援狀態查看、按小說篩選、自動刷新；音訊合併前會提示未生成台詞數量
- 提示詞與工作流：系統/使用者模板管理，可複製為使用者模板；內建提取聲音文本、生成台詞音訊、生成示例音訊等 ComfyUI 工作流
- 系統設定：ComfyUI 位址、LLM 參數、Proxy、批量文本字數、台詞佇列執行方式、UI 語言與時區設定
- 多語介面：支援 `zh-CN` / `zh-TW` / `en-US` / `ja-JP` / `ko-KR`
- 音訊體驗：章節音訊、台詞音訊、合併音訊支援拖動進度條快進
- 除錯與文件：提供 ComfyUI debug 工作流、除錯樣例資源與「小說轉有聲小說」流程圖

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
