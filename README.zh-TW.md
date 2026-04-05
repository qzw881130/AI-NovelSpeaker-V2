# AI-NovelSpeaker-V2

[简体中文](README.md) | [繁體中文](README.zh-TW.md) | [English](README.en-US.md) | [日本語](README.ja-JP.md) | [한국어](README.ko-KR.md)

多小說管理與有聲生成工具（SQLite + 本地檔案儲存 + ComfyUI + LLM）。

## 小說轉有聲小說流程

![Novel to audiobook flow](docs/flow-en.jpg)

## 影片介紹

- B站：https://www.bilibili.com/video/BV136AKzcE2c
- YouTube：https://youtu.be/pVB0qMpFdqg

## 功能概覽

- 小說管理：建立/編輯/刪除小說、專案統計、自動刷新、打包下載真實 ZIP、支援小說級提示詞與工作流綁定，並可刷新與快取小說總音訊時長
- 章節管理：章節 CRUD、正文查看與複製、章節搜尋、AI 轉 JSON、JSON 查看/編輯/查找替換、角色查看、台詞預覽與生成、章節音訊播放/下載/合併
- 台詞音訊：支援單條生成、批量生成並進入台詞任務佇列，可立即或定時執行；任務頁支援自動刷新、詳情、播放、刪除與 pending/running 數量顯示
- 角色庫：角色管理、等級篩選、章節角色對比，支援加入/替換角色庫、示例音訊上傳/生成、聲音文本提取
- 任務佇列：JSON 任務佇列與台詞音訊任務佇列，支援狀態查看、按小說篩選、自動刷新；音訊合併前會提示未生成台詞數量
- 提示詞與工作流：系統/使用者模板管理，可複製為使用者模板；工作流依三種類別分組查看，支援輸入輸出節點配置、日誌開關、系統/使用者工作流切換
- 工作流日誌：記錄工作流呼叫時間、工作流類別、工作流名稱、最終提交給 ComfyUI 的 JSON 與錯誤日誌，支援清空全部日誌
- 小說下載：提供章回音訊下載頁，展示章回編號、標題、字數、音訊時長、音訊大小與下載連結
- 系統設定：ComfyUI 位址、LLM 參數、Proxy、批量文本字數、台詞佇列執行方式、UI 語言與時區設定
- 多語介面：支援 `zh-CN` / `zh-TW` / `en-US` / `ja-JP` / `ko-KR`
- 音訊體驗：章節音訊、台詞音訊、合併音訊支援拖動進度條快進
- 除錯與文件：提供 ComfyUI debug 工作流、低顯存工作流樣例、調試音訊樣例與「小說轉有聲小說」流程圖

## 重要路徑

- `app_server.py`：服務入口
- `server/startup.py`：啟動流程
- `server/http_handler.py`：HTTP 路由
- `server/services.py`：核心服務
- `scripts/init_storage.py`：資料庫/目錄初始化
- `prompts/xhz_system_prompt.txt`：系統提示詞檔案
- `workflows/*.json`：系統內建 ComfyUI 工作流檔案
- `debug/`：ComfyUI 調試工作流截圖、JSON 與調試樣例
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

## ComfyUI 依賴說明

### 調試工作流與低顯存樣例

- `debug/qwen3-tts-generate-character-samples-no-llm.json`：Qwen3 TTS 角色示例音訊生成
- `debug/fishaudio-s2-tts-generate-dialogue-audio.json`：FishAudio S2 台詞音訊生成
- `debug/extract-voice-text.json`：Whisper 音訊轉文字
- `debug/line_audio_workflow_qwen3-tts.png`：Qwen3 TTS 低顯存台詞音訊工作流截圖
- `debug/voice_transcribe_workflow_qwen3-asr.png`：Qwen3-ASR 低顯存提取文本工作流截圖
- `debug/1-旁白.flac`：FishAudio S2 調試工作流使用的參考音訊樣例

### 工作流配置說明

- 目前支援三類工作流：
  - `生成示例音訊`
  - `生成台詞音訊`
  - `提取聲音文本`
- 每個工作流都可以配置輸入輸出節點映射：
  - 生成示例音訊：輸入 `音色描述`、`台詞`；輸出 `生成的聲音檔案`
  - 生成台詞音訊：輸入 `參考音訊檔案`、`台詞`、`參考音訊的文本`；輸出 `生成的聲音檔案`
  - 提取聲音文本：輸入 `音訊檔案`；輸出 `提取的文本`
- 系統工作流使用預設映射，不允許編輯；複製為使用者工作流後，會連同輸入輸出配置一起複製。
- 工作流日誌預設開啟。開啟時，系統會記錄「最終提交給 ComfyUI 的工作流 JSON」；關閉時，不再記錄該工作流的執行日誌。
- 工作流 JSON 同時支援 ComfyUI API prompt 格式與圖編輯器匯出格式（`nodes/links`）。

### 需要的第三方節點（僅列第三方）

| 插件（第三方） | 倉庫 | 本專案工作流使用到的節點 |
| --- | --- | --- |
| Qwen3-TTS ComfyUI | [firadiskin/qwen3-tts-comfyui](https://github.com/firadiskin/qwen3-tts-comfyui) | `FB_Qwen3TTSVoiceDesign`、`FB_Qwen3TTSVoiceClone` |
| ComfyUI-FishAudioS2 | [Saganaki22/ComfyUI-FishAudioS2](https://github.com/Saganaki22/ComfyUI-FishAudioS2) | `FishS2VoiceCloneTTS` |
| ComfyUI_Comfyroll_CustomNodes | [Suzie1/ComfyUI_Comfyroll_CustomNodes](https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes) | `CR Text` |
| ComfyUI-MTB | [melMass/comfy_mtb](https://github.com/melMass/comfy_mtb) | `Load Whisper (mtb)`、`Audio To Text (mtb)` |
| ComfyUI-Custom-Scripts | [pythongosssss/ComfyUI-Custom-Scripts](https://github.com/pythongosssss/ComfyUI-Custom-Scripts) | `ShowText\|pysssss` |
| Comfyui_SynVow_Qwen3ASR | [SynVow/Comfyui_SynVow_Qwen3ASR](https://github.com/SynVow/Comfyui_SynVow_Qwen3ASR) | `Qwen3ASRLoader`、`Qwen3ASRTranscribe` |

說明：`LoadAudio`、`SaveAudio`、`Text Multiline` 等節點來自 ComfyUI Core，不屬於第三方節點。

### 需要的模型（依目前工作流）

- Qwen3 TTS：
  - `Qwen/Qwen3-TTS-12Hz-1.7B-Base`
  - `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`
  - `Qwen3-TTS-1.7B`（低顯存台詞音訊工作流）
- Fish Audio S2：
  - `s2-pro-fp8`
- Whisper 轉寫：
  - `large-v3`
- Qwen3 ASR：
  - `Qwen3-ASR-1.7B`

### 目前內建工作流與依賴對應關係

| 工作流檔案 | 作用 | 關鍵第三方節點 | 需要的模型 |
| --- | --- | --- | --- |
| `workflows/voice_sample_workflow.json` | 生成角色示例音訊 | `FB_Qwen3TTSVoiceDesign`、`CR Prompt Text` | `Qwen/Qwen3-TTS-12Hz-1.7B-Base`、`Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` |
| `workflows/line_audio_workflow.json` | 生成台詞音訊 | `FishS2VoiceCloneTTS`、`CR Prompt Text` | `s2-pro-fp8` |
| `workflows/voice_transcribe_workflow.json` | 從參考音訊提取文本 | `Load Whisper (mtb)`、`Audio To Text (mtb)`、`ShowText\|pysssss` | [QwenLM/Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) |
| `workflows/line_audio_workflow_qwen3-tts.json` | 低顯存生成台詞音訊 | `FB_Qwen3TTSVoiceClone`、`CR Prompt Text` | `Qwen3-TTS-1.7B` |
| `workflows/voice_transcribe_workflow_qwen3-asr.json` | 低顯存提取聲音文本 | `Qwen3ASRLoader`、`Qwen3ASRTranscribe`、`ShowText\|pysssss` | `Qwen3-ASR-1.7B` |

## 頁面入口

- `index.html`：小說管理
- `chapters.html`：章節管理
- `json-tasks.html`：JSON 任務
- `audio-queue.html`：有聲佇列
- `line-audio-tasks.html`：台詞音訊任務佇列
- `roles.html`：角色庫
- `novel-download.html`：小說下載
- `prompts.html`：提示詞管理
- `workflows.html`：工作流管理
- `workflow-logs.html`：工作流日誌
- `settings.html`：系統設定
- `novel-capture.html`：小說抓取
