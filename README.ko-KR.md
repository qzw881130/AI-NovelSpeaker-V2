# AI-NovelSpeaker-V2

[简体中文](README.md) | [繁體中文](README.zh-TW.md) | [English](README.en-US.md) | [日本語](README.ja-JP.md) | [한국어](README.ko-KR.md)

다중 소설 관리 + 오디오 생성 도구입니다 (SQLite + 로컬 파일 저장 + ComfyUI + LLM).

## 영상 소개

- Bilibili: https://www.bilibili.com/video/BV1vXP3znEAT
- YouTube: https://youtu.be/FI28PpHUGAY

## 주요 기능

- 소설 관리: 생성/편집/삭제, 통계, ZIP 번들 다운로드
- 챕터 관리: 챕터 CRUD, JSON 변환, 오디오 생성, 재생/다운로드
- 큐 관리: JSON 작업 큐, 오디오 작업 큐, 전체 중지, running 이외 삭제
- 프롬프트/워크플로: 시스템/사용자 템플릿 관리
- 설정: ComfyUI URL, LLM 파라미터, 배치 글자수 제한, UI 언어/시간대
- 다국어 UI: `zh-CN` / `zh-TW` / `en-US` / `ja-JP` / `ko-KR`

## 주요 경로

- `app_server.py`: 서버 진입점
- `server/startup.py`: 시작 프로세스
- `server/http_handler.py`: HTTP 라우팅
- `server/services.py`: 핵심 서비스
- `scripts/init_storage.py`: DB/디렉터리 초기화
- `prompts/xhz_system_prompt.txt`: 시스템 프롬프트 파일
- `workflows/*.json`: 내장 ComfyUI 워크플로 파일
- `debug/qwen3_tts_workflow_debug.json`: ComfyUI 디버그 워크플로
- `output/`: 로컬 내보내기 디렉터리 (디렉터리는 추적, 생성 파일은 무시)

## 실행 방법

### 사전 요구사항

- Python 3.10+ (권장 3.11/3.12/3.13)
- 선택: ComfyUI (오디오 생성용)

### 저장소 클론

```bash
 git clone git@github.com:qzw881130/AI-NovelSpeaker-V2.git
 # 또는: git clone https://github.com/qzw881130/AI-NovelSpeaker-V2.git
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

`start.bat` 더블클릭 또는 실행:

```bat
start.bat
start.bat --help
start.bat --port=8081
```

## 번들 다운로드 규칙 (Download Bundle)

- ZIP 파일은 로컬 `output/`에 생성됩니다
- ZIP 파일명: `{영문소설명}-{YYYY-MM-dd_HHmm}.zip`
- 압축 해제 구조에는 `output/` 경로가 포함되지 않습니다:
  - `{영문소설명}/audio/*.flac`
  - `{영문소설명}/text/*.txt`
- 파일명 규칙: `챕터번호_챕터제목`
  - 오디오: `001_첫장_석양.flac`
  - 텍스트: `001_첫장_석양.txt`

## ComfyUI 의존성 (Qwen3 TTS)

필요한 서드파티 노드:

- [AICoderTudou/ComfyUI-TD-Qwen3TTS](https://github.com/AICoderTudou/ComfyUI-TD-Qwen3TTS)
- [jamesWalker55/comfyui-various](https://github.com/jamesWalker55/comfyui-various)
- [Suzie1/ComfyUI_Comfyroll_CustomNodes](https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes)
- [LAOGOU-666/Comfyui-Memory_Cleanup](https://github.com/LAOGOU-666/Comfyui-Memory_Cleanup)

사용 모델:

- `Qwen/Qwen3-TTS-12Hz-1.7B-Base`
- `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`

## 페이지

- `index.html`: 소설 관리
- `chapters.html`: 챕터 관리
- `json-tasks.html`: JSON 작업
- `audio-queue.html`: 오디오 큐
- `prompts.html`: 프롬프트 관리
- `workflows.html`: 워크플로 관리
- `settings.html`: 설정
- `novel-capture.html`: 소설 캡처
