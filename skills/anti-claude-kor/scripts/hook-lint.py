#!/usr/bin/env python3
"""Claude Code PostToolUse 훅: 마크다운/텍스트 파일을 쓰거나 고칠 때 슬롭 지수를 검사한다.

settings.json 예시:
{
  "hooks": {
    "PostToolUse": [
      {"matcher": "Write|Edit",
       "hooks": [{"type": "command",
                  "command": "python3 ~/.claude/skills/anti-claude-kor/scripts/hook-lint.py"}]}
    ]
  }
}

임계(기본 20)를 넘으면 exit 2로 요약을 stderr에 돌려보내 모델이 바로 고치게 한다.
환경변수 ANTI_CLAUDE_KOR_THRESHOLD 로 임계를 바꿀 수 있다.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
path = (data.get("tool_input") or {}).get("file_path") or ""
if not path.endswith((".md", ".markdown", ".txt")) or not Path(path).exists():
    sys.exit(0)
threshold = float(os.environ.get("ANTI_CLAUDE_KOR_THRESHOLD", "20"))
lint = Path(__file__).resolve().parent / "lint.py"
out = subprocess.run([sys.executable, str(lint), "--json", path], capture_output=True, text=True)
if out.returncode != 0:
    sys.exit(0)
r = json.loads(out.stdout)[path]
if r["score"] < threshold:
    sys.exit(0)
over = [f"{f['id']} {f['name']} {f['count']}회" for f in r["findings"] if f["over"] and not f.get("suppressed")]
print(f"[anti-claude-kor] {path}: 슬롭 지수 {r['score']} (임계 {threshold:g}). 초과 패턴: "
      + "; ".join(over[:6]) + ". /ack 로 정제하거나 해당 표현을 줄이세요.", file=sys.stderr)
sys.exit(2)
