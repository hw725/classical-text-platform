"""전문에서 경계 규약을 스스로 찾는다 (D-116).

왜 필요한가:
    책마다 글의 시작 표지가 다르다 — 浩齋辰巳日錄은 「○+날짜」, 天津談草는 「날짜 … 談草」,
    문집은 목차와 卷頭. 이것을 코드에 책 이름으로 적어 두면 새 책이 올 때마다 코드를 고쳐야
    한다. 대신 «표지는 책 안에서 되풀이된다»는 사실만 코드에 두고, 어느 표지가 이 책의
    규약인지는 전문을 세어 **데이터**(manifest.segmentation_rules)로 저장한다.

원리:
    후보 «신호 가족»마다 전문에서 나온 자리를 모으고
      (1) 횟수 (2) 간격의 고름 — 연속 출현 사이 행 수가 «매 행»도 «한 번»도 아닌 것
      (3) 날짜 가족이면 날짜 사슬(날이 뒤로 가지 않는 비율)
    로 점수를 매긴다. «쪽마다 한 번, 같은 행 자리»에 되풀이되는 것(판심·엽수·서명)은
    글의 규약이 아니라 종이의 규약이라 버린다(page_furniture).

무엇을 하지 않는가:
    경계를 만들지 않는다. 결과는 신호 목록(횟수·점수·보기)과 그것으로 만든 규칙 초안이고,
    화면이 체크박스로 보여 준 뒤 사람이 고른 것만 저장한다. 제안·적용은 segmentation.py가
    저장된 규칙으로 한다 — 이 모듈은 규칙을 «찾는» 쪽이고, 규칙을 «쓰는» 쪽은 그쪽이다.

신호 가족과 규칙의 대응 (toggle 열):
    date         행 첫머리 날짜            → segmentation_rules.signals.date
    mark         ○+날짜 (행 어디서든)      → signals.mark
    volume       卷頭                      → signals.volume
    title_word:X 짧은 행을 끝맺는 어휘     → title_words 목록
    head_word:X  행 첫머리에 편중된 글자   → head_words 목록
    short_line·after_short·indent (보조)   → signals.* — 날짜·어휘가 있는 행의 신뢰도만 올린다
"""

from __future__ import annotations

import collections
import math
import statistics
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

# 사람이 읽는 이름. 코드 밖(화면)에서도 같은 말을 쓴다.
SIGNAL_LABELS: dict[str, str] = {
    "date": "날짜가 행 첫머리에",
    "mark": "○ 권점 + 날짜",
    "volume": "卷頭 (卷之一 …)",
    "short_line": "짧은 행 (보조)",
    "after_short": "행갈음 뒤의 행 (보조)",
    "indent": "내려쓰기 (보조)",
}

# 규칙 목록(title_words·head_words)이 아니라 켜고 끄는 신호. propose_boundaries가 읽는 키.
TOGGLE_SIGNALS = ("date", "mark", "volume", "short_line", "after_short", "indent")
_PRIMARY = ("date", "mark", "volume")  # 이것만으로 후보가 서는 신호
_AUX = ("short_line", "after_short", "indent")  # 다른 신호가 있는 행의 점수만 올리는 신호

# 날짜 문법의 글자 — 행머리 글자 편중(head_word)에서 뺀다. 날짜 가족과 겹치기 때문이다.
_DATE_CHARS = set(
    "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥一二三四五六七八九十廿卄卅正臘閏是初同翌朔晦"
)
_UNKNOWN = "□"  # OCR이 못 읽은 글자. 어휘 후보에서 뺀다 — 규약이 아니라 잡음이다


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


def induce_signals(lines: list[Line], rules: Optional[dict] = None) -> dict:
    """행 목록에서 신호 가족을 세고 점수를 매긴다. 아무것도 저장하지 않는다.

    입력: 행 목록(빈 행 포함 가능), 규칙(max_title_chars만 쓴다).
    출력: {
      "lines": 비어 있지 않은 행 수, "median_len": 행 길이 중앙값,
      "signals": [{"id","label","toggle","group","count","per100","gap_regularity",
                   "median_gap","chain","score","recommended","examples"} …]  점수 내림차순,
      "dropped": [{"id","label","count","why"} …]  판심 등으로 버린 가족,
      "furniture": [원문 …]  쪽마다 같은 자리에 같은 글로 되풀이되는 짧은 행,
    }
    """
    rules = normalize_rules(rules)
    lines = [ln for ln in lines if ln.text.strip()]
    n = len(lines)
    if n < 4:
        return {"lines": n, "median_len": 0, "signals": [], "dropped": [], "furniture": []}
    texts = [ln.text.strip() for ln in lines]
    lens = [len(t) for t in texts]
    median_len = statistics.median(lens)
    all_text = "".join(texts)

    families: dict[str, list[int]] = {}
    labels: dict[str, str] = {}
    toggles: dict[str, str] = {}
    groups: dict[str, str] = {}

    # ── 날짜·권점·卷頭 (문헌 무관 문법) ──
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
        families[sid] = pos
        labels[sid] = SIGNAL_LABELS[sid]
        toggles[sid] = f"signals.{sid}"
        groups[sid] = "primary"

    # ── 행 첫머리 글자의 편중(lift): 전문 어디서나 나오는 빈도 대비 첫머리에 얼마나 자주 오나 ──
    first = collections.Counter(t[0] for t in texts)
    overall = collections.Counter(all_text)
    for ch, cnt in first.most_common(60):
        if cnt < 5:
            break
        if ch in _DATE_CHARS or ch == _UNKNOWN or _MARK_RE.fullmatch(ch):
            continue
        expected = overall[ch] / max(1, len(all_text)) * n
        if cnt / max(expected, 1e-9) >= 3:
            sid = f"head_word:{ch}"
            families[sid] = [k for k, t in enumerate(texts) if t[0] == ch]
            labels[sid] = f"행 첫머리 「{ch}」"
            toggles[sid] = "head_words"
            groups[sid] = "primary"

    # ── 짧은 행의 끝 어휘(2자): 표제 어휘 후보. 짧은 행(≤ 중앙값-6)에서만 센다 ──
    short_idx = [k for k, t in enumerate(texts) if len(t) <= max(4, median_len - 6)]
    tail2 = collections.Counter(texts[k][-2:] for k in short_idx if len(texts[k]) >= 3)
    for w, cnt in tail2.most_common(30):
        if cnt < 4:
            break
        if _UNKNOWN in w or any(c in _DATE_CHARS for c in w) or _MARK_RE.search(w):
            continue
        sid = f"title_word:{w}"
        families[sid] = [k for k in short_idx if texts[k].endswith(w)]
        labels[sid] = f"짧은 행이 「{w}」로 끝남"
        toggles[sid] = "title_words"
        groups[sid] = "primary"

    # ── 형식 신호는 제안기와 같은 함수로 센다 — 화면의 횟수와 제안의 근거가 어긋나면 안 된다 ──
    layout = _layout_signals(lines, {**rules, "use_layout": True})
    for sid in _AUX:
        families[sid] = [
            k for k, ln in enumerate(lines) if sid in layout.get((ln.page, ln.line_index), [])
        ]
        labels[sid] = SIGNAL_LABELS[sid]
        toggles[sid] = f"signals.{sid}"
        groups[sid] = "aux"

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
    for sid, pos in families.items():
        pos = sorted(set(pos))
        count = len(pos)
        if count == 0:
            continue
        if groups[sid] == "primary" and sid not in _PRIMARY and _page_furniture(lines, pos):
            dropped.append(
                {"id": sid, "label": labels[sid], "count": count, "why": "page_furniture"}
            )
            continue
        reg, med = _gap_regularity(pos)
        chain = _chain_ok(lines, pos) if sid in ("date", "mark") else None
        density = count / n * 100
        # 점수: 되풀이(로그 횟수) × 간격 고름 × (밀도가 «매 행»에 가까우면 감점) × 날짜 사슬
        score = (
            math.log(count)
            * reg
            * (0.3 if density > 60 else 1.0)
            * (1.0 if chain is None else 0.5 + 0.5 * chain)
        )
        rows.append(
            {
                "id": sid,
                "label": labels[sid],
                "toggle": toggles[sid],
                "group": groups[sid],
                "count": count,
                "per100": round(density, 1),
                "gap_regularity": round(reg, 2),
                "median_gap": med,
                "chain": None if chain is None else round(chain, 2),
                "score": round(score, 2),
                "recommended": False,
                "examples": _examples(lines, pos),
            }
        )
    rows.sort(key=lambda r: (-r["score"], -r["count"]))

    # 권고: 주 신호는 «가장 센 것»과 그 40% 이상. 보조 신호는 셋 이상이면 켠다 —
    # 보조는 혼자 후보를 만들지 않으므로 켜 두어도 잘못 걸리는 것이 없다.
    top = max((r["score"] for r in rows if r["group"] == "primary"), default=0.0)
    short_set = set(short_idx)
    for r in rows:
        if r["group"] == "aux":
            # 보조는 혼자 후보를 만들지 않으니 나온 만큼은 켠다. 셋 미만이라고 끄면 세 쪽짜리
            # 표본에서 짧은 행 신호가 사라져 날짜 표제가 «긴 행» 감점으로 떨어진다(시험 실측).
            r["recommended"] = True
        else:
            r["recommended"] = r["count"] >= 4 and top > 0 and r["score"] >= 0.4 * top
        if r["toggle"] == "head_words":
            # 행머리 글자는 표제(짧은 행)에 편중돼야 규약이다. 「天」은 天津談草 표제와 본문의
            # 「天下」「天可以」에 같이 걸려 권고됐다(2026-09-07 실측) — 짧은 행 비율로 가른다.
            pos = families[r["id"]]
            r["short_frac"] = round(sum(1 for k in pos if k in short_set) / max(1, len(pos)), 2)
            if r["short_frac"] < 0.5:
                r["recommended"] = False
    return {
        "lines": n,
        "median_len": median_len,
        "signals": rows,
        "dropped": dropped,
        "furniture": furniture,
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
    for r in induced.get("signals", []):
        if r["id"] not in chosen:
            continue
        if r["toggle"] == "title_words":
            title_words.append(r["id"].split(":", 1)[1])
        elif r["toggle"] == "head_words":
            head_words.append(r["id"].split(":", 1)[1])
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
        r["id"] in chosen and r["group"] == "primary" for r in induced.get("signals", [])
    )
    if enabled_ids is None and not chose_primary:
        # 사람이 명시해서 껐으면(enabled_ids) 그대로 둔다 — 폴백은 자동 권고에만
        for k in _PRIMARY:
            signals[k] = True
    rules["signals"] = signals
    rules["title_words"] = title_words
    rules["head_words"] = head_words
    rules["furniture"] = list(induced.get("furniture", []))
    rules["origin"] = "induced"
    return normalize_rules(rules)


def induction_found_something(induced: dict) -> bool:
    """주 신호(날짜·권점·卷頭·어휘)를 하나라도 권고했는가 — 자동 트리가 규칙을 «저장»할 조건.

    아무것도 못 찾았을 때 저장해 두면, 나중에 확정본이 늘어도 다시 세지 않는다.
    """
    return any(s["recommended"] and s["group"] == "primary" for s in induced.get("signals", []))


def rules_are_empty(rules: Optional[dict]) -> bool:
    """아직 아무도(사람도 프로그램도) 규칙을 정하지 않았는가 — 자동 트리가 먼저 도출할 조건."""
    if not rules:
        return True
    return not (
        rules.get("origin")
        or rules.get("signals")
        or rules.get("title_words")
        or rules.get("head_words")
    )


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
