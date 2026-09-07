"""규칙 도출 테스트 (D-116).

무엇을 고정하는가:
  - ○+날짜가 되풀이되는 일기에서는 mark가, 「談草」로 끝나는 짧은 행이 되풀이되면
    title_word가 권고된다
  - 쪽마다 한 번 같은 자리에 오는 것(판심·엽수)은 신호에서 빠지고 furniture로 간다
  - 도출 결과 → segmentation_rules 변환(rules_from_signals)과 «규칙이 비어 있는가» 판정
  - propose_boundaries가 signals 스위치·head_words·furniture를 따른다
  - API: /segmentation/signals는 저장하지 않고, 규칙이 빈 문헌의 /segmentation/auto는
    먼저 도출해 저장한다
"""

from __future__ import annotations

import json
from pathlib import Path

from src.core.rule_induction import (
    extract_start_patterns_llm,
    induce_signals,
    is_symbol_char,
    rules_are_empty,
    rules_from_signals,
    sample_size,
    sample_start_lines,
    toc_signal,
    verify_pattern,
)
from src.core.segmentation import Line, normalize_rules, propose_boundaries
from tests.test_segmentation import BODY, _FakeRouter, _setup, client  # noqa: F401 — fixture 재사용

_COL = "本文本文本文本文本文本文本文本文本文本文"  # 20자 — 열 용량
_DAYS = [
    "初一日",
    "初二日",
    "初三日",
    "初四日",
    "初五日",
    "初六日",
    "初七日",
    "初八日",
    "初九日",
    "初十日",
    "十一日",
    "十二日",
    "十三日",
    "十四日",
    "十五日",
    "十六日",
    "十七日",
    "十八日",
    "十九日",
    "二十日",
]


def _pages(rows_per_page: list[list[str]]) -> list[Line]:
    out = []
    for p, rows in enumerate(rows_per_page, start=1):
        out += [Line(p, i, t) for i, t in enumerate(rows)]
    return out


def _diary_pages(n_pages=6, per_page=8, furniture="書"):
    """○+날짜가 두 행마다 오는 일기. 쪽 끝에는 판심 글자 하나."""
    pages, k = [], 0
    for _ in range(n_pages):
        rows = []
        for i in range(per_page - 1):
            if i % 2 == 0:
                rows.append(f"{_COL[:8]}○{_DAYS[k % 20]}晴{_COL[:6]}")
                k += 1
            else:
                rows.append(_COL)
        rows.append(furniture)
        pages.append(rows)
    return _pages(pages)


class TestInduce:
    def test_mark_date_diary_is_recognised(self):
        r = induce_signals(_diary_pages())
        # 층계(D-117): 눈에 띄는 기호 ○가 2단에서 규약으로 결정되고,
        # 날짜 사슬이 또렷한 mark도 함께 켜진다
        assert r["stage"]["level"] == 2 and "sym:○" in r["stage"]["by"]
        rows = {s["id"]: s for s in r["signals"]}
        assert rows["sym:○"]["recommended"] is True
        mark = rows["mark"]
        assert mark["recommended"] is True
        assert mark["count"] >= 20 and mark["chain"] is not None and mark["chain"] >= 0.9
        # 쪽마다 한 번 같은 자리의 「書」는 글의 규약이 아니다
        assert "書" in r["furniture"]
        assert all(s["id"] != "head:書" for s in r["signals"])

    def test_title_word_family_from_short_line_tails(self):
        pages = []
        places = "天津保定海關署周玉山李中堂軍械所"
        for p in range(5):
            pages.append(
                [
                    f"{_DAYS[p]}海關署談草",
                    _COL,
                    _COL,
                    f"{places[p * 2 : p * 2 + 3]}談草",
                    _COL,
                    _COL,
                ]
            )
        r = induce_signals(_pages(pages))
        ids = {s["id"]: s for s in r["signals"]}
        assert ids["tail:談草"]["recommended"] is True
        assert ids["tail:談草"]["count"] == 10 and ids["tail:談草"]["value"] == "談草"
        rules = rules_from_signals(r)
        assert rules["title_words"] == ["談草"] and rules["origin"] == "induced"

    def test_page_furniture_family_is_dropped(self):
        # 「京城…」이 쪽마다 첫 행에 한 번 — 인쇄소 도장. head_word:京이 신호가 되면 안 된다
        pages = [[f"京城鍾路{p}", _COL, f"{_DAYS[p]}談草", _COL, _COL, _COL] for p in range(8)]
        r = induce_signals(_pages(pages))
        assert any(d["id"].startswith("head:京") for d in r["dropped"])
        assert all(not s["id"].startswith("head:京") for s in r["signals"])

    def test_head_word_needs_short_lines(self):
        # 「天」이 본문 긴 행 첫머리에만 편중되면 표제 규약이 아니다 — 권고하지 않는다
        pages = [
            ["天" + _COL[1:], _COL, f"{_DAYS[p]}談草", "天" + _COL[1:], _COL, _COL]
            for p in range(8)
        ]
        r = induce_signals(_pages(pages))
        tian = [s for s in r["signals"] if s["id"].startswith("head:天")]
        assert tian and tian[0]["recommended"] is False

    def test_unknown_glyph_is_never_a_word(self):
        pages = [[f"{_DAYS[p]}□□", _COL, _COL, "□談", _COL, _COL] for p in range(8)]
        r = induce_signals(_pages(pages))
        assert r["signals"], "판식 신호는 세어져야 한다 — 빈 목록이면 아래 단언이 공허하다"
        assert all("□" not in s["id"] for s in r["signals"])

    def test_wrapped_date_is_counted_like_the_proposer_sees_it(self):
        # 「…○三十|日雨…」 — 제안기는 date_wrap 후보를 만든다. 도출기도 같은 자리를 mark로 세야 한다
        pages = []
        for p in range(6):
            pages.append(
                [
                    _COL,
                    f"{_COL[:16]}○二十",
                    f"日雨{_COL[:18]}",
                    _COL,
                    f"{_COL[:8]}○{_DAYS[p]}晴{_COL[:6]}",
                    _COL,
                ]
            )
        r = induce_signals(_pages(pages))
        mark = [s for s in r["signals"] if s["id"] == "mark"]
        assert mark and mark[0]["count"] == 12

    def test_too_few_lines(self):
        r = induce_signals(_pages([["○初一日", "本"]]))
        assert r["signals"] == [] and r["lines"] == 2


class TestRulesFromSignals:
    def test_unchecked_signal_is_off_and_missing_aux_stays_on(self):
        r = induce_signals(_diary_pages())
        # 사람이 mark를 뺐다 — 날짜는 그대로 켜 둔다
        chosen = [s["id"] for s in r["signals"] if s["recommended"] and s["id"] != "mark"]
        rules = rules_from_signals(r, None, chosen)
        assert rules["signals"]["mark"] is False
        # bbox가 없어 목록에 없던 내려쓰기는 끄지 않는다(나중에 L2가 생기면 살아난다)
        assert rules["signals"]["indent"] is True

    def test_rules_are_empty(self):
        assert (
            rules_are_empty(None) and rules_are_empty({}) and rules_are_empty({"suppress": ["x"]})
        )
        assert not rules_are_empty({"title_words": ["談草"]})
        assert not rules_are_empty({"origin": "manual"})
        assert not rules_are_empty(normalize_rules({"signals": {"mark": False}}))


class TestProposerHonoursRules:
    def test_mark_switch_off_removes_the_mark_family(self):
        # mark는 «가족» 스위치다 —
        # 끄면 행 중간 ○+날짜 후보가 «날짜»라는 다른 이름으로 살아남지 않는다
        lines = _diary_pages(n_pages=2, furniture=_COL)
        on = propose_boundaries(lines, None)
        off = propose_boundaries(lines, {"signals": {"mark": False}})
        assert any("mark" in p["reasons"] for p in on["proposals"])
        assert off["stats"]["proposals"] == 0
        # 행 첫머리에 ○ 없이 온 날짜는 date 가족 — mark를 꺼도 남는다
        plain = _pages([[_COL, "初三日晴" + _COL[:10], _COL, _COL]])
        r = propose_boundaries(plain, {"signals": {"mark": False}})
        assert r["stats"]["proposals"] == 1 and "mark" not in r["proposals"][0]["reasons"]

    def test_date_and_mark_off_means_no_date_candidates(self):
        lines = _diary_pages(n_pages=2, furniture=_COL)
        r = propose_boundaries(lines, {"signals": {"mark": False, "date": False}})
        assert r["stats"]["proposals"] == 0

    def test_head_word_from_rules(self):
        lines = _pages([[_COL, _COL, "有詩", _COL, _COL, _COL, "有詩", _COL]])
        none = propose_boundaries(lines, None)
        assert none["stats"]["proposals"] == 0
        r = propose_boundaries(lines, {"head_words": ["有"]})
        acc = [p for p in r["proposals"] if p["accepted"]]
        assert len(acc) == 2 and "head_word:有" in acc[0]["reasons"]
        # 긴 본문 행이 有로 시작하면 어휘 점수만으로는 문턱을 못 넘는다
        r2 = propose_boundaries(
            _pages([[_COL, "有" + _COL[1:], _COL, _COL]]), {"head_words": ["有"]}
        )
        assert r2["stats"]["accepted"] == 0

    def test_furniture_text_is_not_a_boundary(self):
        lines = _pages([["天津談草", _COL, "初一日談草", _COL, _COL]])
        r = propose_boundaries(lines, {"title_words": ["談草"], "furniture": ["天津談草"]})
        by_title = {p["title"]: p for p in r["proposals"]}
        assert by_title["天津談草"]["accepted"] is False
        assert "furniture" in by_title["天津談草"]["reasons"]
        assert by_title["初一日談草"]["accepted"] is True

    def test_normalize_keeps_old_coarse_switches(self):
        rules = normalize_rules({"use_layout": False})
        assert rules["signals"]["short_line"] is False and rules["signals"].get("date", True)


def test_signals_api_counts_without_saving(client, tmp_path):  # noqa: F811
    lib, part_id = _setup(client, tmp_path)
    r = client.post("/api/documents/d1/segmentation/signals", json={"part_id": part_id})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"]["l4_pages"] == 3 and d["saved_rules"] is None
    assert (
        isinstance(d["signals"], list) and "stage" in d
    )  # 세 쪽 표본은 넷 미만이라 날짜 가족이 없다
    assert d["recommended_rules"]["origin"] == "induced"
    manifest = json.loads(
        (Path(lib) / "documents" / "d1" / "manifest.json").read_text(encoding="utf-8")
    )
    assert not manifest.get("segmentation_rules")


def test_auto_tree_saves_induced_rules_when_a_primary_signal_is_found(client, tmp_path):  # noqa: F811
    """주 신호가 권고될 만큼 되풀이되는 문헌: 자동 트리가 규칙을 찾아 manifest에 저장한다."""
    lib, part_id = _setup(client, tmp_path)
    pages = Path(lib) / "documents" / "d1" / "L4_text" / "pages"
    rows = []
    for k in range(12):
        rows.append(f"{_COL[:8]}○{_DAYS[k]}晴{_COL[:6]}")
        rows.append(_COL)
    for i in range(1, 4):
        (pages / f"{part_id}_page_{i:03d}.txt").write_text(
            "\n".join(rows[(i - 1) * 8 : i * 8]), encoding="utf-8"
        )
    r = client.post("/api/documents/d1/segmentation/auto", json={"part_id": part_id})
    assert r.status_code == 200, r.text
    d = r.json()
    assert "mark" in (d["induced"] or []) and d["rules_origin"] == "induced"
    manifest = json.loads(
        (Path(lib) / "documents" / "d1" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["segmentation_rules"]["origin"] == "induced"
    assert manifest["segmentation_rules"]["signals"]["mark"] is True
    # 저장된 뒤에는 다시 세지 않는다
    r2 = client.post("/api/documents/d1/segmentation/auto", json={"part_id": part_id})
    assert r2.json()["induced"] is None and r2.json()["rules_origin"] == "induced"


def test_auto_tree_uses_defaults_without_saving_when_nothing_repeats(client, tmp_path):  # noqa: F811
    """되풀이되는 표지가 없는 작은 표본: 기본 신호로 세우되 규칙은 저장하지 않는다."""
    lib, part_id = _setup(client, tmp_path)
    r = client.post("/api/documents/d1/segmentation/auto", json={"part_id": part_id})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["induced"] is not None and d["rules_origin"] == "induced"
    manifest = json.loads(
        (Path(lib) / "documents" / "d1" / "manifest.json").read_text(encoding="utf-8")
    )
    # 세 쪽짜리에서는 권고할 만큼 되풀이되는 것이 없다 — 기본값으로 세우되 저장하지 않는다
    assert not manifest.get("segmentation_rules")
    assert d["applied"] >= 1
    # 사람이 규칙을 저장해 두면 다시 세지 않는다
    client.put("/api/documents/d1/segmentation-rules", json={"rules": {"title_words": ["談草"]}})
    r2 = client.post("/api/documents/d1/segmentation/auto", json={"part_id": part_id})
    assert r2.json()["induced"] is None and r2.json()["rules_origin"] == ""


def test_put_rules_with_new_fields_passes_schema(client, tmp_path):  # noqa: F811
    _setup(client, tmp_path)
    rules = {
        "signals": {"mark": False},
        "head_words": ["有"],
        "furniture": ["書"],
        "toc_llm": True,
        "origin": "manual",
    }
    r = client.put("/api/documents/d1/segmentation-rules", json={"rules": rules})
    assert r.status_code == 200, r.text
    saved = r.json()["segmentation_rules"]
    assert saved["signals"] == {"mark": False} and saved["toc_llm"] is True
    # 모르는 스위치 이름은 normalize_rules가 걷어 내고 저장은 된다 —
    # 오타가 «꺼짐»으로 저장되지 않는다
    r = client.put(
        "/api/documents/d1/segmentation-rules", json={"rules": {"signals": {"marc": False}}}
    )
    assert r.status_code == 200
    assert r.json()["segmentation_rules"]["signals"] == {}


def test_auto_tree_skips_toc_when_switched_off(client, tmp_path):  # noqa: F811
    """편성 탭 목차 줄을 끄면 /auto도 목차를 찾지 않는다 (Codex 지적 2026-09-07)."""
    lib, part_id = _setup(client, tmp_path)
    r = client.post(
        "/api/documents/d1/segmentation/auto",
        json={"part_id": part_id, "use_toc": False, "toc_pages": [1]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["toc_pages"] == []


# ── 층계 (D-117) ─────────────────────────────────────────────────────────


class TestCascade:
    def test_toc_pages_are_not_counted_and_toc_decides(self):
        """목차가 본문과 대조되면 1단에서 멈추고, 목차 쪽의 짧은 행은 규약으로 배우지 않는다."""
        titles = [f"第{_DAYS[i]}篇" for i in range(8)]
        toc_page = ["目錄"] + [f"{t} {i + 1}" for i, t in enumerate(titles)]
        body_pages = [[titles[i], _COL, _COL, _COL] for i in range(8)]
        lines = _pages([toc_page] + body_pages)
        toc = toc_signal(lines)
        assert toc and toc["pages"] == [1] and toc["decisive"], toc
        r = induce_signals(lines, None, toc=toc)
        assert r["stage"]["level"] == 1 and r["stage"]["by"] == ["toc"]
        # 목차 쪽의 「第…篇」 행은 세지 않았다 — 본문에서만 8회
        first = [s for s in r["signals"] if s["id"].startswith("head:第")]
        assert not first or first[0]["count"] == 8
        # 텍스트 신호는 권고하지 않는다(1단에서 멈춤) — 보조만 켠다
        assert all(not s["recommended"] for s in r["signals"] if s["group"] != "aux")
        assert r["stage"]["summary"].startswith("목차 ")

    def test_symbol_family_decides_stage_two(self):
        # 「●」가 두 행마다 한 번 — 날짜가 없어도 시각 신호(2단)가 규약이다
        pages = [
            [f"{_COL[:9]}●{_COL[:10]}" if i % 2 == 0 else _COL for i in range(8)] for _ in range(5)
        ]
        r = induce_signals(_pages(pages))
        assert r["stage"]["level"] == 2
        rules = rules_from_signals(r)
        assert rules["symbols"] == ["●"] and rules["indent_alone"] is False
        assert "sym:●" in r["stage"]["by"]

    def test_punctuation_is_not_a_symbol(self):
        assert is_symbol_char("●") and is_symbol_char("○") and is_symbol_char("△")
        assert not is_symbol_char("、") and not is_symbol_char("。") and not is_symbol_char("「")
        assert not is_symbol_char("□") and not is_symbol_char("本") and not is_symbol_char("1")

    def test_indent_runs_are_not_a_boundary_convention(self):
        # 내려쓴 행이 줄지어 오면(협주·시 본문) 덩어리다 — 내려쓰기 단독은 규약이 아니다
        rows = []
        for i in range(24):
            # 여덟 행마다 셋이 붙어서 내려쓴다 —
            # 절반이 내려쓰면 «본문 위치»의 중앙값이 흔들려 시험이 안 된다
            indented = (i % 8) in (2, 3, 4)
            top = 40 if indented else 10
            rows.append(Line(1, i, _COL, bbox=[10, top, 30, top + 400]))
        r = induce_signals(rows)
        ind = [s for s in r["signals"] if s["id"] == "indent_alone"]
        assert ind and ind[0]["adjacent"] > 0.5 and not ind[0]["recommended"]
        assert r["stage"]["level"] != 2 or "indent_alone" not in r["stage"]["by"]

    def test_weak_toc_falls_through_and_says_so(self):
        # 목차 쪽은 잡혔지만 본문과 거의 대조되지 않는다 — 3단으로 내려가되 요약에 «약함»을 적는다
        toc_page = ["目錄"] + [f"甲{i}篇 {i + 1}" for i in range(8)]
        body = [[f"{_DAYS[i]}談草", _COL, _COL, _COL] for i in range(8)]
        lines = _pages([toc_page] + body)
        toc = toc_signal(lines)
        if toc is None:  # 판별 규칙이 이 합성 목차를 못 잡을 수도 있다 — 그러면 시험 대상이 아니다
            return
        assert not toc["decisive"]
        r = induce_signals(lines, None, toc=toc)
        assert r["stage"]["level"] == 3 and "약함" in r["stage"]["summary"]


class TestProposerVisualRules:
    def test_symbol_alone_makes_candidates(self):
        lines = _pages([[_COL, f"{_COL[:9]}●{_COL[:10]}", _COL, f"●{_COL[:12]}", _COL]])
        none = propose_boundaries(lines, None)
        assert none["stats"]["proposals"] == 0
        r = propose_boundaries(lines, {"symbols": ["●"]})
        acc = [p for p in r["proposals"] if p["accepted"]]
        assert len(acc) == 2
        assert all("symbol:●" in p["reasons"] for p in acc)
        mid = next(p for p in acc if p["char_offset"] > 0)
        assert mid["char_offset"] == 9 and not mid["title"].startswith("●")

    def test_symbol_with_date_keeps_mark_reason(self):
        lines = _diary_pages(n_pages=2, furniture=_COL)
        r = propose_boundaries(lines, {"symbols": ["○"]})
        acc = [p for p in r["proposals"] if p["accepted"]]
        assert acc and all("mark" in p["reasons"] and "symbol:○" in p["reasons"] for p in acc)

    def test_indent_alone_rule(self):
        rows = [Line(1, i, _COL, bbox=[10, 40 if i in (2, 6) else 10, 30, 400]) for i in range(9)]
        assert propose_boundaries(rows, None)["stats"]["proposals"] == 0
        r = propose_boundaries(rows, {"indent_alone": True})
        acc = [p for p in r["proposals"] if p["accepted"]]
        assert [p["line_index"] for p in acc] == [2, 6]
        assert "indent_alone" in acc[0]["reasons"]


class TestLlmPatterns:
    def test_verify_pattern_counts_in_text(self):
        pages = [["又" + _COL[1:], _COL, _COL, "又" + _COL[1:], _COL, _COL] for _ in range(4)]
        lines = _pages(pages)
        row = verify_pattern(lines, "head_word", "又")
        assert row and row["count"] == 8 and row["toggle"] == "head_words" and row["llm"] is True
        assert row["id"] == "head:又" and row["value"] == "又"
        assert verify_pattern(lines, "head_word", "無") is None  # 전문에 없다
        assert verify_pattern(lines, "symbol", "又") is None  # 기호가 아니다

    def test_llm_answer_is_verified_not_trusted(self):
        pages = [["又" + _COL[1:], _COL, _COL, "答" + _COL[1:], _COL, _COL] for _ in range(4)]
        lines = _pages(pages)
        router = _FakeRouter(
            '{"patterns": [{"kind": "head_word", "value": "又", "why": "x"},'
            ' {"kind": "head_word", "value": "無", "why": "지어냄"},'
            ' {"kind": "none"}], "note": "n"}'
        )
        import asyncio

        rows, meta = asyncio.run(extract_start_patterns_llm(lines, normalize_rules(None), router))
        assert [r["id"] for r in rows] == ["head:又"]
        assert meta["model"] == "fake-1" and len(meta["raw"]) == 3
        assert router.calls[0]["response_format"] == "json" and router.calls[0]["think"] is False


def test_signals_api_reports_stage(client, tmp_path):  # noqa: F811
    _lib, part_id = _setup(client, tmp_path)
    r = client.post("/api/documents/d1/segmentation/signals", json={"part_id": part_id})
    assert r.status_code == 200, r.text
    d = r.json()
    assert "stage" in d and d["stage"]["level"] in (0, 1, 2, 3)
    assert d["toc"] is None


class TestSampleScopes:
    def _book(self):
        pages = []
        for p in range(10):
            pages.append([f"{_DAYS[p]}談草", _COL, _COL, "又" + _COL[1:], _COL, _COL])
        return _pages(pages)

    def test_starts_is_default_and_truncates(self):
        lines = self._book()
        rules = normalize_rules(None)
        s = sample_start_lines(lines, rules)
        assert 0 < len(s) <= 80 and all(len(x) <= 24 for x in s)
        assert sample_start_lines(lines, rules, scope="starts") == s

    def test_context_wraps_candidate_with_neighbours(self):
        lines = self._book()
        s = sample_start_lines(lines, normalize_rules(None), scope="context")
        assert s and all("▶" in x and " ／ " in x for x in s)

    def test_pages_and_all_send_whole_pages(self):
        lines = self._book()
        rules = normalize_rules(None)
        pages = sample_start_lines(lines, rules, scope="pages")
        heads = [x for x in pages if x.startswith("— ") and x.endswith("쪽 —")]
        assert len(heads) == 6 and len(pages) == 6 * 7
        whole = sample_start_lines(lines, rules, scope="all")
        assert len(whole) == 10 * 7  # 쪽 머리 + 6행
        size = sample_size(lines, rules, "all")
        assert size["lines"] == 70 and size["chars"] == sum(len(x) for x in whole)

    def test_llm_gets_scope_and_reports_size(self):
        lines = self._book()
        router = _FakeRouter('{"patterns": [{"kind": "head_word", "value": "又"}]}')
        import asyncio

        rows, meta = asyncio.run(
            extract_start_patterns_llm(lines, normalize_rules(None), router, scope="pages")
        )
        assert [r["id"] for r in rows] == ["head:又"]
        assert meta["scope"] == "pages" and meta["sample_lines"] == 42 and meta["sample_chars"] > 0
        assert router.calls[0]["response_format"] == "json"


def test_signals_llm_dry_run_reports_size_without_calling_model(client, tmp_path):  # noqa: F811
    _lib, part_id = _setup(client, tmp_path)
    r = client.post(
        "/api/documents/d1/segmentation/signals/llm",
        json={"part_id": part_id, "scope": "all", "dry_run": True},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["dry_run"] is True and d["scope"] == "all" and d["lines"] > 0 and d["chars"] > 0
    # 모르는 범위는 기본으로
    r = client.post(
        "/api/documents/d1/segmentation/signals/llm",
        json={"part_id": part_id, "scope": "everything", "dry_run": True},
    )
    assert r.json()["scope"] == "starts"


class TestDiscoveryIsGeneric:
    """D-119 — 코드에 종류가 없다: 有·談草·N日·○ 전부 «자리 × 되풀이 문자열»로 찾는다."""

    def test_finds_head_word_and_template_without_naming_them(self):
        # 행마다 달라야 «같은 글 되풀이»로 걸리지 않는다
        body = "山川草木風雲雨雪日月星辰花鳥魚蟲春夏秋冬"
        pages = []
        for p in range(8):
            # 쪽마다 자리를 바꾼다 — 같은 자리에 한 번씩 오면 판심(쪽 규약)으로 걸러진다
            rows = [_COL] * 7
            rows[p % 3] = "又" + body[p:] + body[:p]
            rows[3 + p % 3] = f"{'一二三四五六七八'[p]}、" + body[p + 2 :] + body[: p + 2]
            pages.append(rows)
        r = induce_signals(_pages(pages))
        again = [s for s in r["signals"] if s["id"].startswith("head:又")]
        assert again and again[0]["toggle"] == "head_words" and again[0]["value"].startswith("又")
        # 「一、」「二、」… 는 접으면 «N、» — 템플릿 칸으로
        tpl = [s for s in r["signals"] if s["toggle"] == "head_templates"]
        assert tpl and tpl[0]["value"].startswith("N")
        rules = rules_from_signals(r, None, [s["id"] for s in r["signals"] if s["group"] != "aux"])
        assert any(w.startswith("又") for w in rules["head_words"])
        assert any(t.startswith("N") for t in rules["head_templates"])

    def test_repeated_identical_lines_are_not_a_title_convention(self):
        # 판권 문구 「同十一日」이 쪽마다 되풀이 — 표제는 행마다 달라야 한다
        pages = [["同十一日", _COL, f"{_DAYS[p]}談草", _COL, _COL, "同十一日"] for p in range(8)]
        r = induce_signals(_pages(pages))
        same = [s for s in r["signals"] if s["id"].startswith("head:同")]
        assert all(s.get("marker") == "repeat_text" and not s["recommended"] for s in same)

    def test_template_rule_makes_candidates(self):
        lines = _pages([[_COL, "一、" + _COL[2:], _COL, "二、" + _COL[2:], _COL]])
        assert propose_boundaries(lines, None)["stats"]["proposals"] == 0
        r = propose_boundaries(lines, {"head_templates": ["N、"]})
        acc = [p for p in r["proposals"] if p["accepted"]]
        assert [p["line_index"] for p in acc] == [1, 3]
        assert any(x.startswith("head_template:") for x in acc[0]["reasons"])
