# AI-NovelSpeaker-V2

[简体中文](README.md) | [繁體中文](README.zh-TW.md) | [English](README.en-US.md) | [日本語](README.ja-JP.md) | [한국어](README.ko-KR.md)

複数小説管理 + 音声生成ツール（SQLite + ローカルファイル保存 + ComfyUI + LLM）。

## 小説を音声化する流れ

![Novel to audiobook flow](docs/flow-en.jpg)

## 動画紹介

- Bilibili：https://www.bilibili.com/video/BV136AKzcE2c
- YouTube：https://youtu.be/pVB0qMpFdqg

## 機能

- 小説管理：小説の作成/編集/削除、プロジェクト統計、自動更新、実際の ZIP バンドル出力、小説単位のプロンプト/ワークフロー紐付けに対応
- 章管理：章 CRUD、本文の表示/コピー、章検索、AI→JSON、JSON の表示/編集/検索置換、役割表示、台詞プレビューと生成、章音声の再生/ダウンロード/結合
- 台詞音声：1行単位生成、まとめて生成して台詞タスクキューへ投入、即時または指定時刻実行に対応；タスクページでは自動更新、詳細、再生、削除、pending 件数表示をサポート
- 役割ライブラリ：役割管理、レベル絞り込み、章内役割比較、役割ライブラリへの追加/置換、サンプル音声のアップロード/生成、音声テキスト抽出に対応
- タスクキュー：JSON タスクキューと台詞音声タスクキューを提供し、状態表示、小説ごとの絞り込み、自動更新をサポート；音声結合前には未生成台詞数を案内
- プロンプトとワークフロー：システム/ユーザーテンプレート管理、ユーザーテンプレートへのコピー対応；音声テキスト抽出、台詞音声生成、サンプル音声生成などの ComfyUI ワークフローを内蔵
- システム設定：ComfyUI URL、LLM パラメータ、Proxy、バッチ文字数、台詞キュー実行方式、UI 言語とタイムゾーン設定
- 多言語 UI：`zh-CN` / `zh-TW` / `en-US` / `ja-JP` / `ko-KR` をサポート
- 音声体験：章音声、台詞音声、結合音声でシークバーによる早送りに対応
- デバッグとドキュメント：ComfyUI debug ワークフロー、デバッグ用サンプル資産、「小説を音声化する流れ」図を提供

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
