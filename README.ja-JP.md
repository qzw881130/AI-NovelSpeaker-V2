# AI-NovelSpeaker-V2

[简体中文](README.md) | [繁體中文](README.zh-TW.md) | [English](README.en-US.md) | [日本語](README.ja-JP.md) | [한국어](README.ko-KR.md)

複数小説管理 + 音声生成ツール（SQLite + ローカルファイル保存 + ComfyUI + LLM）。

## 小説を音声化する流れ

![Novel to audiobook flow](docs/flow-en.jpg)

## 動画紹介

- Bilibili：https://www.bilibili.com/video/BV136AKzcE2c
- YouTube：https://youtu.be/pVB0qMpFdqg

## 機能

- 小説管理：作成/編集/削除、統計、ZIP バンドル出力
- 章管理：章 CRUD、JSON 変換、音声生成、再生/ダウンロード
- キュー管理：JSON タスク、音声タスク、一括停止、running 以外の削除
- プロンプト/ワークフロー管理：システム/ユーザー テンプレート
- 設定：ComfyUI URL、LLM パラメータ、バッチ文字数上限、UI 言語/タイムゾーン
- 多言語 UI：`zh-CN` / `zh-TW` / `en-US` / `ja-JP` / `ko-KR`

## 主なパス

- `app_server.py`：サーバー起動
- `server/startup.py`：起動処理
- `server/http_handler.py`：HTTP ルーティング
- `server/services.py`：コアサービス
- `scripts/init_storage.py`：DB/ディレクトリ初期化
- `prompts/xhz_system_prompt.txt`：システムプロンプト
- `workflows/*.json`：内蔵 ComfyUI ワークフローファイル
- `debug/qwen3_tts_workflow_debug.json`：ComfyUI デバッグ用ワークフロー
- `output/`：ローカル出力ディレクトリ（ディレクトリは追跡、生成物は無視）

## 起動

### 前提

- Python 3.10+（推奨 3.11/3.12/3.13）
- 任意：ComfyUI（音声生成に使用）

### リポジトリ取得

```bash
 git clone git@github.com:qzw881130/AI-NovelSpeaker-V2.git
 # または: git clone https://github.com/qzw881130/AI-NovelSpeaker-V2.git
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

`start.bat` をダブルクリック、または実行：

```bat
start.bat
start.bat --help
start.bat --port=8081
```

## バンドル出力ルール（Download Bundle）

- ZIP はローカル `output/` に生成
- ZIP 名：`{英語ディレクトリ名}-{YYYY-MM-dd_HHmm}.zip`
- 展開後は `output/` を含まない構成：
  - `{英語ディレクトリ名}/audio/*.flac`
  - `{英語ディレクトリ名}/text/*.txt`
- ファイル名：`章番号_章タイトル`
  - 音声：`001_第一章_落日.flac`
  - テキスト：`001_第一章_落日.txt`

## ComfyUI 依存（Qwen3 TTS）

必要なサードパーティノード：

- [AICoderTudou/ComfyUI-TD-Qwen3TTS](https://github.com/AICoderTudou/ComfyUI-TD-Qwen3TTS)
- [jamesWalker55/comfyui-various](https://github.com/jamesWalker55/comfyui-various)
- [Suzie1/ComfyUI_Comfyroll_CustomNodes](https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes)
- [LAOGOU-666/Comfyui-Memory_Cleanup](https://github.com/LAOGOU-666/Comfyui-Memory_Cleanup)

利用モデル：

- `Qwen/Qwen3-TTS-12Hz-1.7B-Base`
- `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`

## ページ

- `index.html`：小説管理
- `chapters.html`：章管理
- `json-tasks.html`：JSON タスク
- `audio-queue.html`：音声キュー
- `prompts.html`：プロンプト管理
- `workflows.html`：ワークフロー管理
- `settings.html`：設定
- `novel-capture.html`：小説キャプチャ
