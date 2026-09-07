#!/usr/bin/env python3
"""클로드체 한국어 린터: AI 티 나는 수사 패턴을 탐지하고 슬롭 지수(0~100)를 매긴다.

사용법:
  python3 lint.py <파일>...          # 리포트 출력
  python3 lint.py --json <파일>...   # JSON 출력
  cat 글.md | python3 lint.py -      # stdin

의존성 없음 (표준 라이브러리만).

등급:
  core  : 단독으로 감점되는 패턴
  circ  : 정황 등급. core 패턴이 하나라도 허용치를 넘었을 때만 가산한다.
          (전각 대시, 어미 통일, 쉼표 비율처럼 사람 글에서도 흔히 나타나는 신호)
검사 제외: 코드 블록, 인라인 코드, 블록쿼트, YAML frontmatter.
"""
import json
import math
import re
import sys
from pathlib import Path

# 명사 규칙 공통 접미: 조사·서술격 활용을 한 번에 받는다.
JOSA = r"(?:은|는|이|가|을|를|의|에|에서|으로|로|로서|로써|와|과|도|만|까지|부터|처럼|마다|이다|입니다|였|이었|예요|이에요)?"
END = r"(?![가-힣])"
# 문장 시작 위치 (줄 시작 또는 문장 부호 뒤)
SOS = r"(?:^|(?<=[.!?…] ))"
NUM = r"(?:한|두|세|네|다섯|여섯|여러|몇|하나의|또 하나의|핵심)"
EM_DASH = chr(0x2014)  # 이 파일 안에 문자 자체를 두지 않는다

# 패턴: id, 이름, 정규식, 1회당 가중치, 문서당 허용 횟수, 등급, 등록 시기
# 허용 횟수 이하는 감점 없음. 기계적 반복만 잡는 설계다.
PATTERNS = [
    # A군: 구조
    dict(id="A1", name="부정 선행 정의 (~가 아니라 / ~가 아닙니다)",
         rx=re.compile(r"(?<!뿐만 )아니라(?![가-힣])|아닙니다|아니에요"),
         weight=3.0, allow=1, tier="core", since="2026-08"),
    dict(id="A1b", name="부정 대구 완화형 (~뿐만 아니라 ~도)",
         rx=re.compile(r"뿐만 아니라"),
         weight=1.0, allow=2, tier="core", since="2026-09"),
    dict(id="A3", name="변환 프레이밍 (A에서 B로 / A가 B가 됩니다)",
         rx=re.compile(r"에서\s[가-힣]+[으로로]\s(?:옮|이동|바뀌|넘어)|[이가]\s?됩니다|로\s바뀝니다"),
         weight=1.5, allow=2, tier="core", since="2026-08"),
    dict(id="A5", name="열거 후 '~까지' 상승",
         rx=re.compile(r"[가-힣A-Za-z0-9]·[^\n]{0,40}까지"),
         weight=2.0, allow=1, tier="core", since="2026-08"),
    dict(id="A7", name="'~하면 끝 / ~면 성공' 무노력 프레이밍",
         rx=re.compile(r"[면든]\s?(?:끝|성공)|끝이에요|끝나요\b|배포\s?끝"),
         weight=2.5, allow=1, tier="core", since="2026-08"),
    dict(id="A7b", name="'한 줄/하나' 최소수량 집착",
         rx=re.compile(r"한\s?(?:줄|번|곳|장|화면|마디)(?:만|이면|으로|에서|의)?|[가-힣]+\s하나(?:만|로|면)"),
         weight=1.2, allow=3, tier="core", since="2026-08"),
    dict(id="A8", name="수사 의문 소제목 (왜 ~인가)",
         rx=re.compile(r"^(?:왜|무엇[을이]?|어디서?|누가|언제)[^\n]{0,25}(?:인가|는가|하나|나요?)\s*$", re.M),
         weight=2.0, allow=1, tier="core", since="2026-08"),
    dict(id="A9", name="정정·단서 접속 상투구 (다만, / 정확히는, / 짚고 넘어가자면)",
         rx=re.compile(SOS + r"(?:다만,|정확히는,|짚고 넘어가자면|엄밀히 말하면|엄밀히는|덧붙이자면|덧붙이면)", re.M),
         weight=1.0, allow=2, tier="core", since="2026-09"),
    # B군: 기호·서식
    dict(id="B1", name="전각 대시(U+2014)",
         rx=re.compile(EM_DASH),
         weight=1.5, allow=1, tier="circ", since="2026-08"),
    dict(id="B2", name="가운뎃점(·) 열거",
         rx=re.compile(r"[가-힣A-Za-z0-9]\s?·\s?[가-힣A-Za-z0-9]"),
         weight=0.8, allow=2, tier="core", since="2026-08"),
    dict(id="B3", name="숫자 펀치 (0원 / ~% 절감 / 절반)",
         rx=re.compile(r"0원|텔레메트리\s?0|%\s?(?:저렴|절감|할인)|절반\s?(?:덜|만|으로)"),
         weight=1.5, allow=1, tier="core", since="2026-08"),
    dict(id="B4", name="화살표(→) 서사",
         rx=re.compile(r"→"),
         weight=1.0, allow=1, tier="core", since="2026-08"),
    dict(id="B5", name="서식 남용 (이모지 불릿 / 제목 단계 건너뜀)",
         rx=None, fn="formatting", weight=1.0, allow=0, tier="core", since="2026-09"),
    dict(id="B6", name="굵은 글씨 남용",
         rx=re.compile(r"\*\*[^*\n]+\*\*"),
         weight=0.4, allow=4, tier="core", since="2026-09"),
    # C군: 어휘
    dict(id="C1", name="공간·추상 은유 명사 (자리/판/틈/경계/갈래/축)",
         rx=re.compile(
             r"(?<![가-힣])자리(?![가-힣]{0,2}\s?(?:잡|앉))" + JOSA + END
             + r"|(?<![가-힣])판(?:을|이|은|에서|으로|도)" + END
             + r"|(?<![가-힣])틈(?:을|이|은|도|에|으로)" + END
             + r"|앞단|경계(?:를|가|는)" + END
             + r"|" + NUM + r"\s?갈래|(?<![가-힣])갈래(?:로|가|를|는|입니다)" + END
             + r"|" + NUM + r"\s?축(?:은|는|이|이다|입니다|으로|을|이었)?" + END
             + r"|(?<![가-힣])축으로\s?(?:삼|잡|두)"),
         weight=1.5, allow=3, tier="core", since="2026-08"),
    dict(id="C3", name="진정성 마커 (진짜/실제로/그대로/직접)",
         rx=re.compile(r"진짜|실제로|그대로|직접\s"),
         weight=0.7, allow=3, tier="core", since="2026-08"),
    dict(id="C4", name="강조 부사 (딱/바로 그/통째로/매번/그때그때)",
         rx=re.compile(r"\b딱\s|바로\s그|통째로|그때그때|매번"),
         weight=0.7, allow=2, tier="core", since="2026-08"),
    dict(id="C5", name="관용 과장구 (눈 깜짝할/철통/압도적/파고들다)",
         rx=re.compile(r"눈\s?깜짝|철통|압도적|자존심을\s걸고|파고들"),
         weight=2.0, allow=1, tier="core", since="2026-08"),
    dict(id="C6", name="번역투 에이전트 어휘 (떠받치다/조용히 삼켜지다/근본 원인은/재검증·교차 확인)",
         rx=re.compile(r"떠받치|조용히\s?(?:삼켜|넘어가|실패|무시)|말 그대로|근본 원인은|"
                       r"재검증(?:했|합니다|하여|해|을)|교차\s?(?:확인|검증)|재확인했|"
                       r"즉시 실패(?:하도록|하게)|바이트 단위로\s?(?:동일|같)|의도적으로|실제로 동작에"),
         weight=1.5, allow=1, tier="core", since="2026-09"),
    dict(id="C7", name="단순 동사 회피 (~로 기능합니다 / 구실을 합니다 / ~로 작용합니다)",
         rx=re.compile(r"기능(?:합니다|하는|해요|한다)(?![가-힣])|구실을\s?(?:합니다|하는|한다)|"
                       r"(?:으로|로)\s?작용(?:합니다|하는|해요|한다)(?![가-힣])"),
         weight=1.0, allow=2, tier="core", since="2026-09"),
    dict(id="C8", name="추상 한자어·용어 남발 (층위/정체/지문/결벽/낙차/프레이밍)",
         rx=re.compile(r"층위|정체(?=[가는를은])|지문(?=[이가을은])|결벽|무결성|낙차|프레이밍|담론|함의|정합성|"
                       r"위상(?=[이가을은])|서사(?=[가를로는])|장치(?=[를가다])|메커니즘|패러다임"),
         weight=0.7, allow=3, tier="core", since="2026-09"),
    # D군: 담화
    dict(id="D5", name="맺음말 상투구 (결론적으로 / 종합하면 / 정리하자면 / 요약하면)",
         rx=re.compile(r"결론적으로|종합하면|종합하자면|정리하자면|정리하면|요약하면|요약하자면|요컨대"),
         weight=3.0, allow=1, tier="core", since="2026-09"),
    dict(id="A10", name="3항 강박 (목록 항목이 늘 3개)",
         rx=None, fn="triads", weight=1.5, allow=1, tier="core", since="2026-09"),
]


def mask(text):
    """검사 제외 영역을 같은 길이의 공백으로 치환한다 (줄 번호 보존)."""
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    text = re.sub(r"\A---\n.*?\n---\n", blank, text, flags=re.S)       # frontmatter
    text = re.sub(r"```.*?(?:```|\Z)", blank, text, flags=re.S)          # 코드 블록
    text = re.sub(r"`[^`\n]+`", blank, text)                             # 인라인 코드
    text = re.sub(r"^[ \t]*>.*$", blank, text, flags=re.M)               # 블록쿼트
    return text


def sentences(text):
    out = []
    for s in re.split(r"[.!?…\n]+", text):
        s = s.strip()
        if len(s) >= 4 and re.search(r"[가-힣]$", s):
            out.append(s)
    return out


def ending_uniformity(sents):
    """존대 문체 어미 통일도 (0~1). 문어체 다체는 원래 균일하므로 제외."""
    buckets = {"hamnida": 0, "haeyo": 0, "plain": 0}
    for s in sents:
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


def comma_ratio(sents):
    """쉼표가 든 문장 비율. KatFish(ACL 2025): 사람 26% vs 모델 61% (에세이)."""
    if len(sents) < 8:
        return None
    return sum("," in s for s in sents) / len(sents)


def long_sentences(sents, limit=35):
    """어절 수가 limit 이상인 문장 수. 한국어 산문은 대개 15~20어절이다."""
    return sum(len(s.split()) >= limit for s in sents)


LIST_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")
EMOJI_BULLET = re.compile(r"^\s*(?:[-*+]\s+)?[\U0001F300-\U0001FAFF☀-➿⭐✅✔]")
HEADING = re.compile(r"^(#{1,6})\s+\S")


def hits_triads(text):
    """마크다운 목록의 60% 이상이 정확히 3항이고 목록이 3개 이상이면 3항 목록마다 1회."""
    lines = text.splitlines()
    lists, cur, start, kind = [], 0, 0, None

    def marker(ln):
        m = LIST_LINE.match(ln)
        if not m:
            return None
        return "num" if ln.lstrip()[0].isdigit() else "bul"

    for i, ln in enumerate(lines, 1):
        k = marker(ln)
        if k:
            if cur and k != kind:          # 마커 종류가 바뀌면 다른 목록
                lists.append((start, cur)); cur = 0
            if cur == 0:
                start, kind = i, k
            cur += 1
        elif ln.strip() == "" and cur:
            # 빈 줄 하나는 같은 종류의 목록이 이어질 때만 같은 목록으로 본다 (느슨한 목록)
            nxt = lines[i] if i < len(lines) else ""
            if marker(nxt) != kind:
                lists.append((start, cur)); cur = 0
        elif cur:
            lists.append((start, cur)); cur = 0
    if cur:
        lists.append((start, cur))
    if len(lists) < 3:
        return []
    triads = [l for l in lists if l[1] == 3]
    if len(triads) / len(lists) < 0.6:
        return []
    return [dict(line=s, match="3항 목록", context=lines[s - 1].strip()[:80]) for s, _ in triads]


def hits_formatting(text):
    lines = text.splitlines()
    out, prev = [], None
    for i, ln in enumerate(lines, 1):
        if EMOJI_BULLET.match(ln):
            out.append(dict(line=i, match="이모지 불릿", context=ln.strip()[:80]))
        m = HEADING.match(ln)
        if m:
            lvl = len(m.group(1))
            if prev is not None and lvl - prev > 1:
                out.append(dict(line=i, match=f"제목 단계 건너뜀 (h{prev}→h{lvl})", context=ln.strip()[:80]))
            prev = lvl
    return out


STRUCT_FNS = {"triads": hits_triads, "formatting": hits_formatting}


def lint(text):
    masked = mask(text)
    chars = max(len(re.sub(r"\s", "", masked)), 300)  # 짧은 글 밀도 폭발 방지
    lines = text.splitlines()
    findings = []
    for p in PATTERNS:
        if p["rx"] is not None:
            hits = []
            for m in p["rx"].finditer(masked):
                ln = masked.count("\n", 0, m.start()) + 1
                hits.append(dict(line=ln, match=m.group(0).strip(),
                                 context=lines[ln - 1].strip()[:80] if ln <= len(lines) else ""))
        else:
            hits = STRUCT_FNS[p["fn"]](masked)
        over = max(0, len(hits) - p["allow"])
        if hits:
            findings.append(dict(id=p["id"], name=p["name"], tier=p["tier"], since=p["since"],
                                 count=len(hits), allow=p["allow"], over=over,
                                 penalty=round(over * p["weight"], 1), hits=hits))
    # 정황 등급 문서 신호
    sents = sentences(masked)
    uni, n = ending_uniformity(sents)
    if uni is not None and uni > 0.92:
        findings.append(dict(id="D4", name=f"어미 통일 결벽 ({uni:.0%}, {n}문장)", tier="circ",
                             since="2026-08", count=1, allow=0, over=1, penalty=3.0, hits=[]))
    cr = comma_ratio(sents)
    if cr is not None and cr > 0.55:
        findings.append(dict(id="D6", name=f"쉼표 문장 비율 과다 ({cr:.0%}, {len(sents)}문장)", tier="circ",
                             since="2026-09", count=1, allow=0, over=1, penalty=2.0, hits=[]))
    ls = long_sentences(sents)
    if ls >= 2:
        findings.append(dict(id="D7", name=f"긴 문장 (35어절 이상 {ls}개)", tier="circ",
                             since="2026-09", count=ls, allow=1, over=ls - 1, penalty=round((ls - 1) * 1.0, 1), hits=[]))
    # 정황 등급은 core 초과가 있을 때만 가산
    has_core_over = any(f["over"] and f["tier"] == "core" for f in findings)
    penalty = 0.0
    for f in findings:
        if f["tier"] == "circ" and not has_core_over:
            f["penalty"] = 0.0
            f["suppressed"] = True
        penalty += f["penalty"]
    density = penalty / chars * 1000
    score = round(100 * (1 - math.exp(-density / 12)), 1)
    return dict(score=score, density=round(density, 2), chars=chars,
                findings=sorted(findings, key=lambda f: (-f["penalty"], -f["count"])))


def print_report(label, r):
    bar = "█" * int(r["score"] / 5) + "░" * (20 - int(r["score"] / 5))
    print(f"\n== {label}")
    print(f"   슬롭 지수 {r['score']:5.1f}/100  {bar}   (밀도 {r['density']}/1000자, {r['chars']}자)")
    for f in r["findings"]:
        flag = "▲" if f["over"] and not f.get("suppressed") else " "
        tier = "·정황" if f["tier"] == "circ" else ""
        note = " (core 초과 없어 미가산)" if f.get("suppressed") else ""
        print(f"   {flag} [{f['id']}{tier}] {f['name']}: {f['count']}회 (허용 {f['allow']}, 감점 {f['penalty']}){note}")
        if f["over"] and not f.get("suppressed"):
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
