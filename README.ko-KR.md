# AI-NovelSpeaker-V2

[简体中文](README.md) | [繁體中文](README.zh-TW.md) | [English](README.en-US.md) | [日本語](README.ja-JP.md) | [한국어](README.ko-KR.md)

다중 소설 관리 + 오디오 생성 도구입니다 (SQLite + 로컬 파일 저장 + ComfyUI + LLM).

## 소설을 오디오북으로 만드는 흐름

![Novel to audiobook flow](docs/flow-en.jpg)

## 영상 소개

- Bilibili: https://www.bilibili.com/video/BV136AKzcE2c
- YouTube: https://youtu.be/pVB0qMpFdqg

## 후원

이 프로젝트가 도움이 되었다면 지속적인 개발을 위해 후원을 환영합니다.

| Alipay | WeChat Pay |
| --- | --- |
| <img src="docs/alipay.jpg" alt="Alipay" width="220" /> | <img src="docs/wechat-pay.jpg" alt="WeChat Pay" width="220" /> |

## 주요 기능

- 소설 관리: 소설 생성/편집/삭제, 프로젝트 통계, 자동 새로고침, ZIP 번들 다운로드, 소설 단위 프롬프트/워크플로 연결, 총 오디오 길이 갱신 및 캐시
- 챕터 관리: 챕터 CRUD, 본문 보기/복사, 챕터 검색, AI→JSON, JSON 보기/편집/찾기 바꾸기, 역할 보기, 대사 미리보기 및 생성, 챕터 오디오 재생/다운로드/병합
- 대사 오디오: 단일 생성, 일괄 생성 후 대사 작업 큐 투입, 즉시 실행 또는 예약 실행 지원; 작업 페이지에서 자동 새로고침, 상세, 재생, 삭제, pending/running 수량 표시 지원
- 역할 라이브러리: 역할 관리, 등급 필터링, 챕터 역할 비교, 역할 라이브러리 추가/교체, 샘플 오디오 업로드/생성, 음성 텍스트 추출 지원
- 작업 큐: JSON 작업 큐와 대사 오디오 작업 큐를 제공하며 상태 보기, 소설별 필터링, 자동 새로고침 지원; 오디오 병합 전 미생성 대사 수를 안내
- 프롬프트 및 워크플로: 시스템/사용자 템플릿 관리, 사용자 템플릿 복사 지원; 워크플로를 3개 카테고리로 나누어 보고, 입출력 노드 설정, 로그 스위치, 시스템/사용자 워크플로 전환 지원
- 워크플로 로그: 실행 시간, 워크플로 카테고리, 워크플로 이름, ComfyUI 에 실제 제출한 최종 JSON, 오류 로그를 기록하며 전체 삭제 지원
- 소설 다운로드: 챕터 번호, 제목, 글자 수, 오디오 길이, 오디오 크기, 다운로드 링크를 표시하는 전용 페이지 제공
- 시스템 설정: ComfyUI URL, LLM 파라미터, Proxy, 배치 텍스트 글자 수, 대사 큐 실행 방식, UI 언어 및 시간대 설정
- 다국어 UI: `zh-CN` / `zh-TW` / `en-US` / `ja-JP` / `ko-KR` 지원
- 오디오 경험: 챕터 오디오, 대사 오디오, 병합 오디오에서 진행 바 드래그 탐색 지원
- 디버그 및 문서: ComfyUI debug 워크플로, 저메모리 워크플로 샘플, 디버그 오디오 샘플, “소설을 오디오북으로 만드는 흐름” 다이어그램 제공

## 주요 경로

- `app_server.py`: 서버 진입점
- `server/startup.py`: 시작 프로세스
- `server/http_handler.py`: HTTP 라우팅
- `server/services.py`: 핵심 서비스
- `scripts/init_storage.py`: DB/디렉터리 초기화
- `prompts/xhz_system_prompt.txt`: 시스템 프롬프트 파일
- `workflows/*.json`: 내장 ComfyUI 워크플로 파일
- `debug/`: ComfyUI 디버그 스크린샷, JSON, 샘플 리소스
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

## ComfyUI 의존성 설명

### 디버그 워크플로와 저메모리 샘플

- `debug/qwen3-tts-generate-character-samples-no-llm.json`: Qwen3 TTS 역할 샘플 오디오 생성
- `debug/fishaudio-s2-tts-generate-dialogue-audio.json`: FishAudio S2 대사 오디오 생성
- `debug/extract-voice-text.json`: Whisper 음성→텍스트 디버그 워크플로
- `debug/line_audio_workflow_qwen3-tts.png`: Qwen3 TTS 저메모리 대사 오디오 워크플로 스크린샷
- `debug/voice_transcribe_workflow_qwen3-asr.png`: Qwen3-ASR 저메모리 텍스트 추출 워크플로 스크린샷
- `debug/1-旁白.flac`: FishAudio S2 디버그 워크플로에 사용하는 기준 음성 샘플

### 워크플로 설정 설명

- 현재 지원하는 워크플로 카테고리는 3가지입니다:
  - `생성 예시 음성`
  - `대사 음성 생성`
  - `음성 텍스트 추출`
- 각 워크플로는 입출력 노드 매핑을 설정할 수 있습니다:
  - 생성 예시 음성: 입력 `음색 설명`, `대사`; 출력 `생성된 음성 파일`
  - 대사 음성 생성: 입력 `참고 음성 파일`, `대사`, `참고 음성 텍스트`; 출력 `생성된 음성 파일`
  - 음성 텍스트 추출: 입력 `음성 파일`; 출력 `추출된 텍스트`
- 시스템 워크플로는 기본 매핑을 사용하며 편집할 수 없습니다. 사용자 워크플로로 복사하면 입출력 설정도 함께 복사됩니다.
- 워크플로 로그는 기본적으로 켜져 있습니다. 켜져 있으면 ComfyUI 에 실제 제출한 최종 JSON 을 기록하고, 꺼져 있으면 해당 워크플로는 로그를 남기지 않습니다.
- 워크플로 JSON 은 ComfyUI API prompt 형식과 그래프 내보내기 형식(`nodes/links`)을 모두 지원합니다.

### 필요한 서드파티 노드

| 플러그인 | 저장소 | 이 프로젝트에서 사용하는 노드 |
| --- | --- | --- |
| Qwen3-TTS ComfyUI | [flybirdxx/ComfyUI-Qwen-TTS](https://github.com/flybirdxx/ComfyUI-Qwen-TTS) | `FB_Qwen3TTSVoiceDesign`, `FB_Qwen3TTSVoiceClone` |
| ComfyUI-FishAudioS2 | [Saganaki22/ComfyUI-FishAudioS2](https://github.com/Saganaki22/ComfyUI-FishAudioS2) | `FishS2VoiceCloneTTS` |
| ComfyUI_Comfyroll_CustomNodes | [Suzie1/ComfyUI_Comfyroll_CustomNodes](https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes) | `CR Text` |
| ComfyUI-MTB | [melMass/comfy_mtb](https://github.com/melMass/comfy_mtb) | `Load Whisper (mtb)`, `Audio To Text (mtb)` |
| ComfyUI-Custom-Scripts | [pythongosssss/ComfyUI-Custom-Scripts](https://github.com/pythongosssss/ComfyUI-Custom-Scripts) | `ShowText\|pysssss` |
| Comfyui_SynVow_Qwen3ASR | [shumoLR/Comfyui_SynVow_Qwen3ASR](https://github.com/shumoLR/Comfyui_SynVow_Qwen3ASR) | `Qwen3ASRLoader`, `Qwen3ASRTranscribe` |

참고: `LoadAudio`, `SaveAudio`, `Text Multiline` 등은 ComfyUI Core 노드입니다.

### 사용 모델

- Qwen3 TTS:
  - `Qwen/Qwen3-TTS-12Hz-1.7B-Base`
  - `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`
  - `Qwen3-TTS-1.7B` (저메모리 대사 오디오 워크플로)
- Fish Audio S2:
  - `s2-pro-fp8`
- Whisper:
  - `large-v3`
- Qwen3 ASR:
  - `Qwen3-ASR-1.7B`

### 현재 내장 워크플로와 의존성 대응표

| 워크플로 파일 | 용도 | 주요 서드파티 노드 | 필요한 모델 |
| --- | --- | --- | --- |
| `workflows/voice_sample_workflow.json` | 역할 샘플 음성 생성 | `FB_Qwen3TTSVoiceDesign`, `CR Prompt Text` | `Qwen/Qwen3-TTS-12Hz-1.7B-Base`, `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` |
| `workflows/line_audio_workflow.json` | 대사 음성 생성 | `FishS2VoiceCloneTTS`, `CR Prompt Text` | `s2-pro-fp8` |
| `workflows/voice_transcribe_workflow.json` | 기준 음성에서 텍스트 추출 | `Load Whisper (mtb)`, `Audio To Text (mtb)`, `ShowText\|pysssss` | [QwenLM/Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) |
| `workflows/line_audio_workflow_qwen3-tts.json` | 저메모리 대사 음성 생성 | `FB_Qwen3TTSVoiceClone`, `CR Prompt Text` | `Qwen3-TTS-1.7B` |
| `workflows/voice_transcribe_workflow_qwen3-asr.json` | 저메모리 음성 텍스트 추출 | `Qwen3ASRLoader`, `Qwen3ASRTranscribe`, `ShowText\|pysssss` | `Qwen3-ASR-1.7B` |

## 페이지

- `index.html`: 소설 관리
- `chapters.html`: 챕터 관리
- `json-tasks.html`: JSON 작업
- `audio-queue.html`: 오디오 큐
- `line-audio-tasks.html`: 대사 오디오 작업 큐
- `roles.html`: 역할 라이브러리
- `novel-download.html`: 소설 다운로드
- `prompts.html`: 프롬프트 관리
- `workflows.html`: 워크플로 관리
- `workflow-logs.html`: 워크플로 로그
- `settings.html`: 설정
- `novel-capture.html`: 소설 캡처
