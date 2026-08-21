#!/usr/bin/env python3
"""클로드체 한국어 린터: AI 티 나는 수사 패턴을 탐지하고 슬롭 지수(0~100)를 매긴다.

사용법:
  python3 lint.py <파일>...          # 리포트 출력
  python3 lint.py --json <파일>...   # JSON 출력
  cat 글.md | python3 lint.py -      # stdin

의존성 없음 (표준 라이브러리만).
"""
import json
import math
import re
import sys
from pathlib import Path

# 패턴: (id, 이름, 정규식, 1회당 가중치, 문서당 허용 횟수)
# 허용 횟수 이하는 감점 없음. 기계적 반복만 잡는 설계다.
PATTERNS = [
    ("A1", "부정 선행 정의 (~가 아니라 / ~가 아닙니다)",
     re.compile(r"아니라[,\s\u2014]|아닙니다|아니에요|아니라\b"), 3.0, 1),
    ("A3", "변환 프레이밍 (A에서 B로 / A가 B가 됩니다)",
     re.compile(r"에서\s[가-힣]+[으로로]\s(?:옮|이동|바뀌|넘어)|[이가]\s?됩니다|로\s바뀝니다"), 1.5, 2),
    ("A5", "열거 후 '~까지' 상승",
     re.compile(r"[가-힣A-Za-z0-9]·[^\n]{0,40}까지"), 2.0, 1),
    ("A7", "'~하면 끝 / ~면 성공' 무노력 프레이밍",
     re.compile(r"[면든]\s?(?:끝|성공)|끝이에요|끝나요\b|배포\s?끝"), 2.5, 1),
    ("A7b", "'한 줄/하나' 최소수량 집착",
     re.compile(r"한\s?(?:줄|번|곳|장|화면|마디)(?:만|이면|으로|에서|의)?|[가-힣]+\s하나(?:만|로|면)"), 1.2, 3),
    ("A8", "수사 의문 소제목 (왜 ~인가)",
     re.compile(r"^(?:왜|무엇[을이]?|어디서?|누가|언제)[^\n]{0,25}(?:인가|는가|하나|나요?)\s*$", re.M), 2.0, 1),
    ("B1", "전각 대시(U+2014)", re.compile(r"\u2014"), 1.5, 1),
    ("B2", "가운뎃점(·) 열거",
     re.compile(r"[가-힣A-Za-z0-9]\s?·\s?[가-힣A-Za-z0-9]"), 0.8, 2),
    ("B3", "숫자 펀치 (0원 / ~% 절감 / 절반)",
     re.compile(r"0원|텔레메트리\s?0|%\s?(?:저렴|절감|할인)|절반\s?(?:덜|만|으로)"), 1.5, 1),
    ("B4", "화살표(→) 서사", re.compile(r"→"), 1.0, 1),
    ("C1", "공간 은유 (자리/판/틈/앞단)",
     re.compile(r"자리(?:를|가|에|는|입니다|다|로)\b|판[을이은]\s|틈을|앞단|경계[를가]"), 1.5, 2),
    ("C3", "진정성 마커 (진짜/실제로/그대로/직접)",
     re.compile(r"진짜|실제로|그대로|직접\s"), 0.7, 3),
    ("C4", "강조 부사 (딱/바로 그/통째로/매번/그때그때)",
     re.compile(r"\b딱\s|바로\s그|통째로|그때그때|매번"), 0.7, 2),
    ("C5", "관용 과장구 (눈 깜짝할/철통/압도적/파고들다)",
     re.compile(r"눈\s?깜짝|철통|압도적|자존심을\s걸고|파고들"), 2.0, 1),
]


def ending_uniformity(text):
    """존대 문체 어미 통일도 (0~1). 문어체 다체는 원래 균일하므로 제외."""
    buckets = {"hamnida": 0, "haeyo": 0, "plain": 0}
    for s in re.split(r"[.!?…\n]+", text):
        s = s.strip()
        if len(s) < 4 or not re.search(r"[가-힣]$", s):
            continue
        if re.search(r"(니다|십시오)$", s):
            buckets["hamnida"] += 1
        elif re.search(r"요$", s):
            buckets["haeyo"] += 1
        elif re.search(r"[다까]$", s):
            buckets["plain"] += 1
    total = sum(buckets.values())
    if total < 8:
        return None, total
    dominant = max(buckets, key=lambda k: buckets[k])
    if dominant == "plain":
        return 0.0, total
    return buckets[dominant] / total, total


def lint(text):
    chars = max(len(re.sub(r"\s", "", text)), 300)  # 짧은 글 밀도 폭발 방지
    lines = text.splitlines()
    findings = []
    penalty = 0.0
    for pid, name, rx, weight, allow in PATTERNS:
        hits = []
        for m in rx.finditer(text):
            ln = text.count("\n", 0, m.start()) + 1
            hits.append({"line": ln, "match": m.group(0).strip(),
                         "context": lines[ln - 1].strip()[:80] if ln <= len(lines) else ""})
        over = max(0, len(hits) - allow)
        pen = over * weight
        penalty += pen
        if hits:
            findings.append({"id": pid, "name": name, "count": len(hits),
                             "allow": allow, "over": over, "penalty": round(pen, 1),
                             "hits": hits})
    # 어미 통일은 단독 증거가 아니라 정황 증거다. 격식 문서의 합니다체 통일은
    # 관례이므로, 다른 패턴이 허용치를 넘었을 때만 가산한다.
    has_over = any(f["over"] for f in findings)
    uni, n = ending_uniformity(text)
    if has_over and uni is not None and uni > 0.92:
        penalty += 3.0
        findings.append({"id": "D4", "name": f"어미 통일 결벽 ({uni:.0%}, {n}문장)",
                         "count": 1, "allow": 0, "over": 1, "penalty": 3.0, "hits": []})
    density = penalty / chars * 1000
    score = round(100 * (1 - math.exp(-density / 12)), 1)
    return {"score": score, "density": round(density, 2), "chars": chars,
            "findings": sorted(findings, key=lambda f: -f["penalty"])}


def print_report(label, r):
    bar = "█" * int(r["score"] / 5) + "░" * (20 - int(r["score"] / 5))
    print(f"\n== {label}")
    print(f"   슬롭 지수 {r['score']:5.1f}/100  {bar}   (밀도 {r['density']}/1000자, {r['chars']}자)")
    for f in r["findings"]:
        flag = "▲" if f["over"] else " "
        print(f"   {flag} [{f['id']}] {f['name']}: {f['count']}회 (허용 {f['allow']}, 감점 {f['penalty']})")
        if f["over"]:
            for h in f["hits"][:3]:
                print(f"       L{h['line']}: …{h['context']}")


def main():
    args = sys.argv[1:]
    as_json = "--json" in args
    files = [a for a in args if a != "--json"]
    if not files:
        sys.exit(__doc__)
    results = {}
    for f in files:
        text = sys.stdin.read() if f == "-" else Path(f).read_text(encoding="utf-8")
        results[f] = lint(text)
        if not as_json:
            print_report(f, results[f])
    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
