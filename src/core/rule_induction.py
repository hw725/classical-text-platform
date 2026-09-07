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
    _MARK_RE,
    Line,
    _layout_signals,
    _line_candidates,
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

# 규칙 목록(title_words·head_words·symbols)이 아니라 켜고 끄는 신호. propose_boundaries가 읽는 키.
TOGGLE_SIGNALS = ("date", "mark", "volume", "short_line", "after_short", "indent")
_PRIMARY = ("date", "mark", "volume")  # 이것만으로 후보가 서는 텍스트 신호
_AUX = ("short_line", "after_short", "indent")  # 다른 신호가 있는 행의 점수만 올리는 신호

# 날짜 문법의 글자 — 행머리 글자 편중(head_word)에서 뺀다. 날짜 가족과 겹치기 때문이다.
_DATE_CHARS = set(
    "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥一二三四五六七八九十廿卄卅正臘閏是初同翌朔晦"
)
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


def induce_signals(
    lines: list[Line],
    rules: Optional[dict] = None,
    toc: Optional[dict] = None,
) -> dict:
    """행 목록에서 신호 가족을 세고 층계로 이 책의 규약을 고른다. 아무것도 저장하지 않는다.

    입력: 행 목록(빈 행 포함 가능), 규칙(max_title_chars만 쓴다),
          toc — toc_signal()의 결과(None이면 목차 없음). 목차 쪽은 세지 않는다.
    출력: {
      "lines": 비어 있지 않은 행 수, "median_len": 행 길이 중앙값,
      "stage": {"level": 1|2|3|0, "name", "summary", "by": [결정한 신호 id]},
      "signals": [{"id","label","toggle","group","count","per100","gap_regularity",
                   "median_gap","chain","adjacent","score","recommended","examples"} …]
                 group: visual(2단)·primary(3단)·aux(보조). 점수 내림차순,
      "dropped": [{"id","label","count","why"} …]  판심 등으로 버린 가족,
      "furniture": [원문 …]  쪽마다 같은 자리에 같은 글로 되풀이되는 짧은 행,
      "toc": toc 그대로,
    }
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
    all_text = "".join(texts)

    families: dict[str, list[int]] = {}
    labels: dict[str, str] = {}
    toggles: dict[str, str] = {}
    groups: dict[str, str] = {}

    def add(sid: str, pos: list[int], label: str, toggle: str, group: str) -> None:
        families[sid] = pos
        labels[sid] = label
        toggles[sid] = toggle
        groups[sid] = group

    # ── 2단 후보: 되풀이되는 기호(○뿐 아니라 한자가 아닌 글자 전부) ──
    sym_lines: dict[str, list[int]] = collections.defaultdict(list)
    for k, t in enumerate(texts):
        for ch in set(t):
            if is_symbol_char(ch):
                sym_lines[ch].append(k)
    for ch, pos in sym_lines.items():
        if len(pos) >= _MIN_COUNT:
            add(f"symbol:{ch}", pos, f"기호 「{ch}」", "symbols", "visual")

    # ── 3단 후보: 날짜·권점·卷頭 (문헌 무관 문법) ──
    date_pos, mark_pos, vol_pos = [], [], []
    seen_volumes: set[str] = set()
    for k, t in enumerate(texts):
        nxt = texts[k + 1] if k + 1 < n else ""
        cands = _line_candidates(t, nxt)
        head0 = parse_date_head(t)
        if not head0.present:
            # 「…○三十|日雨…」 — 제안기(propose_boundaries)와 같은 눈으로 본다(D-115)
            head0 = parse_wrapped_date_head(t, nxt)
        marked = any(off > 0 for off, _c in cands) or (head0.present and head0.mark)
        if not marked and head0.present and k > 0 and _MARK_RE.fullmatch(texts[k - 1][-1]):
            marked = True  # 「…事○|八日啓…」 — 표지가 앞 열 끝에 남은 꼴
        if marked:
            mark_pos.append(k)
        elif head0.present:
            date_pos.append(k)
        vol = volume_head(t, rules["max_title_chars"])
        if vol is not None and vol not in seen_volumes:
            seen_volumes.add(vol)
            vol_pos.append(k)
    for sid, pos in (("date", date_pos), ("mark", mark_pos), ("volume", vol_pos)):
        add(sid, pos, SIGNAL_LABELS[sid], f"signals.{sid}", "primary")

    # ── 3단 후보: 행 첫머리 글자의 편중(lift) ──
    first = collections.Counter(t[0] for t in texts)
    overall = collections.Counter(all_text)
    for ch, cnt in first.most_common(60):
        if cnt < 5:
            break
        if ch in _DATE_CHARS or ch == _UNKNOWN or _MARK_RE.fullmatch(ch) or is_symbol_char(ch):
            continue
        expected = overall[ch] / max(1, len(all_text)) * n
        if cnt / max(expected, 1e-9) >= 3:
            add(
                f"head_word:{ch}",
                [k for k, t in enumerate(texts) if t[0] == ch],
                f"행 첫머리 「{ch}」",
                "head_words",
                "primary",
            )

    # ── 3단 후보: 짧은 행의 끝 어휘(2자) — 짧은 행(≤ 중앙값-6)에서만 센다 ──
    short_idx = [k for k, t in enumerate(texts) if len(t) <= max(4, median_len - 6)]
    tail2 = collections.Counter(texts[k][-2:] for k in short_idx if len(texts[k]) >= 3)
    for w, cnt in tail2.most_common(30):
        if cnt < _MIN_COUNT:
            break
        if _UNKNOWN in w or any(c in _DATE_CHARS for c in w) or any(is_symbol_char(c) for c in w):
            continue
        add(
            f"title_word:{w}",
            [k for k in short_idx if texts[k].endswith(w)],
            f"짧은 행이 「{w}」로 끝남",
            "title_words",
            "primary",
        )

    # ── 형식 신호는 제안기와 같은 함수로 센다 — 화면의 횟수와 제안의 근거가 어긋나면 안 된다 ──
    layout = _layout_signals(lines, {**rules, "use_layout": True})
    for sid in _AUX:
        add(
            sid,
            [k for k, ln in enumerate(lines) if sid in layout.get((ln.page, ln.line_index), [])],
            SIGNAL_LABELS[sid],
            f"signals.{sid}",
            "aux",
        )
    # 내려쓰기는 2단(시각)에서 «혼자 경계를 만드는» 후보이기도 하다
    if families["indent"]:
        add(
            "indent_alone",
            list(families["indent"]),
            SIGNAL_LABELS["indent_alone"],
            "indent_alone",
            "visual",
        )

    # ── 종이의 규약: 쪽마다 같은 자리에 같은 글로 되풀이되는 짧은 행 ──
    by_text: dict[str, list[int]] = collections.defaultdict(list)
    for k in short_idx:
        by_text[texts[k]].append(k)
    # 날짜로 시작하는 행은 뺀다 — 두주(頭註)의 날짜가 쪽마다 같은 자리에 오지만, 그것은 날짜
    # 사슬(same_day_repeat)이 다루고, 진짜 회차의 「同十一日」까지 잃으면 안 된다(天津談草 실측).
    furniture = sorted(
        t
        for t, pos in by_text.items()
        if len(pos) >= 3 and not parse_date_head(t).present and _page_furniture(lines, pos)
    )

    rows = []
    dropped = []
    short_set = set(short_idx)
    for sid, pos in families.items():
        pos = sorted(set(pos))
        count = len(pos)
        if count == 0:
            continue
        if groups[sid] != "aux" and sid not in _PRIMARY and _page_furniture(lines, pos):
            dropped.append(
                {"id": sid, "label": labels[sid], "count": count, "why": "page_furniture"}
            )
            continue
        reg, med = _gap_regularity(pos)
        chain = _chain_ok(lines, pos) if sid in ("date", "mark") else None
        density = count / n * 100
        adjacent = _adjacent_fraction(pos)
        # 점수: 되풀이(로그 횟수) × 간격 고름 × (밀도가 «매 행»에 가까우면 감점) × 날짜 사슬
        #       × (줄지어 오면 감점 — 표지가 아니라 덩어리다)
        score = (
            math.log(count)
            * reg
            * (0.3 if density > _MAX_DENSITY else 1.0)
            * (1.0 if chain is None else 0.5 + 0.5 * chain)
            * (0.3 if adjacent > _MAX_ADJACENT else 1.0)
        )
        row = {
            "id": sid,
            "label": labels[sid],
            "toggle": toggles[sid],
            "group": groups[sid],
            "count": count,
            "per100": round(density, 1),
            "gap_regularity": round(reg, 2),
            "median_gap": med,
            "chain": None if chain is None else round(chain, 2),
            "adjacent": round(adjacent, 2),
            "score": round(score, 2),
            "recommended": False,
            "examples": _examples(lines, pos),
        }
        if toggles[sid] == "head_words":
            # 행머리 글자는 표제(짧은 행)에 편중돼야 규약이다. 「天」은 天津談草 표제와 본문의
            # 「天下」「天可以」에 같이 걸려 권고됐다(2026-09-07 실측) — 짧은 행 비율로 가른다.
            row["short_frac"] = round(sum(1 for k in pos if k in short_set) / max(1, count), 2)
        rows.append(row)
    rows.sort(key=lambda r: (-r["score"], -r["count"]))

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
                by = [r["id"] for r in primary if r["score"] >= 0.4 * top_p]

    # 권고: 결정한 단의 신호와, 그보다 «확실한» 텍스트 신호(날짜 사슬 0.8 이상)는 켠다.
    # 보조는 혼자 후보를 만들지 않으니 나온 만큼 켠다 — 셋 미만이라고 끄면 세 쪽짜리 표본에서
    # 짧은 행 신호가 사라져 날짜 표제가 «긴 행» 감점으로 떨어진다(시험 실측).
    by_set = set(by)
    for r in rows:
        if r["group"] == "aux":
            r["recommended"] = True
        elif r["id"] in by_set:
            r["recommended"] = True
        elif (
            r["id"] in ("date", "mark")
            and stage_level in (2, 3)
            and _solid(r)
            and (r["chain"] or 0) >= 0.8
        ):
            # 시각 신호가 규약이어도 날짜 사슬이 또렷하면 함께 켠다 —
            # OCR이 기호를 빠뜨린 자리를 메운다
            r["recommended"] = True
        elif r["id"] == "volume" and stage_level in (2, 3):
            r["recommended"] = True  # 卷頭는 저자가 적은 구조라 어느 단에서도 켠다

    weak_toc = (
        f"목차 {toc['entries']}항목 중 {toc['matched']} 대조(약함) · "
        if toc and stage_level != 1
        else ""
    )
    if stage_level == 1:
        summary = f"목차 {toc['entries']}항목 중 {toc['matched']} 대조 — 목차가 이 책의 규약입니다"
    elif stage_level in (2, 3):
        summary = (
            weak_toc
            + f"{STAGE_NAMES[stage_level]}: "
            + " · ".join(f"{labels[i]} {len(set(families[i]))}회" for i in by)
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
    """
    rules = normalize_rules(base_rules)
    chosen = (
        set(enabled_ids)
        if enabled_ids is not None
        else {r["id"] for r in induced.get("signals", []) if r.get("recommended")}
    )
    signals = {k: False for k in TOGGLE_SIGNALS}
    title_words: list[str] = []
    head_words: list[str] = []
    symbols: list[str] = []
    indent_alone = False
    for r in induced.get("signals", []):
        if r["id"] not in chosen:
            continue
        if r["toggle"] == "title_words":
            title_words.append(r["id"].split(":", 1)[1])
        elif r["toggle"] == "head_words":
            head_words.append(r["id"].split(":", 1)[1])
        elif r["toggle"] == "symbols":
            symbols.append(r["id"].split(":", 1)[1])
        elif r["toggle"] == "indent_alone":
            indent_alone = True
        elif r["toggle"].startswith("signals."):
            signals[r["toggle"].split(".", 1)[1]] = True
    # 목록에 아예 없는 보조 신호(예: bbox가 없어 내려쓰기 0)는 끄지 않는다 — 나중에 L2가
    # 생기면 저절로 살아나야 한다.
    listed = {
        r["toggle"].split(".", 1)[1]
        for r in induced.get("signals", [])
        if r["toggle"].startswith("signals.")
    }
    for k in TOGGLE_SIGNALS:
        if k not in listed:
            signals[k] = True
    # 주 신호를 하나도 못 골랐으면(표본이 작거나 규약이 안 보이면) 주 신호는 기본값(켬)으로 둔다 —
    # 전부 끄면 제안이 아예 서지 않는다. 세 쪽짜리 시험 문헌에서 그랬다.
    chose_primary = any(
        r["id"] in chosen and r["group"] in ("primary", "visual")
        for r in induced.get("signals", [])
    )
    if enabled_ids is None and not chose_primary:
        # 사람이 명시해서 껐으면(enabled_ids) 그대로 둔다 — 폴백은 자동 권고에만
        for k in _PRIMARY:
            signals[k] = True
    rules["signals"] = signals
    rules["title_words"] = title_words
    rules["head_words"] = head_words
    rules["symbols"] = symbols
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


def sample_start_lines(lines: list[Line], rules: dict, limit: int = 80) -> list[str]:
    """LLM에 보일 표본 — 짧은 행·행갈음 뒤의 행·내려쓴 행을 권 전체에서 고르게.

    왜 이 셋인가: 글의 시작은 별행 표제(짧은 행)이거나 행갈음 뒤의 첫 행이거나 내려쓴 행이다.
    본문 전체를 넘기면 토큰만 쓰고 신호는 묽어진다.
    """
    lines = [ln for ln in lines if ln.text.strip()]
    layout = _layout_signals(lines, {**rules, "use_layout": True})
    picks: list[str] = []
    seen: set[str] = set()
    for ln in lines:
        sig = layout.get((ln.page, ln.line_index), [])
        if "short_line" in sig or "after_short" in sig or "indent" in sig:
            t = ln.text.strip()[:24]
            if t not in seen:
                seen.add(t)
                picks.append(t)
    if not picks:
        # 판식 신호가 하나도 없는 책(모든 행이 꽉 찬 산문) — 행 첫머리를 고르게 보인다
        picks = [ln.text.strip()[:24] for ln in lines]
    if len(picks) <= limit:
        return picks
    step = len(picks) / limit
    return [picks[int(i * step)] for i in range(limit)]


def verify_pattern(lines: list[Line], kind: str, value: str) -> Optional[dict]:
    """LLM이 말한 표지가 전문에서 실제로 되풀이되는가 — 세어서 신호 행으로 돌려준다. 아니면 None.

    모델은 지어낸다. 표본에 있어 보여도 넷 미만이거나 간격이 고르지 않으면 규약이 아니다.
    """
    lines = [ln for ln in lines if ln.text.strip()]
    texts = [ln.text.strip() for ln in lines]
    if not value:
        return None
    if kind == "head_word":
        pos = [k for k, t in enumerate(texts) if t.startswith(value)]
        sid, toggle, label = f"head_word:{value}", "head_words", f"행 첫머리 「{value}」 (LLM 후보)"
    elif kind == "title_word":
        pos = [k for k, t in enumerate(texts) if t.endswith(value) and len(t) <= 40]
        sid, toggle, label = f"title_word:{value}", "title_words", f"「{value}」로 끝남 (LLM 후보)"
    elif kind == "symbol" and len(value) == 1 and is_symbol_char(value):
        pos = [k for k, t in enumerate(texts) if value in t]
        sid, toggle, label = f"symbol:{value}", "symbols", f"기호 「{value}」 (LLM 후보)"
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
) -> tuple[list[dict], dict]:
    """4단 — 표본 행을 LLM에 보여 «시작 표지»를 정해진 종류로 답받고, 코드가 세어 확인한다.

    출력: (확인된 신호 행 목록, {"provider","model","error","raw","note"}). 모델이 경계를 찍지
    않는다 — «규칙 후보»를 말하고, 그것이 전문에서 되풀이되는지는 verify_pattern이 정한다.
    """
    from core.toc import reference_excerpt

    meta: dict = {"provider": None, "model": None, "error": None, "raw": [], "note": ""}
    sample = sample_start_lines(lines, rules)
    if not sample:
        meta["error"] = "표본으로 삼을 짧은 행·행갈음 행이 없습니다."
        return [], meta
    ref = ""
    if reference_text and reference_text.strip():
        ref = "해제(판단에만 쓸 것):\n" + reference_excerpt(reference_text, 4000) + "\n\n"
    prompt = (
        ref
        + "다음은 이 책에서 «글이 시작할 법한 자리»(짧은 행·행갈음 뒤의 행·내려쓴 행)의 "
        + "표본입니다.\n"
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
