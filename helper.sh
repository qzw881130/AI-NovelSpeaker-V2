#!/usr/bin/env bash
set -euo pipefail

show_header() {
  printf '%s\n' "AI-NovelSpeaker-V2 helper"
  printf '%s\n' "Usage:"
  printf '%s\n' "  ./helper.sh"
  printf '%s\n' "  ./helper.sh list"
  printf '%s\n' "  ./helper.sh help <script-key>"
  printf '%s\n' ""
  printf '%s\n' "Script keys:"
  printf '%s\n' "  process-silences"
  printf '%s\n' "  backfill-durations"
  printf '%s\n' "  init-storage"
  printf '%s\n' "  import-hlm"
  printf '%s\n' "  normalize-hlm-titles"
  printf '%s\n' ""
}

show_process_silences() {
  cat <<'EOF'
process-silences
  Function:
    批量执行“识别所有长空音频” + “删除音频标记片段”。
    按小说 ID 和起止章回扫描已完成台词音频，检测长静音并删除标记片段。

  Command:
    .venv/bin/python scripts/process_line_audio_silences.py --novel-id=<ID> --start=<章回> --end=<章回> [options]

  Required parameters:
    --novel-id=<ID>       小说 ID，例如 8
    --start=<章回编号>    起始章回，例如 061
    --end=<章回编号>      结束章回，例如 084

  Optional parameters:
    --noise-db=-45dB      ffmpeg silencedetect 阈值，默认 -45dB
    --min-duration=1.2    最短静音长度，默认 1.2 秒
    --dry-run             只检测并打印结果，不改音频文件
    --yes                 跳过执行前确认
    --quiet               只输出最终摘要和错误

  Examples:
    .venv/bin/python scripts/process_line_audio_silences.py --novel-id=8 --start=061 --end=084 --dry-run
    .venv/bin/python scripts/process_line_audio_silences.py --novel-id=8 --start=061 --end=084
    .venv/bin/python scripts/process_line_audio_silences.py --novel-id=8 --start=061 --end=084 --yes
EOF
}

show_backfill_durations() {
  cat <<'EOF'
backfill-durations
  Function:
    批量补齐已完成台词音频任务的 duration_seconds。
    适用于旧任务缺少音频时长缓存时修复数据库显示。

  Command:
    .venv/bin/python scripts/backfill_line_audio_durations.py --novel-id=<ID> [options]

  Required parameters:
    --novel-id=<ID>       小说 ID，例如 8

  Optional parameters:
    --chapter-id=1,2,3    指定章回编号，逗号分隔；不填表示全部章回
    --db=<path>           SQLite 数据库路径，默认 data/novels.db
    --dry-run             只扫描不写入
    --quiet               只输出最终摘要

  Examples:
    .venv/bin/python scripts/backfill_line_audio_durations.py --novel-id=8 --dry-run
    .venv/bin/python scripts/backfill_line_audio_durations.py --novel-id=8 --chapter-id=61,62,63
EOF
}

show_init_storage() {
  cat <<'EOF'
init-storage
  Function:
    初始化/迁移本地 SQLite 存储结构，创建默认 prompt、workflow、数据目录等。

  Command:
    .venv/bin/python scripts/init_storage.py

  Parameters:
    无命令行参数。

  Example:
    .venv/bin/python scripts/init_storage.py
EOF
}

show_import_hlm() {
  cat <<'EOF'
import-hlm
  Function:
    从固定路径 /Volumes/Data/books/literature-books/红楼梦.txt 导入红楼梦文本到 english_dir=hlm 的小说。
    会拆分章节、写入 novel/hlm/text，并重新导入章节和统计字数。

  Command:
    .venv/bin/python scripts/import_hongloumeng_text.py

  Parameters:
    无命令行参数。源码路径和目标 english_dir 写在脚本内。

  Example:
    .venv/bin/python scripts/import_hongloumeng_text.py
EOF
}

show_normalize_hlm_titles() {
  cat <<'EOF'
normalize-hlm-titles
  Function:
    将 english_dir=hlm 的红楼梦章节标题从阿拉伯数字章号规范为中文章号。
    同步更新文本文件名、章节表、json_tasks 中的标题和 JSON 文本。

  Command:
    .venv/bin/python scripts/normalize_hlm_chapter_titles.py

  Parameters:
    无命令行参数。目标 english_dir 写在脚本内。

  Example:
    .venv/bin/python scripts/normalize_hlm_chapter_titles.py
EOF
}

show_all() {
  show_header
  show_process_silences
  printf '\n'
  show_backfill_durations
  printf '\n'
  show_init_storage
  printf '\n'
  show_import_hlm
  printf '\n'
  show_normalize_hlm_titles
  printf '\n'
  printf '%s\n' "Tip: use './helper.sh help <script-key>' to show one script only."
}

case "${1:-list}" in
  list|--help|-h)
    show_all
    ;;
  help)
    case "${2:-}" in
      process-silences) show_process_silences ;;
      backfill-durations) show_backfill_durations ;;
      init-storage) show_init_storage ;;
      import-hlm) show_import_hlm ;;
      normalize-hlm-titles) show_normalize_hlm_titles ;;
      "")
        show_header
        ;;
      *)
        printf 'Unknown script key: %s\n\n' "$2" >&2
        show_header
        exit 2
        ;;
    esac
    ;;
  *)
    printf 'Unknown command: %s\n\n' "$1" >&2
    show_header
    exit 2
    ;;
esac
