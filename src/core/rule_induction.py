"""전문에서 경계 규약을 스스로 찾는다 — 층계(cascade)로 (D-116·D-117).

왜 필요한가:
    책마다 글의 시작 표지가 다르다 — 浩齋辰巳日錄은 「○+날짜」, 天津談草는 「날짜 … 談草」,
    문집은 목차와 卷頭. 이것을 코드에 책 이름으로 적어 두면 새 책이 올 때마다 코드를 고쳐야
    한다. 대신 «표지는 책 안에서 되풀이된다»는 사실만 코드에 두고, 어느 표지가 이 책의
    규약인지는 전문을 세어 **데이터**(manifest.segmentation_rules)로 저장한다.

층계 — 사람이 책을 펼쳤을 때 보는 순서(D-117, 사용자 지시 2026-09-07):
    1단 목차   목차 쪽이 있고 항목이 본문과 충분히 대조되면 목차가 규약이다. 여기서 멈춘다.
              목차 쪽은 아래 단에서 세지 않는다 — 목차의 짧은 행을 본문 규약으로 «배우면» 안 된다.
    2단 시각   되풀이되는 기호(○●△… 한자가 아닌 글자), 내려쓰기. 눈에 띄는 것이 먼저다.
    3단 텍스트 날짜 문법, 짧은 행 끝 어휘, 행 첫머리 글자.
    4단 LLM    통계가 아무것도 못 찾았을 때만, 사람이 누르면 — extract_start_patterns_llm.
    0단 없음   사람이 찍는다.
    위 단이 확실하면 아래 단은 «보조»로만 남고(세기는 하되 권고하지 않는다), 약하면 내려간다.

원리 (모든 단에 같다):
    후보 «신호 가족»마다 전문에서 나온 자리를 모으고
      (1) 횟수 (2) 간격의 고름 — 연속 출현 사이 행 수가 «매 행»도 «한 번»도 아닌 것
      (3) 날짜 가족이면 날짜 사슬(날이 뒤로 가지 않는 비율)
      (4) 잇달아 나오는 비율 — 표지는 줄지어 오지 않는다
          (시의 본문처럼 내려쓴 «덩어리»는 표지가 아니다)
    로 점수를 매긴다. «쪽마다 한 번, 같은 행 자리»에 되풀이되는 것(판심·엽수·서명)은
    글의 규약이 아니라 종이의 규약이라 버린다(page_furniture).

무엇을 하지 않는가:
    경계를 만들지 않는다. 결과는 신호 목록(횟수·점수·보기)과 그것으로 만든 규칙 초안이고,
    화면이 체크박스로 보여 준 뒤 사람이 고른 것만 저장한다. 제안·적용은 segmentation.py가
    저장된 규칙으로 한다 — 이 모듈은 규칙을 «찾는» 쪽이고, 규칙을 «쓰는» 쪽은 그쪽이다.

신호 가족과 규칙의 대응 (toggle 열):
    toc          목차                      → (규칙이 아니라 제안 입력; 화면의 목차 줄)
    symbol:X     되풀이되는 기호           → symbols 목록 (혼자 후보를 만든다)
    indent_alone 내려쓰기만으로 경계       → indent_alone
    date         행 첫머리 날짜            → signals.date
    mark         ○+날짜 (행 어디서든)      → signals.mark
    volume       卷頭                      → signals.volume
    title_word:X 짧은 행을 끝맺는 어휘     → title_words 목록
    head_word:X  행 첫머리에 편중된 글자   → head_words 목록
    short_line·after_short·indent (보조)   → signals.* — 다른 신호가 있는 행의 신뢰도만 올린다
"""

from __future__ import annotations

import collections
import math
import statistics
import unicodedata
from pathlib import Path
from typing import Optional

from core.segmentation import (
    Line,
    _layout_signals,
    _line_candidates,
    fold_text,
    normalize_rules,
    parse_date_head,
    parse_wrapped_date_head,
    volume_head,
)
from core.toc import lenient_json

# 사람이 읽는 이름. 코드 밖(화면)에서도 같은 말을 쓴다.
SIGNAL_LABELS: dict[str, str] = {
    "date": "날짜가 행 첫머리에",
    "mark": "○ 권점 + 날짜",
    "volume": "卷頭 (卷之一 …)",
    "indent_alone": "내려쓰기만으로 경계",
    "short_line": "짧은 행 (보조)",
    "after_short": "행갈음 뒤의 행 (보조)",
    "indent": "내려쓰기 (보조)",
}
STAGE_NAMES = {1: "목차", 2: "시각 신호", 3: "텍스트 패턴", 0: "찾지 못함"}

# 규칙 목록(title_words·head_words·symbols·templates)이 아니라 켜고 끄는 신호.
# propose_boundaries가 읽는 키.
# 발견기가 «찾은 꼴이 날짜·卷 문법이면» 이 스위치에 배정한다 — 종류를 미리 세지 않는다(D-119).
TOGGLE_SIGNALS = ("date", "mark", "volume", "short_line", "after_short", "indent")
_PRIMARY = ("date", "mark", "volume")  # 문법으로 판정되는 텍스트 신호(폴백 기본값용)
_AUX = ("short_line", "after_short", "indent")  # 판식 물리 — 다른 신호가 있는 행의 점수만 올린다

_UNKNOWN = "□"  # OCR이 못 읽은 글자. 어휘 후보에서 뺀다 — 규약이 아니라 잡음이다
# 구두점·괄호·OCR 잡음은 기호 후보가 아니다. 그 밖의 «한자·숫자·문자가 아닌 글자»는 전부 후보다.
_NOT_SYMBOL = set(
    "、。，．：；！？「」『』（）()[]【】〈〉《》・･…‥—–-_~〜/／\\|＊*＋+=＝<>＜＞\"'“”‘’ 　\t"
)

# 층계 문턱. 표지라면 넷 이상, 간격이 고르고(0.6), 매 행은 아니며(60%), 줄지어 오지 않는다(50%).
_MIN_COUNT = 4
_MIN_REGULARITY = 0.6
_MAX_DENSITY = 60.0
_MAX_ADJACENT = 0.5
_TOC_MIN_RATIO = 0.8  # 목차 항목의 이 비율 이상이 본문에서 찾아지면 «목차가 규약»
_TOC_MIN_MATCHES = 3  # …그리고 대조된 항목이 이만큼은 있어야 한다


def is_symbol_char(ch: str) -> bool:
    """되풀이 기호 후보인가 — 한자·숫자·문자·공백·구두점·□이 아닌 글자."""
    if ch in _NOT_SYMBOL or ch == _UNKNOWN or ch.isspace():
        return False
    cat = unicodedata.category(ch)
    if cat.startswith("L") or cat.startswith("N"):  # 문자(한자 포함)·숫자
        return False
    return cat.startswith("S") or cat.startswith("P")


def _gap_regularity(positions: list[int]) -> tuple[float, Optional[float]]:
    """연속 출현 사이 행 수가 고른 비율과 중앙값.

    표지라면 간격이 «너무 촘촘하지도(매 행) 너무 드물지도» 않고 고르다. 중앙값의 3배를
    넘는 간격이 많으면 우연히 흩어진 것이다.
    """
    if len(positions) < 3:
        return 0.0, None
    gaps = [b - a for a, b in zip(positions, positions[1:]) if b > a]
    if not gaps:
        return 0.0, None
    med = statistics.median(gaps)
    within = sum(1 for g in gaps if 0 < g <= 3 * med) / len(gaps)
    return within, med


def _adjacent_fraction(positions: list[int]) -> float:
    """바로 앞 행도 같은 가족인 비율.

    표지는 줄지어 오지 않는다 — 내려쓴 시 본문·목차 덩어리를 가른다.
    """
    if len(positions) < 2:
        return 0.0
    s = set(positions)
    return sum(1 for k in positions if k - 1 in s) / len(positions)


def _page_furniture(lines: list[Line], positions: list[int]) -> bool:
    """판심·엽수·서명처럼 «쪽마다 한 번, 같은 행 자리»에 되풀이되는가.

    쪽당 1회인 쪽이 70% 이상이고 쪽 안 행 위치의 최빈값이 절반 이상이면 그렇다고 본다.
    (天津談草 실측 2026-09-06: 판심의 「京」「取」「六七」「三番」이 이것으로 빠졌다.)
    """
    per_page = collections.Counter(lines[k].page for k in positions)
    if len(per_page) < 3:
        return False
    frac_one = sum(1 for c in per_page.values() if c == 1) / len(per_page)
    idx_mode = collections.Counter(lines[k].line_index for k in positions).most_common(1)[0][1]
    return frac_one >= 0.7 and idx_mode / len(positions) >= 0.5


def _chain_ok(lines: list[Line], positions: list[int]) -> Optional[float]:
    """날짜 가족의 사슬 일관성 — «날이 뒤로 가지 않는» 이웃 쌍의 비율. 3개 미만이면 None."""
    days = []
    n = len(lines)
    for k in positions:
        nxt = lines[k + 1].text.strip() if k + 1 < n else ""
        for _off, cand in _line_candidates(lines[k].text.strip(), nxt):
            h = parse_date_head(cand)
            if not h.present:
                h = parse_wrapped_date_head(cand, nxt)
            if h.present and h.day is not None:
                days.append((h.month, h.day))
    if len(days) < 3:
        return None
    ok = 0
    for (m1, d1), (m2, d2) in zip(days, days[1:]):
        if (m1 is not None and m2 is not None and m2 != m1) or d2 >= d1 or d2 <= 3:
            ok += 1
    return ok / (len(days) - 1)


def _examples(lines: list[Line], positions: list[int], limit: int = 3) -> list[str]:
    """보기 몇 줄 — 화면 도움말용. 앞·가운데·뒤에서 하나씩 고른다."""
    if not positions:
        return []
    picks = sorted({positions[0], positions[len(positions) // 2], positions[-1]})[:limit]
    return [f"{lines[k].page}쪽 {lines[k].text.strip()[:20]}" for k in picks]


def toc_signal(lines: list[Line], rules: Optional[dict] = None) -> Optional[dict]:
    """1단 — 목차 쪽을 찾고 항목을 본문과 대조한 요약. 목차가 없으면 None.

    출력: {"pages": [...], "entries": n, "matched": m, "ratio": m/n, "decisive": bool}
    규칙만 쓴다(LLM 없음). 항목 구조화에 LLM을 쓰는 것은 제안·자동 트리의 몫이다.
    """
    from core.toc import align_toc_to_body, detect_toc_pages, extract_toc_entries_rule

    rules = normalize_rules(rules)
    page_lines: dict[int, list[str]] = {}
    for ln in lines:
        page_lines.setdefault(ln.page, []).append(ln.text)
    if not page_lines:
        return None
    toc_pages = detect_toc_pages(page_lines, rules["max_title_chars"])
    if not toc_pages:
        return None
    entries = extract_toc_entries_rule(page_lines, toc_pages)
    if not entries:
        return None
    body = [ln for ln in lines if ln.page not in set(toc_pages) and ln.text.strip()]
    matches, _un = align_toc_to_body(entries, body)
    ratio = len(matches) / max(1, len(entries))
    return {
        "pages": toc_pages,
        "entries": len(entries),
        "matched": len(matches),
        "ratio": round(ratio, 2),
        "decisive": len(matches) >= _TOC_MIN_MATCHES and ratio >= _TOC_MIN_RATIO,
    }


def toc_decisive(matched: int, entries: int) -> bool:
    """목차가 «이 책의 규약»으로 설 만큼 대조되었는가 — 라우터도 같은 문턱을 쓴다."""
    return matched >= _TOC_MIN_MATCHES and matched / max(1, entries) >= _TOC_MIN_RATIO


def _solid(r: dict) -> bool:
    if r.get("marker") == "repeat_text":
        return False
    """표지로 볼 만큼 되풀이·고름·밀도·연속성이 맞는가.

    내려쓰기 단독은 훨씬 엄격하다 — 네 책 눈가림(2026-09-07)에서 내려쓴 행의 연속 비율이
    0.4~0.5로 전부 «덩어리»(협주·두주·시 본문)였는데 0.5 문턱을 통과해 천진담초 정밀도가
    65/240으로 무너졌다. 제목만 내려쓴 판식이라면 연속은 거의 0이고 밀도도 낮다.
    """
    ok = (
        r["count"] >= _MIN_COUNT
        and r["gap_regularity"] >= _MIN_REGULARITY
        and r["per100"] <= _MAX_DENSITY
        and r["adjacent"] <= _MAX_ADJACENT
        and r.get("short_frac", 1.0) >= 0.5
    )
    if ok and r["id"] == "indent_alone":
        ok = r["adjacent"] <= 0.2 and r["per100"] <= 30.0
    return ok


# ── 발견기 (D-119): «어느 자리에 어떤 문자열이 편중되어 되풀이되는가» — 종류를 코드에 두지
# 않는다 ──
#
# 자리 셋: 행 첫머리(head) · 행 끝(tail) · 기호 뒤(after). 문자열은 접은 글자로 1~4자.
# 접기(fold)는 언어 지식이지 책의 규칙이 아니다: 한자 수사 → N, 天干 → G, 地支 → Z, 나머지는 그대로.
# 그래서 「二十八日」「十日」은 같은 꼴 «N日»이 되고, 「壬午」「辛巳」는 «GZ»가 된다.
# 무엇이 날짜이고 무엇이 卷頭인지는 찾은 뒤에 문법으로 «판정»만 한다(parse_date_head·volume_head).

from core.segmentation import _MAX_NGRAM  # noqa: E402

_STAGE3_KEEP = 0.6  # 3단에서 1위 점수의 이 비율 이상만 켠다(0.4는 문집에서 31가족을 켰다)
_STAGE3_MAX = 6  # …그리고 최대 여섯
_MIN_LIFT = 3.0  # 그 자리에 오는 빈도가 전문 어디서나 오는 빈도의 몇 배여야 «편중»인가
_NEST_KEEP = 0.8  # 긴 꼴이 짧은 꼴의 이 비율 이상 나오면 긴 꼴만 남긴다(「有詩」가 「有」를 설명)


def _has_unknown(s: str) -> bool:
    return _UNKNOWN in s


def _discover(texts: list[str]) -> dict[tuple[str, str], list[int]]:
    """자리 × 접은 문자열 → 나온 행 번호 목록. 넷 이상 나온 것만.

    head: 행 첫머리 1~4자. tail: 행 끝 1~4자(짧은 행이 아니어도 센다 — 편중은 lift가 가린다).
    after: 기호 뒤 1~4자 — 「○N日」의 «N日». 기호 자체는 (after, 기호) 가족으로 따로 센다.
    """
    fam: dict[tuple[str, str], set[int]] = collections.defaultdict(set)
    for k, t in enumerate(texts):
        f = fold_text(t)
        if not f:
            continue
        for n in range(1, min(_MAX_NGRAM, len(f)) + 1):
            h = f[:n]
            if not _has_unknown(h) and not h[0].isspace():
                fam[("head", h)].add(k)
            tl = f[-n:]
            if not _has_unknown(tl):
                fam[("tail", tl)].add(k)
        for i, ch in enumerate(t):
            if is_symbol_char(ch):
                fam[("sym", ch)].add(k)
                rest = fold_text(t[i + 1 : i + 1 + _MAX_NGRAM * 2])
                for n in range(1, min(_MAX_NGRAM, len(rest)) + 1):
                    a = rest[:n]
                    if not _has_unknown(a):
                        fam[("after", f"{ch}{a}")].add(k)
    return {key: sorted(pos) for key, pos in fam.items() if len(pos) >= _MIN_COUNT}


def _lift(texts_folded: list[str], key: tuple[str, str], count: int) -> float:
    """자리 편중 — 그 자리에 온 횟수 ÷ 우연히 그 자리에 올 기대 횟수.

    기대 횟수 = 전문 어디에나 나온 횟수 × (행 수 ÷ 글자 수). 「談草」가 본문에도 40번 나와도
    행 끝에 25번 몰려 있으면 기대(≈2)의 열 배가 넘는다. 기호 뒤는 자리 자체가 편중이라
    문턱값을 준다.
    """
    pos, st = key
    if pos in ("sym", "after"):
        return _MIN_LIFT
    anywhere = sum(t.count(st) for t in texts_folded)
    total_chars = sum(len(t) for t in texts_folded)
    expected = anywhere * len(texts_folded) / max(1, total_chars)
    return count / max(expected, 1e-9)


def _prune_nested(fams: dict[tuple[str, str], list[int]]) -> dict[tuple[str, str], list[int]]:
    """겹치는 꼴 정리 — 같은 자리에서 긴 꼴이 짧은 꼴의 대부분(80%↑)을 설명하면 짧은 꼴을 버린다.

    긴 꼴을 «짧은 꼴의 일부»라고 버리지는 않는다: 「N日」(33)은 「N」(119)의 28%뿐이지만 날짜라는
    뜻이 있고, 「N」은 편중이 없어 어차피 떨어진다(천진담초 실측 2026-09-07 — 그렇게 버렸더니 날짜가
    통째로 사라졌다). 작은 하위 꼴은 횟수·편중·점수가 걸러 낸다.
    """
    keep = dict(fams)
    for (pos, st), positions in fams.items():
        for (pos2, s2), positions2 in fams.items():
            if pos != pos2 or st == s2 or len(s2) <= len(st):
                continue
            longer = (pos in ("head", "after") and s2.startswith(st)) or (
                pos == "tail" and s2.endswith(st)
            )
            if longer and len(positions2) / max(1, len(positions)) >= _NEST_KEEP:
                keep.pop((pos, st), None)  # 긴 꼴이 설명한다
    return keep


_DATE_FOLD_CHARS = set("N月日初是同翌朔晦閏正臘")


def _repeat_fraction(texts: list[str], positions: list[int]) -> tuple[int, float]:
    """(날짜로 읽히는 행 수, 그 날짜들 중 서로 다른 날짜의 비율).

    판권·두주는 쪽마다 «같은 날짜»를 되적는다 — 天津談草 실측 2026-09-07: 판권 문구
    「同十一日」 13행이
    전부 11일. 표제라면 날짜가 행마다 다르다(「十一日海關署談草」「十二日海關署談草」).
    """
    dates = []
    for k in positions:
        h = parse_date_head(texts[k])
        if h.present and h.day is not None:
            dates.append((h.month, h.day))
    return len(dates), (len(set(dates)) / len(dates) if dates else 1.0)


def _classify(pos: str, s: str, texts: list[str], positions: list[int]) -> tuple[str, str, str]:
    """찾은 꼴을 규칙 칸에 배정한다 → (toggle, value, label). 문법 판정은 여기서만 한다.

    - 기호 자체: symbols
    - 기호 뒤/행 첫머리가 날짜 문법이면 signals.mark / signals.date
    - 행 끝이 卷 이름이면 signals.volume
    - 접힌 글자(N·G·Z)가 든 꼴은 템플릿(head_templates·tail_templates), 아니면
    어휘(head_words·title_words)
    """
    sample = [texts[k] for k in positions[:40]]
    if pos == "sym":
        return "symbols", s, f"기호 「{s}」"
    if pos == "after":
        sym, rest = s[0], s[1:]
        dated = 0
        for k in positions[:40]:
            nxt = texts[k + 1] if k + 1 < len(texts) else ""
            segs = [seg.strip() for seg in texts[k].split(sym)[1:]]
            if any(
                parse_date_head(seg).present or parse_wrapped_date_head(sym + seg, nxt).present
                for seg in segs
            ):
                dated += 1
        if dated / max(1, len(sample)) >= 0.8:
            return "signals.mark", "", f"기호 「{sym}」 뒤 날짜 ({rest})"
        return "symbols", sym, f"기호 「{sym}」 뒤 「{rest}」"
    if pos == "head":
        dated = sum(1 for t in sample if parse_date_head(t).present)
        if dated / max(1, len(sample)) >= 0.8:
            return "signals.date", "", f"행 첫머리 날짜 ({s})"
        if any(c in "NGZ" for c in s):
            return "head_templates", s, f"행 첫머리 꼴 「{s}」"
        return "head_words", s, f"행 첫머리 「{s}」"
    # tail
    if all(c in _DATE_FOLD_CHARS for c in s):
        return "drop", "", f"행 끝 날짜 꼴 ({s}) — 표제 규약이 아님"
    vol = sum(1 for t in sample if volume_head(t, 40) is not None)
    if vol / max(1, len(sample)) >= 0.8:
        return "signals.volume", "", f"행 끝 卷 이름 ({s})"
    if any(c in "NGZ" for c in s):
        return "tail_templates", s, f"행 끝 꼴 「{s}」"
    return "title_words", s, f"행 끝 「{s}」"


def induce_signals(
    lines: list[Line],
    rules: Optional[dict] = None,
    toc: Optional[dict] = None,
) -> dict:
    """행 목록에서 «자리 × 되풀이 문자열»을 찾아 층계로 이 책의 규약을 고른다. 저장하지 않는다.

    입력: 행 목록(빈 행 포함 가능), 규칙(max_title_chars만 쓴다),
          toc — toc_signal()의 결과(None이면 목차 없음). 목차 쪽은 세지 않는다.
    출력: {
      "lines", "median_len",
      "stage": {"level": 1|2|3|0, "name", "summary", "by": [결정한 신호 id]},
      "signals": [{"id","label","toggle","value","group","count","per100","gap_regularity",
                   "median_gap","chain","adjacent","lift","score","recommended","examples"} …]
                 group: visual(2단 — 기호·내려쓰기 단독)·primary(3단 — 텍스트 꼴)·aux(보조)
                 . 점수 내림차순,
      "dropped": [{"id","label","count","why"} …]  판심·편중 없음으로 버린 가족,
      "furniture": [원문 …], "toc": toc 그대로,
    }
    코드에 있는 «종류»는 자리 셋(행 첫머리·행 끝·기호 뒤)과 판식 물리(짧은 행·행갈음·
    내려쓰기)뿐이다.
    有詩·談草·○·N日은 전부 이 책에서 나온 값이다(D-119).
    """
    rules = normalize_rules(rules)
    toc_pages = set((toc or {}).get("pages") or [])
    lines = [ln for ln in lines if ln.text.strip() and ln.page not in toc_pages]
    n = len(lines)
    if n < 4:
        return {
            "lines": n,
            "median_len": 0,
            "stage": {
                "level": 0,
                "name": STAGE_NAMES[0],
                "summary": "행이 너무 적습니다",
                "by": [],
            },
            "signals": [],
            "dropped": [],
            "furniture": [],
            "toc": toc,
        }
    texts = [ln.text.strip() for ln in lines]
    lens = [len(t) for t in texts]
    median_len = statistics.median(lens)
    folded = [fold_text(t) for t in texts]

    # ── 텍스트 꼴 발견 → 분류 → 문법 스위치(날짜·권점+날짜·卷頭)로 판정된 꼴은 하나로 합친다 ──
    # 「翌日」「初N日」「同日」은 꼴은 달라도 다 «행 첫머리 날짜»다. 따로 두면 작은 가족 여럿이 되어
    # 규약이 흩어진다(운양집 실측 2026-09-07: 103회가 8·7회로 쪼개졌다).
    fams = _prune_nested(_discover(texts))
    rows: list[dict] = []
    dropped: list[dict] = []
    short_idx = [k for k, t in enumerate(texts) if len(t) <= max(4, median_len - 6)]
    short_set = set(short_idx)
    grammar: dict[str, tuple[set[int], list[str]]] = {}
    plain: list[tuple[str, str, list[int], str, str, str]] = []
    for (pos, st), positions in fams.items():
        toggle, value, label = _classify(pos, st, texts, positions)
        if toggle == "drop":
            dropped.append(
                {"id": f"{pos}:{st}", "label": label, "count": len(positions), "why": "date_tail"}
            )
            continue
        if toggle in ("signals.date", "signals.mark", "signals.volume"):
            acc = grammar.setdefault(toggle, (set(), []))
            acc[0].update(positions)
            acc[1].append(st)
        else:
            plain.append((pos, st, positions, toggle, value, label))
    merged_labels = {
        "signals.date": "행 첫머리 날짜",
        "signals.mark": "기호 뒤 날짜",
        "signals.volume": "행 끝 卷 이름",
    }
    entries: list[tuple[str, str, list[int], str, str, str, bool]] = []
    for toggle, (posset, forms) in grammar.items():
        key = toggle.split(".", 1)[1]
        shown = "·".join(sorted(set(forms), key=lambda f: -len(f))[:4])
        entries.append(
            (key, "", sorted(posset), toggle, "", f"{merged_labels[toggle]} ({shown})", True)
        )
    for pos, st, positions, toggle, value, label in plain:
        entries.append((pos, st, positions, toggle, value, label, False))
    for pos, st, positions, toggle, value, label, is_grammar in entries:
        count = len(positions)
        sid = pos if is_grammar else f"{pos}:{st}"
        lift = _MIN_LIFT if is_grammar else _lift(folded, (pos, st), count)
        if not is_grammar and pos in ("head", "tail") and lift < _MIN_LIFT:
            continue  # 자리에 편중되지 않은 흔한 글자(「之」「也」)
        if not is_grammar and _page_furniture(lines, positions):
            dropped.append({"id": sid, "label": label, "count": count, "why": "page_furniture"})
            continue
        reg, med = _gap_regularity(positions)
        chain = _chain_ok(lines, positions) if toggle in ("signals.date", "signals.mark") else None
        density = count / n * 100
        adjacent = _adjacent_fraction(positions)
        score = (
            math.log(count)
            * reg
            * (0.3 if density > _MAX_DENSITY else 1.0)
            * (1.0 if chain is None else 0.5 + 0.5 * chain)
            * (0.3 if adjacent > _MAX_ADJACENT else 1.0)
        )
        row = {
            "id": sid,
            "label": label,
            "toggle": toggle,
            "value": value,
            "group": "visual" if pos == "sym" else "primary",
            "count": count,
            "per100": round(density, 1),
            "gap_regularity": round(reg, 2),
            "median_gap": med,
            "chain": None if chain is None else round(chain, 2),
            "adjacent": round(adjacent, 2),
            "lift": round(lift, 1),
            "score": round(score, 2),
            "recommended": False,
            "examples": _examples(lines, positions),
        }
        if toggle in ("head_words", "head_templates"):
            # 행머리 꼴은 표제(짧은 행)에 편중돼야 규약이다 — 「天」이 天津談草와 본문
            # 「天下」에 같이 걸렸다
            row["short_frac"] = round(
                sum(1 for k in positions if k in short_set) / max(1, count), 2
            )
        if not is_grammar and pos in ("head", "tail"):
            # 표제는 행마다 다르다. 어휘·꼴 가족의 행들이 절반 이상 «같은 글»이거나, 날짜가
            # 있는데 서로
            # 다른 날짜가 절반이 안 되면 판권·두주·되풀이 표기다 — 天津談草 실측
            # 2026-09-07: 「同」 54행
            # (판권 문구 「同十一日」 13행이 전부 11일, 「同」 한 글자 25행). 기호 가족(sym·
            # after)은 본문이
            # 붙어 있어 이 판정을 하지 않는다.
            distinct = len({texts[k] for k in positions}) / max(1, count)
            row["distinct"] = round(distinct, 2)
            dated, date_distinct = _repeat_fraction(texts, positions)
            if distinct < 0.5 or (dated >= _MIN_COUNT and date_distinct < 0.5):
                row["marker"] = "repeat_text"
                row["label"] = label + " (같은 글·같은 날짜 되풀이 — 판권·두주?)"
        rows.append(row)

    # ── 판식 물리(보조) — 제안기와 같은 함수로 센다 ──
    layout = _layout_signals(lines, {**rules, "use_layout": True})
    aux_pos: dict[str, list[int]] = {}
    for sid in _AUX:
        aux_pos[sid] = [
            k for k, ln in enumerate(lines) if sid in layout.get((ln.page, ln.line_index), [])
        ]
        if not aux_pos[sid]:
            continue
        positions = aux_pos[sid]
        reg, med = _gap_regularity(positions)
        adjacent = _adjacent_fraction(positions)
        density = len(positions) / n * 100
        score = (
            math.log(len(positions))
            * reg
            * (0.3 if density > _MAX_DENSITY else 1.0)
            * (0.3 if adjacent > _MAX_ADJACENT else 1.0)
        )
        rows.append(
            {
                "id": sid,
                "label": SIGNAL_LABELS[sid],
                "toggle": f"signals.{sid}",
                "value": "",
                "group": "aux",
                "count": len(positions),
                "per100": round(density, 1),
                "gap_regularity": round(reg, 2),
                "median_gap": med,
                "chain": None,
                "adjacent": round(adjacent, 2),
                "lift": None,
                "score": round(score, 2),
                "recommended": True,
                "examples": _examples(lines, positions),
            }
        )
    if aux_pos.get("indent"):
        base = next(r for r in rows if r["id"] == "indent")
        rows.append(
            {
                **base,
                "id": "indent_alone",
                "label": SIGNAL_LABELS["indent_alone"],
                "toggle": "indent_alone",
                "group": "visual",
                "recommended": False,
            }
        )

    # ── 종이의 규약: 쪽마다 같은 자리에 같은 글로 되풀이되는 짧은 행 ──
    by_text: dict[str, list[int]] = collections.defaultdict(list)
    for k in short_idx:
        by_text[texts[k]].append(k)
    furniture = sorted(
        t
        for t, pos in by_text.items()
        if len(pos) >= 3 and not parse_date_head(t).present and _page_furniture(lines, pos)
    )

    rows.sort(key=lambda r: (-r["score"], -r["count"]))
    # 같은 자리에서 행이 80% 이상 겹치는 두 꼴(「有」와 「有詩」)은 점수 높은 쪽만 남긴다
    pos_of = {
        r["id"]: set(fams.get(tuple(r["id"].split(":", 1)), [])) for r in rows if ":" in r["id"]
    }
    redundant: set[str] = set()
    for i, r in enumerate(rows):
        if r["id"] in redundant or r["id"] not in pos_of or not pos_of[r["id"]]:
            continue
        for r2 in rows[i + 1 :]:
            if r2["id"] in redundant or r2["id"] not in pos_of or not pos_of[r2["id"]]:
                continue
            if r["id"].split(":", 1)[0] != r2["id"].split(":", 1)[0]:
                continue
            inter = len(pos_of[r["id"]] & pos_of[r2["id"]])
            if inter / len(pos_of[r2["id"]]) >= 0.8:
                redundant.add(r2["id"])
    rows = [r for r in rows if r["id"] not in redundant]

    # ── 층계 — 어느 단에서 멈추는가 ──
    stage_level = 0
    by: list[str] = []
    if toc and toc.get("decisive"):
        stage_level = 1
        by = ["toc"]
    else:
        visual = [r for r in rows if r["group"] == "visual" and _solid(r)]
        if visual:
            stage_level = 2
            top_v = visual[0]["score"]
            by = [r["id"] for r in visual if r["score"] >= 0.4 * top_v]
        else:
            primary = [r for r in rows if r["group"] == "primary" and _solid(r)]
            if primary:
                stage_level = 3
                top_p = primary[0]["score"]
                by = [r["id"] for r in primary if r["score"] >= _STAGE3_KEEP * top_p][:_STAGE3_MAX]
    by_set = set(by)
    labels = {r["id"]: r["label"] for r in rows}
    # 권점+날짜가 켜지면 «행 첫머리 날짜»도 켠다 — OCR이 ○를 빠뜨린 날짜 자리(D-115)
    mark_on = any(
        x["toggle"] == "signals.mark"
        and (x["id"] in by_set or (x["chain"] or 0) >= 0.8)
        and _solid(x)
        for x in rows
    )
    for r in rows:
        if r["group"] == "aux":
            r["recommended"] = True
        elif r["id"] in by_set:
            r["recommended"] = True
        elif (
            r["toggle"] in ("signals.date", "signals.mark")
            and stage_level in (2, 3)
            and _solid(r)
            and ((r["chain"] or 0) >= 0.8 or mark_on)
        ):
            r["recommended"] = (
                True  # 날짜 사슬이 또렷하면 함께 켠다 — OCR이 기호를 빠뜨린 자리를 메운다
            )
        elif r["toggle"] == "signals.volume" and stage_level in (2, 3):
            r["recommended"] = True  # 卷頭는 저자가 적은 구조라 어느 단에서도 켠다
    weak_toc = (
        f"목차 {toc['entries']}항목 중 {toc['matched']} 대조(약함) · "
        if toc and stage_level != 1
        else ""
    )
    if stage_level == 1:
        summary = f"목차 {toc['entries']}항목 중 {toc['matched']} 대조 — 목차가 이 책의 규약입니다"
    elif stage_level in (2, 3):
        counts = {r["id"]: r["count"] for r in rows}
        summary = (
            weak_toc
            + f"{STAGE_NAMES[stage_level]}: "
            + " · ".join(f"{labels[i]} {counts[i]}회" for i in by)
        )
    else:
        summary = weak_toc + "되풀이되는 표지를 찾지 못했습니다 — LLM에 묻거나 찍어 주세요"
    return {
        "lines": n,
        "median_len": median_len,
        "stage": {
            "level": stage_level,
            "name": STAGE_NAMES[stage_level],
            "summary": summary,
            "by": by,
        },
        "signals": rows,
        "dropped": dropped,
        "furniture": furniture,
        "toc": toc,
    }


def rules_from_signals(
    induced: dict,
    base_rules: Optional[dict] = None,
    enabled_ids: Optional[list[str]] = None,
) -> dict:
    """신호 목록(과 사람이 고른 것)으로 segmentation_rules를 만든다.

    입력: induce_signals() 결과, 바탕 규칙(억제·해제·글자수는 그대로 둔다),
          켤 신호 id 목록(None이면 recommended).
    출력: normalize_rules를 통과한 규칙. origin="induced".
    행마다 toggle이 «어느 칸»인지, value가 «무슨 값»인지 들고 있으므로 여기서는 옮겨 담기만 한다.
    """
    rules = normalize_rules(base_rules)
    chosen = (
        set(enabled_ids)
        if enabled_ids is not None
        else {r["id"] for r in induced.get("signals", []) if r.get("recommended")}
    )
    signals = {k: False for k in TOGGLE_SIGNALS}
    lists: dict[str, list[str]] = {
        "title_words": [],
        "head_words": [],
        "symbols": [],
        "head_templates": [],
        "tail_templates": [],
    }
    indent_alone = False
    listed: set[str] = set()
    for r in induced.get("signals", []):
        if r["toggle"].startswith("signals."):
            listed.add(r["toggle"].split(".", 1)[1])
        if r["id"] not in chosen:
            continue
        if r["toggle"] in lists:
            v = r.get("value") or ""
            if v and v not in lists[r["toggle"]]:
                lists[r["toggle"]].append(v)
        elif r["toggle"] == "indent_alone":
            indent_alone = True
        elif r["toggle"].startswith("signals."):
            signals[r["toggle"].split(".", 1)[1]] = True
    # 목록에 아예 없는 스위치(예: bbox가 없어 내려쓰기 0)는 끄지 않는다 — L2가 생기면 살아나야 한다
    for k in TOGGLE_SIGNALS:
        if k not in listed:
            signals[k] = True
    chose_primary = any(
        r["id"] in chosen and r["group"] in ("primary", "visual")
        for r in induced.get("signals", [])
    )
    if enabled_ids is None and not chose_primary:
        # 주 신호를 하나도 못 골랐으면(표본이 작거나 규약이 안 보이면) 주 신호는 기본값(켬)
        # 으로 둔다 —
        # 전부 끄면 제안이 아예 서지 않는다. 사람이 명시해서 껐으면(enabled_ids) 그대로 둔다.
        for k in _PRIMARY:
            signals[k] = True
    rules["signals"] = signals
    rules.update(lists)
    rules["indent_alone"] = indent_alone
    rules["furniture"] = list(induced.get("furniture", []))
    rules["origin"] = "induced"
    return normalize_rules(rules)


def induction_found_something(induced: dict) -> bool:
    """2·3단에서 규약을 골랐는가 — 자동 트리가 규칙을 «저장»할 조건.

    아무것도 못 찾았을 때 저장해 두면, 나중에 확정본이 늘어도 다시 세지 않는다.
    1단(목차)은 규칙에 적을 것이 없으므로 저장 조건이 아니다 — 목차는 제안 때마다 다시 본다.
    """
    return (induced.get("stage") or {}).get("level") in (2, 3)


def rules_are_empty(rules: Optional[dict]) -> bool:
    """아직 아무도(사람도 프로그램도) 규칙을 정하지 않았는가 — 자동 트리가 먼저 도출할 조건."""
    if not rules:
        return True
    return not (
        rules.get("origin")
        or rules.get("signals")
        or rules.get("title_words")
        or rules.get("head_words")
        or rules.get("symbols")
        or rules.get("indent_alone")
    )


# ── 4단: 통계가 못 찾은 책 — LLM에 시작 표지의 공통점을 묻는다 (계획 C) ─────────

START_PATTERN_SYSTEM_PROMPT = (
    "당신은 한문 고서의 판식을 읽는 편집자입니다. 주어진 행 표본만 보고, 이 책에서 "
    "«새 글이 시작하는 행»에 되풀이되는 표지를 찾아 JSON으로만 답하십시오. "
    "표본에 실제로 있는 글자만 답하고, "
    "없으면 kind를 none으로 하십시오."
)


# 표본 범위 (D-117 4단). 기본은 «시작 자리»다 — 표지가 표면에 되풀이되는 책은 그것으로 충분하고,
# 문맥이 있어야 보이는 표지(제목인지 시의 끝구인지)는 앞뒤 행, 자리에 규칙성이 없는 표지(행 중간의
# 「又」「按」)는 쪽 통째, 그래도 안 보이면 권 전체. 넓을수록 토큰을 많이 쓰므로 크기를 먼저 보인다.
SAMPLE_SCOPES = ("starts", "context", "pages", "all")
SAMPLE_SCOPE_LABELS = {
    "starts": "시작 자리 80줄",
    "context": "시작 자리 80줄 + 앞뒤 한 행",
    "pages": "6쪽 통째",
    "all": "권 전체",
}


def _even(items: list, limit: int) -> list:
    if len(items) <= limit:
        return items
    step = len(items) / limit
    return [items[int(i * step)] for i in range(limit)]


def sample_start_lines(
    lines: list[Line], rules: dict, limit: int = 80, scope: str = "starts"
) -> list[str]:
    """LLM에 보일 표본 — 짧은 행·행갈음 뒤의 행·내려쓴 행을 권 전체에서 고르게.

    왜 이 셋인가: 글의 시작은 별행 표제(짧은 행)이거나 행갈음 뒤의 첫 행이거나 내려쓴 행이다.
    본문 전체를 넘기면 토큰만 쓰고 신호는 묽어진다.

    scope — "starts"(기본)·"context"(앞뒤 한 행을 붙임: «앞 ／ ▶후보 ／ 뒤»)·"pages"(고르게 고른
    여섯 쪽의 행 전부, 쪽 머리 «— n쪽 —»)·"all"(권 전체, 쪽 머리 포함).
    출력: 모델에 보일 줄 목록.
    """
    lines = [ln for ln in lines if ln.text.strip()]
    if not lines:
        return []
    if scope in ("pages", "all"):
        pages = sorted({ln.page for ln in lines})
        chosen = set(pages) if scope == "all" else set(_even(pages, 6))
        out: list[str] = []
        cur = None
        for ln in lines:
            if ln.page not in chosen:
                continue
            if ln.page != cur:
                cur = ln.page
                out.append(f"— {cur}쪽 —")
            out.append(ln.text.strip())
        return out
    layout = _layout_signals(lines, {**rules, "use_layout": True})
    idx: list[int] = []
    seen: set[str] = set()
    for k, ln in enumerate(lines):
        sig = layout.get((ln.page, ln.line_index), [])
        if "short_line" in sig or "after_short" in sig or "indent" in sig:
            t = ln.text.strip()[:24]
            if t not in seen:
                seen.add(t)
                idx.append(k)
    if not idx:
        # 판식 신호가 하나도 없는 책(모든 행이 꽉 찬 산문) — 행 첫머리를 고르게 보인다
        idx = list(range(len(lines)))
    idx = _even(idx, limit)
    if scope == "context":
        out = []
        for k in idx:
            prev = lines[k - 1].text.strip()[:24] if k > 0 else ""
            nxt = lines[k + 1].text.strip()[:24] if k + 1 < len(lines) else ""
            out.append(f"{prev} ／ ▶{lines[k].text.strip()[:24]} ／ {nxt}")
        return out
    return [lines[k].text.strip()[:24] for k in idx]


def sample_size(lines: list[Line], rules: dict, scope: str = "starts") -> dict:
    """보내기 전에 크기를 알린다 — 줄 수·글자 수. 토큰 수는 모델마다 달라 적지 않는다."""
    sample = sample_start_lines(lines, rules, scope=scope)
    return {"scope": scope, "lines": len(sample), "chars": sum(len(s) for s in sample)}


def verify_pattern(lines: list[Line], kind: str, value: str) -> Optional[dict]:
    """LLM이 말한 표지가 전문에서 실제로 되풀이되는가 — 세어서 신호 행으로 돌려준다. 아니면 None.

    모델은 지어낸다. 표본에 있어 보여도 넷 미만이거나 간격이 고르지 않으면 규약이 아니다.
    """
    lines = [ln for ln in lines if ln.text.strip()]
    texts = [ln.text.strip() for ln in lines]
    if not value:
        return None
    if len(value) == 1 and is_symbol_char(value):
        kind = (
            "symbol"  # 모델이 «행머리 ○»라 해도 ○는 기호다 — 2단 가족으로 합친다(2026-09-07 실측)
        )
    if kind == "head_word":
        pos = [k for k, t in enumerate(texts) if t.startswith(value)]
        sid, toggle, label = f"head:{value}", "head_words", f"행 첫머리 「{value}」 (LLM 후보)"
    elif kind == "title_word":
        pos = [k for k, t in enumerate(texts) if t.endswith(value) and len(t) <= 40]
        sid, toggle, label = f"tail:{value}", "title_words", f"행 끝 「{value}」 (LLM 후보)"
    elif kind == "symbol" and len(value) == 1 and is_symbol_char(value):
        pos = [k for k, t in enumerate(texts) if value in t]
        sid, toggle, label = f"sym:{value}", "symbols", f"기호 「{value}」 (LLM 후보)"
    else:
        return None
    if len(pos) < _MIN_COUNT:
        return None
    reg, med = _gap_regularity(pos)
    density = len(pos) / max(1, len(texts)) * 100
    return {
        "id": sid,
        "label": label,
        "toggle": toggle,
        "value": value,
        "group": "visual" if kind == "symbol" else "primary",
        "count": len(pos),
        "per100": round(density, 1),
        "gap_regularity": round(reg, 2),
        "median_gap": med,
        "chain": None,
        "adjacent": round(_adjacent_fraction(pos), 2),
        "score": round(math.log(len(pos)) * reg, 2),
        "recommended": reg >= _MIN_REGULARITY and density <= _MAX_DENSITY,
        "examples": _examples(lines, pos),
        "llm": True,
    }


async def extract_start_patterns_llm(
    lines: list[Line],
    rules: dict,
    router,
    force_provider: Optional[str] = None,
    force_model: Optional[str] = None,
    reference_text: str = "",
    scope: str = "starts",
) -> tuple[list[dict], dict]:
    """4단 — 표본 행을 LLM에 보여 «시작 표지»를 정해진 종류로 답받고, 코드가 세어 확인한다.

    scope — sample_start_lines의 범위. 넓은 범위(쪽 통째·권 전체)에서는 표지가 행 중간에 있을 수
    있다고 모델에 말한다.

    출력: (확인된 신호 행 목록, {"provider","model","error","raw","note"}). 모델이 경계를 찍지
    않는다 — «규칙 후보»를 말하고, 그것이 전문에서 되풀이되는지는 verify_pattern이 정한다.
    """
    from core.toc import reference_excerpt

    meta: dict = {"provider": None, "model": None, "error": None, "raw": [], "note": ""}
    scope = scope if scope in SAMPLE_SCOPES else "starts"
    sample = sample_start_lines(lines, rules, scope=scope)
    meta["scope"] = scope
    meta["sample_lines"] = len(sample)
    meta["sample_chars"] = sum(len(s) for s in sample)
    if not sample:
        meta["error"] = "표본으로 삼을 짧은 행·행갈음 행이 없습니다."
        return [], meta
    if scope == "starts":
        intro = (
            "다음은 이 책에서 «글이 시작할 법한 자리»(짧은 행·행갈음 뒤의 행·내려쓴 행)의 "
            "표본입니다."
        )
    elif scope == "context":
        intro = (
            "다음은 이 책에서 «글이 시작할 법한 자리»의 표본입니다. "
            "한 줄이 «앞 행 ／ ▶후보 행 ／ 뒤 행»이고 "
            "▶가 붙은 행이 후보입니다."
        )
    else:
        intro = (
            "다음은 이 책의 쪽들을 통째로 옮긴 것입니다"
            "(«— n쪽 —»가 쪽 머리, 그다음 줄들이 그 쪽의 행). "
            "표지는 행 첫머리가 아니라 행 중간에 있을 수도 있습니다."
        )
    ref = ""
    if reference_text and reference_text.strip():
        ref = "해제(판단에만 쓸 것):\n" + reference_excerpt(reference_text, 4000) + "\n\n"
    prompt = (
        ref
        + intro
        + "\n"
        + "새 글의 시작 행에 되풀이되는 표지를 찾으십시오. 종류는 넷뿐입니다:\n"
        + "  head_word(행 첫머리 글자·어휘) · title_word(행을 끝맺는 어휘) · symbol(기호 한 글자)"
        + " · none(없음)\n"
        + '형식: {"patterns": [{"kind": "head_word", "value": "又", "why": "..."}], '
        + '"note": "..."}\n\n'
        + "\n".join(sample)
    )
    kwargs = {
        "system": START_PATTERN_SYSTEM_PROMPT,
        "response_format": "json",
        "max_tokens": 1024,
        "purpose": "segmentation_rules",
        "think": False,  # 목록 뽑기다 — 사고 예산을 쓸 일이 아니다(D-083)
    }
    if force_provider:
        kwargs["force_provider"] = force_provider
    if force_model:
        kwargs["force_model"] = force_model
    try:
        response = await router.call(prompt, **kwargs)
    except Exception as e:  # noqa: BLE001 — LLM이 없어도 찍는 길은 남아 있다
        meta["error"] = f"{type(e).__name__}: {e}"
        return [], meta
    meta["provider"] = getattr(response, "provider", None)
    meta["model"] = getattr(response, "model", None)
    data = lenient_json(getattr(response, "text", "") or "")
    if not isinstance(data, dict):
        meta["error"] = "JSON 응답을 해석할 수 없습니다."
        return [], meta
    out: list[dict] = []
    for p in (data.get("patterns") or [])[:8]:
        if not isinstance(p, dict):
            continue
        kind = str(p.get("kind") or "").strip()
        value = str(p.get("value") or "").strip()
        meta["raw"].append({"kind": kind, "value": value, "why": str(p.get("why") or "")[:200]})
        if kind == "none" or not value or len(value) > 8:
            continue
        row = verify_pattern(lines, kind, value)
        if row and all(r["id"] != row["id"] for r in out):
            out.append(row)
    meta["note"] = str(data.get("note") or "")[:500]
    return out, meta


# ── 행 수집 (L4, 없으면 L2) ─────────────────────────────────────────────


def collect_lines_any_layer(doc_path: str | Path, part_id: str) -> tuple[list[Line], dict]:
    """규칙 도출용 행 목록 — 확정본(L4)이 있는 쪽은 L4, 없는 쪽은 OCR 결과(L2)로 채운다.

    왜 섞는가: 규약을 세는 데는 글자 몇이 틀려도 된다. 그러나 제안·적용은 L4만 읽으므로
    (D-088), 결과의 source 셈을 화면이 알려 «L4를 채우면 제안이 더 잡힌다»고 말해야 한다.
    출력: (행 목록, {"l4_pages": n, "l2_pages": m, "pages_total": t})
    """
    from core.document import get_document_info
    from core.segmentation import _l2_line_boxes, _list_part_pages, collect_document_lines

    doc_path = Path(doc_path)
    pages = _list_part_pages(doc_path, part_id, get_document_info)
    lines, page_texts = collect_document_lines(doc_path, part_id, pages)
    l4_pages = set(page_texts)
    l2_pages = 0
    for page in pages:
        if page in l4_pages:
            continue
        boxes = _l2_line_boxes(doc_path, part_id, page)
        if not boxes:
            continue
        l2_pages += 1
        offset = 0
        for i, (bbox, direction, text) in enumerate(boxes):
            lines.append(
                Line(
                    page=page,
                    line_index=i,
                    text=text,
                    bbox=bbox,
                    char_start=offset,
                    writing_direction=direction,
                )
            )
            offset += len(text) + 1
    lines.sort(key=lambda ln: (ln.page, ln.line_index))
    return lines, {"l4_pages": len(l4_pages), "l2_pages": l2_pages, "pages_total": len(pages)}


def save_segmentation_rules(doc_path: str | Path, rules: Optional[dict]) -> Optional[dict]:
    """manifest.segmentation_rules를 스키마 검증 뒤 원자적으로 쓴다. None이면 지운다.

    라우터 둘(documents의 PUT·composition의 자동 트리)이 같은 길을 쓰도록 여기 둔다 —
    라우터 간 import는 금지(CLAUDE.md).
    """
    import json

    import jsonschema

    from core.document import get_document_info, write_json_atomic

    doc_path = Path(doc_path)
    manifest = get_document_info(doc_path)
    rules = normalize_rules(rules) if rules is not None else None
    manifest["segmentation_rules"] = rules
    schema_path = (
        Path(__file__).resolve().parent.parent.parent
        / "schemas"
        / "source_repo"
        / "manifest.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(manifest, schema)  # ValidationError는 호출한 쪽이 400으로 바꾼다
    write_json_atomic(doc_path / "manifest.json", manifest)
    return rules
