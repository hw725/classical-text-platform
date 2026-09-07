"""글 경계 제안 테스트 (D-088).

무엇을 고정하는가:
  - 날짜 문법: 干支·月·日·是月·是日·初·廿, 한자 수사
  - 사슬: 是月·일자만 적은 표제는 앞 회차의 달을 물려받고, 일자가 작아지면 달을 올린다
  - 본문 속 날짜(달 역행)는 신뢰도가 내려가 승인되지 않는다
  - 표제 어휘·억제 목록은 규칙(문헌 설정)에서 오고 코드에는 없다
  - 형식 신호: 짧은 행·내려쓰기(bbox)
  - API: 제안은 저장하지 않고, 적용은 쪽별 char_range 출처를 가진 단위를 만든다
"""

from __future__ import annotations

import json

import pytest

from src.core.segmentation import (
    DEFAULT_RULES,
    Line,
    cjk_number,
    normalize_rules,
    parse_date_head,
    propose_boundaries,
    span_to_text_and_refs,
)

# 천진담초(1882) 실제 표제 — 운양 김윤식 텍스트 데이터베이스에서 확인한 원문
CHEONJIN_TITLES = [
    ("辛巳十一月二十八日保定督署談草", "辛巳", 11, 28),
    ("是月三十日替署談草", None, None, 30),
    ("十二月初一日替着遣飮時使通詞傳語口談", None, 12, 1),
    ("壬午正月初十日天津海關道署談草", "壬午", 1, 10),
    ("是月十八日周玉山談草", None, None, 18),
    ("壬午二月十一日與許涑文談草略", "壬午", 2, 11),
    ("二十一日海關署談草", None, None, 21),
    ("是日軍械所與劉薌林談草", None, None, None),
    ("十四日海關署口談節錄", None, None, 14),
    ("六月初七日許涑文談略", None, 6, 7),
]
BODY = "本文本文本文本文本文本文本文本文本文本文本"  # 21자 — 본문 열 길이


class TestDateGrammar:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("十", 10),
            ("二十八", 28),
            ("廿一", 21),
            ("初十", 10),
            ("三十", 30),
            ("正", 1),
            ("臘", 12),
            ("卄三", 23),
            ("三", 3),
        ],
    )
    def test_cjk_number(self, text, expected):
        assert cjk_number(text) == expected

    @pytest.mark.parametrize("title,ganzhi,month,day", CHEONJIN_TITLES)
    def test_real_titles_parse(self, title, ganzhi, month, day):
        h = parse_date_head(title)
        assert h.present
        assert (h.ganzhi, h.month, h.day) == (ganzhi, month, day)

    def test_ganzhi_alone_is_not_a_date(self):
        assert not parse_date_head("壬午年間事").present

    def test_body_sentence_without_date(self):
        assert not parse_date_head("李中堂以筆談問曰").present


def _doc(titles, rules=None, body_lines=3, body=BODY):
    """표제 + 본문 행으로 한 쪽짜리 문헌을 만든다."""
    lines, li = [], 0
    for t in titles:
        lines.append(Line(1, li, t))
        li += 1
        for _ in range(body_lines):
            lines.append(Line(1, li, body))
            li += 1
    return propose_boundaries(lines, rules)


class TestChain:
    def test_month_inherited_and_rolled(self):
        r = _doc(
            ["壬午三月二十二日海關署談草", "十二日海關署談草", "是月十八日周玉山談草"],
            {"title_words": ["談草"]},
        )
        months = [(p["date"]["month"], p["date"]["day"]) for p in r["proposals"]]
        assert months == [(3, 22), (4, 12), (4, 18)]
        assert r["proposals"][1]["date"]["month_rolled"] is True
        assert r["proposals"][2]["date"]["month_inferred"] is True
        assert all(p["accepted"] for p in r["proposals"])

    def test_same_day_marker(self):
        r = _doc(["二月十一日與許涑文談草略", "是日軍械所與劉薌林談草"], {"title_words": ["談草"]})
        assert r["proposals"][1]["date"]["day"] == 11 and "same_day" in r["proposals"][1]["reasons"]

    def test_date_inside_body_is_not_accepted(self):
        """12-19 회차 본문 속 「三月廿一日李中堂以筆談問曰」 — 달이 거꾸로, 행은 본문 길이."""
        lines = [Line(1, 0, "十二月十九日北洋衙門談草")]
        lines += [Line(1, i, BODY) for i in range(1, 4)]
        lines.append(Line(1, 4, "三月廿一日李中堂以筆談問曰" + "本文本文本文本文"))  # 21자 본문 열
        lines += [Line(1, i, BODY) for i in range(5, 8)]
        r = propose_boundaries(lines, {"title_words": ["談草", "筆談"]})
        body_prop = next(p for p in r["proposals"] if p["title"].startswith("三月"))
        assert "date_jump" in body_prop["reasons"]
        assert body_prop["accepted"] is False
        assert len(r["spans"]) == 1  # 경계는 12-19 하나뿐

    def test_suppress_list_from_rules(self):
        r = _doc(
            ["十二月十九日北洋衙門談草", "三月廿一日李中堂以筆談問曰"],
            {"title_words": ["談草", "筆談"], "suppress": ["三月廿一日李中堂以筆談問曰"]},
        )
        sup = r["proposals"][1]
        assert sup["suppressed"] is True and sup["accepted"] is False
        assert r["stats"]["suppressed"] == 1


class TestSignals:
    def test_no_title_words_in_code(self):
        assert DEFAULT_RULES["title_words"] == [] and DEFAULT_RULES["suppress"] == []

    def test_title_word_and_place(self):
        r = _doc(["壬午正月初十日天津海關道署談草"], {"title_words": ["談草"]})
        p = r["proposals"][0]
        assert p["kind"] == "談草" and p["place"] == "天津海關道署"
        assert "title_word:談草" in p["reasons"]
        # 15자라 기본 max_title_chars(14)를 넘는다 — 형식 신호 없이 날짜+어휘로 승인
        assert "short_line" not in p["reasons"] and p["accepted"] is True
        r2 = _doc(["二十一日海關署談草"], {"title_words": ["談草"]})
        assert "short_line" in r2["proposals"][0]["reasons"]

    def test_date_only_short_line_is_enough(self):
        """표제 어휘가 없는 일기: 날짜 + 짧은 별행이면 승인."""
        r = _doc(["初三日晴", "初四日雨"], None)
        assert [p["accepted"] for p in r["proposals"]] == [True, True]

    def test_date_only_long_line_is_weak(self):
        """날짜로 시작하지만 본문만큼 긴 행은 승인 문턱 아래."""
        r = _doc(["初三日" + BODY[:18]], None)
        p = r["proposals"][0]
        assert "long_line" in p["reasons"] and p["accepted"] is False

    def test_indent_from_bbox(self):
        """세로쓰기: 표제 열의 위(y1)가 본문 열보다 한 글자 넘게 낮으면 내려쓰기."""
        lines = []
        for i in range(6):
            lines.append(
                Line(1, i, BODY, bbox=[100 * (7 - i), 120, 100 * (7 - i) + 40, 120 + 21 * 26])
            )
        lines.append(Line(1, 6, "十四日海關署口談節錄", bbox=[50, 175, 90, 175 + 10 * 26]))
        r = propose_boundaries(lines, {"title_words": ["口談"]})
        p = r["proposals"][0]
        assert "indent" in p["reasons"] and p["confidence"] >= 0.8

    def test_front_matter_span(self):
        lines = [
            Line(1, 0, "天津奉使緣起"),
            Line(1, 1, BODY),
            Line(1, 2, "辛巳十一月二十八日保定督署談草"),
            Line(1, 3, BODY),
        ]
        r = propose_boundaries(lines, {"title_words": ["談草"]})
        assert [s["kind"] for s in r["spans"]] == ["front", "談草"]
        assert r["spans"][0]["start"] == {"page": 1, "line_index": 0, "char_offset": 0}
        assert r["spans"][1]["end"] == {"page": 1, "line_index": 3, "char_end": None}

    def test_rules_normalized(self):
        r = normalize_rules({"title_words": [" 談草 ", ""], "max_title_chars": "12"})
        assert r["title_words"] == ["談草"] and r["max_title_chars"] == 12 and r["use_date"] is True


class TestSpanRefs:
    def test_cross_page_span_makes_one_ref_per_page(self):
        lines = [
            Line(1, 0, "十四日海關署口談節錄", char_start=0),
            Line(1, 1, BODY, char_start=11),
            Line(2, 0, BODY, char_start=0),
            Line(2, 1, BODY, char_start=22),
        ]
        page_texts = {1: "十四日海關署口談節錄\n" + BODY, 2: BODY + "\n" + BODY}
        span = {"start": {"page": 1, "line_index": 0}, "end": {"page": 2, "line_index": 1}}
        text, refs = span_to_text_and_refs(span, lines, page_texts, "d1", "v1")
        assert text.startswith("十四日") and text.count("\n") == 3
        assert [r["page"] for r in refs] == [1, 2]
        assert refs[0]["char_range"] == [0, len(page_texts[1])]
        assert refs[1]["char_range"] == [0, len(page_texts[2])]
        assert refs[0]["part_id"] == "v1" and refs[0]["layer"] == "L4"


# ── API ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("HOME", str(fake_home))
    from fastapi.testclient import TestClient

    from app.server import app

    with TestClient(app) as c:
        yield c


def _setup(client, tmp_path):
    """서고 + 문헌(PDF 3쪽) + L4 확정본 + 해석 저장소."""
    import fitz

    r = client.post("/api/library/quick-start")
    assert r.status_code == 200
    lib = r.json()["library_path"]
    pdf = tmp_path / "t.pdf"
    d = fitz.open()
    for _ in range(3):
        d.new_page(width=400, height=600)
    d.save(str(pdf))
    with open(pdf, "rb") as f:
        r = client.post(
            "/api/documents/create-from-files",
            data={"doc_id": "d1", "title": "담초"},
            files=[("files", ("t.pdf", f.read(), "application/pdf"))],
        )
    assert r.status_code == 200, r.text
    part_id = r.json()["parts"][0]["part_id"]
    from pathlib import Path

    pages = Path(lib) / "documents" / "d1" / "L4_text" / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    (pages / f"{part_id}_page_001.txt").write_text(
        "天津奉使緣起\n" + BODY + "\n辛巳十一月二十八日保定督署談草\n" + BODY, encoding="utf-8"
    )
    (pages / f"{part_id}_page_002.txt").write_text(
        BODY + "\n是月三十日替署談草\n" + BODY, encoding="utf-8"
    )
    (pages / f"{part_id}_page_003.txt").write_text(
        "十二月初一日替着遣飮時使通詞傳語口談\n" + BODY, encoding="utf-8"
    )
    r = client.post(
        "/api/interpretations",
        json={
            "interp_id": "i1",
            "source_document_id": "d1",
            "interpreter_type": "human",
            "interpreter_name": "t",
            "title": "t",
        },
    )
    assert r.status_code == 200, r.text
    # Work 엔티티는 D-099에서 없앴다 — 편성은 문헌·권만으로 된다.
    return lib, part_id


def test_propose_and_apply(client, tmp_path):
    lib, part_id = _setup(client, tmp_path)
    # 규칙 저장 (문헌 설정)
    r = client.put(
        "/api/documents/d1/segmentation-rules", json={"rules": {"title_words": ["談草", "口談"]}}
    )
    assert r.status_code == 200 and r.json()["segmentation_rules"]["title_words"] == [
        "談草",
        "口談",
    ]
    from pathlib import Path

    manifest = json.loads(
        (Path(lib) / "documents" / "d1" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["segmentation_rules"]["title_words"] == ["談草", "口談"]

    r = client.post(
        "/api/documents/d1/segmentation/propose",
        json={"part_id": part_id},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    titles = [p["title"] for p in data["proposals"] if p["accepted"]]
    assert titles == [
        "辛巳十一月二十八日保定督署談草",
        "是月三十日替署談草",
        "十二月初一日替着遣飮時使通詞傳語口談",
    ]
    assert data["proposals"][1]["date"]["month"] == 11  # 是月 → 물려받음
    assert data["spans"][0]["kind"] == "front" and len(data["spans"]) == 4
    assert data["pages"] == [1, 2, 3]
    # 제안은 아무것도 저장하지 않는다
    assert client.get("/api/documents/d1/contents").json()["total_units"] == 0

    r = client.post(
        "/api/documents/d1/segmentation/apply",
        json={
            "part_id": part_id,
            "spans": [
                {k: v for k, v in s.items() if k in ("title", "kind", "start", "end")}
                for s in data["spans"]
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["created"]) == 4 and r.json()["errors"] == []

    tree = client.get("/api/documents/d1/contents").json()
    blocks = tree["parts"][0]["units"]
    assert [b["sequence_index"] for b in blocks] == [0, 1, 2, 3]
    # 두 쪽에 걸친 회차(1쪽 3행 → 2쪽 1행)는 쪽 배지가 둘
    assert [p["page"] for p in blocks[1]["pages"]] == [1, 2]
    assert blocks[1]["preview"].startswith("辛巳十一月二十八日")
    # 출처에 char_range가 있고 part_id가 채워진다
    from src.core.entity import list_entities

    tb = next(
        b
        for b in list_entities(Path(lib) / "interpretations" / "i1", "unit")
        if b["sequence_index"] == 1
    )
    assert tb["source_refs"][0]["part_id"] == part_id and tb["source_refs"][0]["char_range"][0] > 0
    assert tb["metadata"]["title"] == "辛巳十一月二十八日保定督署談草"


def test_propose_without_l4_is_400(client, tmp_path):
    lib, part_id = _setup(client, tmp_path)
    import shutil
    from pathlib import Path

    shutil.rmtree(Path(lib) / "documents" / "d1" / "L4_text" / "pages")
    r = client.post(
        "/api/documents/d1/segmentation/propose",
        json={"part_id": part_id},
    )
    assert r.status_code == 400 and "L4" in r.json()["error"]


# ── 목차 신호 (D-089) ─────────────────────────────────────────────────────

from src.core.toc import (  # noqa: E402
    TocEntry,
    align_toc_to_body,
    detect_toc_pages,
    extract_toc_entries_llm,
    extract_toc_entries_rule,
    title_similarity,
    toc_page_score,
)

TOC_PAGE = [
    "雲養集目錄",
    "卷之一",
    "詩",
    "感懷 一",
    "次韻贈李參判 二",
    "登北漢 三",
    "卷之二",
    "疏",
    "辭職疏 一",
    "論時務疏 五",
]
BODY_P5 = ["雲養集卷之一", "詩", "感懷", BODY, "次韻贈李參判幷序", BODY, "登北漢山", BODY]
BODY_P6 = ["雲養集卷之二", "疏", "辭職疏 壬午", BODY, "論時務疏", BODY]


class TestTocDetection:
    def test_toc_page_scores_high_body_low(self):
        assert toc_page_score(TOC_PAGE) >= 0.9
        assert toc_page_score(BODY_P5) < 0.7

    def test_detect_first_run_only(self):
        pages = {1: ["雲養集", "重刊本"], 2: TOC_PAGE, 3: TOC_PAGE[:6], 5: BODY_P5, 6: BODY_P6}
        assert detect_toc_pages(pages) == [2, 3]

    def test_rule_extraction_levels_and_leaf_hint(self):
        entries = extract_toc_entries_rule({2: TOC_PAGE}, [2])
        titles = [(e.level, e.title, e.page_hint) for e in entries]
        assert titles[0] == (1, "卷之一", None)  # 卷之一의 수사는 葉 번호가 아니다
        assert (2, "感懷", "一") in titles and (2, "論時務疏", "五") in titles
        assert all(e.title != "雲養集目錄" for e in entries)


class TestTocAlignment:
    def test_similarity_head_and_containment(self):
        assert title_similarity("感懷", "感懷") == 1.0
        assert title_similarity("次韻贈李參判", "次韻贈李參判幷序") >= 0.95
        assert title_similarity("卷之一", "雲養集卷之一") >= 0.9
        assert title_similarity("感懷", BODY) < 0.6

    def test_order_preserving_alignment_with_decoy(self):
        entries = extract_toc_entries_rule({2: TOC_PAGE}, [2])
        # 본문 앞에 미끼 행(뒤 항목과 같은 제목)을 두어도 순서 때문에 앞 항목이 먼저 온다
        body = [Line(5, i, t) for i, t in enumerate(["論時務疏"] + BODY_P5)]
        body += [Line(6, i, t) for i, t in enumerate(BODY_P6)]
        matches, unmatched = align_toc_to_body(entries, body)
        got = {m.title: (m.page, m.line_index) for m in matches}
        assert got["論時務疏"] == (6, 4)  # 미끼(5쪽 0행)가 아니라 순서상 맞는 자리
        assert got["卷之一"] == (5, 1) and got["感懷"] == (5, 3)
        assert unmatched == []

    def test_unmatched_entries_reported(self):
        entries = [TocEntry("感懷"), TocEntry("없는글"), TocEntry("登北漢")]
        body = [Line(5, i, t) for i, t in enumerate(BODY_P5)]
        matches, unmatched = align_toc_to_body(entries, body)
        assert [m.title for m in matches] == ["感懷", "登北漢"] and unmatched == [1]


class _FakeRouter:
    def __init__(self, text):
        self._text = text
        self.calls = []

    async def call(self, prompt, **kwargs):
        self.calls.append(kwargs)

        class R:
            pass

        r = R()
        r.text, r.provider, r.model = self._text, "fake", "fake-1"
        return r


class TestTocLlm:
    def test_llm_json_used_and_json_forced(self):
        import asyncio

        router = _FakeRouter(
            '{"is_toc": true, "entries": [{"title": "感懷", "level": 2, "page_hint": "一"}, '
            '{"title": "卷之二", "level": 1}]}'
        )
        entries, meta = asyncio.run(extract_toc_entries_llm({2: TOC_PAGE}, [2], router))
        assert meta["method"] == "llm" and meta["provider"] == "fake"
        assert [(e.title, e.level, e.page_hint) for e in entries] == [
            ("感懷", 2, "一"),
            ("卷之二", 1, None),
        ]
        # 사고는 명시적으로 끈다(D-083·D-089). Gemini 2.5 flash는 지정하지 않으면 기본으로 사고해
        # 출력 상한을 삼킨다 — 운양집 총목 실측(2026-09-03)에서 한 쪽 JSON도 잘렸다.
        assert router.calls[0]["response_format"] == "json" and router.calls[0]["think"] is False

    def test_llm_is_called_per_page_and_partial_failure_falls_back_per_page(self):
        """쪽마다 따로 부른다 — 한 번에 넘기면 항목 100여 개의 JSON이 max_tokens에서 잘린다."""
        import asyncio

        class _PerPageRouter(_FakeRouter):
            async def call(self, prompt, **kwargs):
                if "[3쪽]" in prompt:
                    self._text = "이 쪽은 이상합니다"
                else:
                    self._text = '{"is_toc": true, "entries": [{"title": "感懷", "level": 2}]}'
                return await super().call(prompt, **kwargs)

        router = _PerPageRouter("")
        entries, meta = asyncio.run(
            extract_toc_entries_llm({2: TOC_PAGE, 3: TOC_PAGE}, [2, 3], router)
        )
        assert len(router.calls) == 2
        assert (
            meta["method"] == "llm+rule" and meta["pages_llm"] == [2] and meta["pages_rule"] == [3]
        )
        assert entries[0].title == "感懷" and entries[0].source_page == 2
        assert any(e.title == "論時務疏" and e.source_page == 3 for e in entries)  # 3쪽은 규칙으로

    def test_llm_garbage_falls_back_to_rule(self):
        import asyncio

        entries, meta = asyncio.run(
            extract_toc_entries_llm({2: TOC_PAGE}, [2], _FakeRouter("응답이 이상합니다"))
        )
        assert meta["method"] == "rule" and meta["error"]
        assert any(e.title == "感懷" for e in entries)


class TestTocSignalInProposer:
    def test_toc_match_creates_boundary_without_date(self):
        lines = [Line(5, i, t) for i, t in enumerate(BODY_P5)]
        entries = extract_toc_entries_rule({2: TOC_PAGE}, [2])
        matches, _ = align_toc_to_body(entries, lines)
        r = propose_boundaries(lines, None, toc_matches=[m.to_dict() for m in matches])
        titles = [(p["title"], p["kind"], p["accepted"]) for p in r["proposals"]]
        assert ("卷之一", "volume", True) in titles and ("感懷", "", True) in titles
        assert all(any(x.startswith("toc:") for x in p["reasons"]) for p in r["proposals"])
        assert r["proposals"][0]["confidence"] >= 0.6


def test_toc_api_and_propose_with_toc(client, tmp_path):
    lib, part_id = _setup(client, tmp_path)
    from pathlib import Path

    pages = Path(lib) / "documents" / "d1" / "L4_text" / "pages"
    (pages / f"{part_id}_page_001.txt").write_text("\n".join(TOC_PAGE), encoding="utf-8")
    (pages / f"{part_id}_page_002.txt").write_text("\n".join(BODY_P5), encoding="utf-8")
    (pages / f"{part_id}_page_003.txt").write_text("\n".join(BODY_P6), encoding="utf-8")

    r = client.post("/api/documents/d1/segmentation/toc", json={"part_id": part_id})
    assert r.status_code == 200, r.text
    assert r.json()["toc_pages"] == [1] and r.json()["method"] == "rule"
    assert [e["title"] for e in r.json()["entries"]][:3] == ["卷之一", "詩", "感懷"]

    body = {"document_id": "d1", "part_id": part_id}
    r = client.post("/api/documents/d1/segmentation/propose", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["toc"]["pages"] == [1] and data["pages"] == [2, 3]  # 목차 쪽은 본문 후보에서 빠진다
    # 卷之一·卷之二(포함 관계)까지 9항목 전부 대조
    assert len(data["toc"]["matches"]) == 9 and data["toc"]["unmatched"] == []
    accepted = [p["title"] for p in data["proposals"] if p["accepted"]]
    assert accepted[:3] == ["卷之一", "詩", "感懷"] and "論時務疏" in accepted
    assert all(ln["page"] != 1 for ln in data["lines"])


# ── 경계 색인 보기 (D-090) ─────────────────────────────────────────────────


def test_boundary_index_is_a_view_over_textblocks(client, tmp_path):
    """경계 색인은 별도 데이터가 아니다 — 단위의 source_refs에서 계산한 보기 (D-090)."""
    lib, part_id = _setup(client, tmp_path)
    rules = {"rules": {"title_words": ["談草", "口談"]}}
    client.put("/api/documents/d1/segmentation-rules", json=rules)
    body = {"document_id": "d1", "part_id": part_id}
    data = client.post("/api/documents/d1/segmentation/propose", json=body).json()
    keep = ("title", "kind", "start", "end")
    r = client.post(
        "/api/documents/d1/segmentation/apply",
        json={
            "part_id": part_id,
            "spans": [{k: v for k, v in s.items() if k in keep} for s in data["spans"]],
        },
    )
    assert r.status_code == 200, r.text
    created = r.json()["created"]
    from pathlib import Path

    interp_dir = Path(lib) / "interpretations" / "i1"
    # D-092: 단위의 정본은 경계 목록 하나. 단위 파일(blocks/*.json)은 만들지 않는다.
    # D-097: 그 목록은 원본 저장소(문헌)에 산다 — 해석 저장소는 참조만 한다.
    assert (Path(lib) / "documents" / "d1" / "boundaries" / f"{part_id}.json").exists()
    assert not (interp_dir / "core_entities" / "boundaries").exists()
    assert not list((interp_dir / "core_entities" / "blocks").glob("*.json"))

    # 목록 = 단위를 원본 위치 순서로, 행 앵커는 source_refs에서 계산
    url = f"/api/documents/d1/boundaries?part_id={part_id}"
    lst = client.get(url).json()
    assert lst["total"] == 4 and [b["order"] for b in lst["boundaries"]] == [0, 1, 2, 3]
    b1 = lst["boundaries"][1]
    assert b1["id"] == created[1]["id"]
    assert b1["title"] == "辛巳十一月二十八日保定督署談草" and b1["status"] == "approved"
    assert b1["start"] == {"page": 1, "line": 2, "offset": 0}
    assert b1["end"] == {"page": 2, "line": 0, "offset": None}
    assert b1["l4_commit"] and b1["bbox"] is None  # L2가 없는 픽스처 — 좌표를 만들지 않는다

    # 내용 트리에도 anchor(metadata.anchor)가 붙는다
    tree = client.get("/api/documents/d1/contents").json()
    blk = tree["parts"][0]["units"][1]
    assert blk["anchor"]["kind"] == "談草" and blk["anchor"]["status"] == "approved"

    # 시작을 한 행 뒤로 — 앞 블록의 끝이 늘고 두 블록의 본문·출처가 다시 이어진다
    r = client.put(f"/api/documents/d1/boundaries/{b1['id']}", json={"shift_start": 1})
    assert r.status_code == 200, r.text
    assert r.json()["boundary"]["start"] == {"page": 1, "line": 3, "offset": 0}
    lst = client.get(url).json()["boundaries"]
    assert lst[0]["end"] == {"page": 1, "line": 2, "offset": None}
    from src.core.entity import get_entity

    tb1 = get_entity(interp_dir, "unit", b1["id"])
    tb0 = get_entity(interp_dir, "unit", lst[0]["id"])
    assert not tb1["original_text"].startswith("辛巳")
    assert tb0["original_text"].rstrip().endswith("辛巳十一月二十八日保定督署談草")
    assert tb1["source_refs"][0]["char_range"][0] > 0
    assert tb1["metadata"]["anchor"]["status"] == "approved"

    # 편성 탭 경로로 만든(경계 제안을 거치지 않은) 단위도 색인 보기에 나타난다
    r = client.post(
        "/api/interpretations/i1/entities/unit/compose",
        json={
            "sequence_index": 99,
            "original_text": BODY,
            "part_id": part_id,
            "source_refs": [
                {
                    "document_id": "d1",
                    "page": 3,
                    "layout_block_id": None,
                    "char_range": [len("十二月初一日替着遣飮時使通詞傳語口談") + 1, 999],
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    lst = client.get(url).json()["boundaries"]
    assert lst[-1]["start"] == {"page": 3, "line": 1, "offset": 0} and lst[-1]["kind"] == "manual"

    # CSV: article_index 관례의 열, BOM
    r = client.get(url.replace("boundaries?", "boundaries/export.csv?"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = [ln for ln in r.content.decode("utf-8-sig").splitlines() if ln.strip()]
    assert rows[0].startswith("기사id,문헌,권,순서,유형,층위,제목,시작쪽,시작행,끝쪽,끝행,상태")
    assert len(rows) == 6 and ",1,3,2,0,approved," in rows[2]


def test_boundary_bbox_from_l2_when_line_counts_match(tmp_path):
    """L2 행 수가 L4 행 수와 맞을 때만 앵커 bbox를 만든다."""
    import json

    from src.core.segmentation import anchor_bbox

    doc = tmp_path / "documents" / "d"
    (doc / "L4_text" / "pages").mkdir(parents=True)
    (doc / "L2_ocr").mkdir()
    manifest = {"document_id": "d", "parts": [{"part_id": "v1"}]}
    (doc / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (doc / "L4_text" / "pages" / "v1_page_001.txt").write_text("甲\n\n乙\n丙", encoding="utf-8")
    lines = [
        {"text": "甲", "bbox": [900, 100, 940, 500]},
        {"text": "乙", "bbox": [800, 100, 840, 500]},
        {"text": "丙", "bbox": [700, 100, 740, 500]},
    ]
    l2 = {
        "part_id": "v1",
        "page_number": 1,
        "image_width": 1000,
        "image_height": 1500,
        "ocr_results": [{"layout_block_id": "b", "lines": lines}],
    }
    (doc / "L2_ocr" / "v1_page_001.json").write_text(json.dumps(l2), encoding="utf-8")
    # L4 행 2(빈 행 다음 乙)는 L2의 두 번째 행
    a = anchor_bbox(doc, "v1", 1, 2)
    assert a["bbox"] == [800, 100, 840, 500] and a["image_width"] == 1000
    assert anchor_bbox(doc, "v1", 1, 1) is None  # 빈 행에는 앵커가 없다


class TestCheonjinFalsePositives:
    """천진담초 208쪽 실측(2026-09-03)에서 나온 오탐 유형을 고정한다 — D-088 «남은 것»."""

    def test_marginal_date_note_needs_title_word_when_configured(self):
        """어휘를 정한 문헌에서 날짜만 있는 짧은 행(두주 「廿一日」)은 승인하지 않는다."""
        lines = [Line(1, 0, "十二月十九日北洋衙門談草")]
        lines += [Line(1, i, BODY) for i in range(1, 4)]
        lines.append(Line(1, 4, "廿一日"))
        lines += [Line(1, i, BODY) for i in range(5, 8)]
        r = propose_boundaries(lines, {"title_words": ["談草"]})
        note = next(p for p in r["proposals"] if p["title"] == "廿一日")
        assert "no_title_word" in note["reasons"] and note["accepted"] is False
        # 어휘 없는 일기류에는 그대로 승인 (기존 동작)
        r2 = propose_boundaries(lines, None)
        assert next(p for p in r2["proposals"] if p["title"] == "廿一日")["accepted"] is True

    def test_word_after_clause_marker_is_not_a_title(self):
        r = _doc(
            ["十二月十九日北洋衙門談草", "李中堂以筆談問曰", "以上口談使通"],
            {"title_words": ["談草", "筆談", "口談"]},
        )
        by = {p["title"]: p for p in r["proposals"]}
        assert "word_in_clause" in by["李中堂以筆談"]["reasons"]
        assert by["李中堂以筆談"]["accepted"] is False
        assert by["以上口談"]["accepted"] is False
        assert by["十二月十九日北洋衙門談草"]["accepted"] is True

    def test_same_day_repeat_without_word(self):
        r = _doc(["三月初四日北洋大臣衙門談草", "四日"], {"title_words": ["談草"]})
        rep = r["proposals"][1]
        assert "same_day_repeat" in rep["reasons"] and rep["accepted"] is False

    def test_dongil_is_same_day_marker(self):
        r = _doc(["七月十四日北洋衙門談草", "同日移麟德口談略"], {"title_words": ["談草", "口談"]})
        p = r["proposals"][1]
        assert "same_day" in p["reasons"] and p["date"]["day"] == 14 and p["accepted"] is True

    def test_word_in_long_line_without_date(self):
        """「同北洋大臣衙門筆談事情及海關談略」(16자, 날짜 없음) — 어휘만으로 승인하지 않는다."""
        r = _doc(
            ["十二月十九日北洋衙門談草", "同北洋大臣衙門筆談事情及海關談略"],
            {"title_words": ["談草", "筆談"]},
        )
        p = r["proposals"][1]
        assert "long_line" in p["reasons"] and p["accepted"] is False


class TestTocUnyangjipLessons:
    """운양집 중간본 총목 실측(2026-09-03)에서 나온 것들 — D-089 «남은 것»."""

    SEO_TAIL = [
        "之辭不足見重今頓得其",
        "賛美之辭乎将使我顔伍",
        "泥而心不寧矣頓得其時",
        "薄之蒔乎於吾心亦必不",
        "悦何以竭文為哉癸丑夏",
        "至日雲養老人序并書",
    ]
    TOC_P9 = SEO_TAIL + [
        "雲養集総目",
        "第一巻詩一百九十八首",
        "撃磬集",
        "混游漫吟",
        "昇平館集",
        "松屋雜詠",
        "健齋集",
        "雲嶽飲泉集",
        "第二巻詩二百七十八首",
        "江北唱和集",
        "北山集",
    ]
    TOC_P10 = [
        "継時",
        "第三巻詩三百四首",
        "関宮唱献集",
        "海西持斧集",
        "續昇平館集",
        "新津于役集",
        "慈江避暑集",
        "河陽行吟集",
        "第七巻",
        "賦五",
        "三十",
        "序四十五",
    ]
    BODY_P14 = [
        "北山集二百二十七",
        "雲養集巻之一",
        "清風金允植洵卿著",
        "詩",
        "撃磬集",
        "自甲寅至甲子在",
        "歸川天雲樓",
        "乙丑秋江漲淹舎笥中詩稾皆没於水心研従兄収拾",
        "拾樓有之餘得若千首時余客湖西聞之恐然有",
        "撃客入海之想帰家後騰写一冊命之曰撃磬集益",
        "孟春夜會石荘山房分韻得凍字",
    ]

    def test_shinjitai_marker_and_midpage_start(self):
        """NDL 엔진의 신자체(総目·巻)를 정자로 보고, 序 꼬리 뒤에서 시작하는 총목을 잡는다."""
        assert toc_page_score(self.TOC_P9) >= 0.7
        assert toc_page_score(self.SEO_TAIL + ["本文" * 8] * 6) < 0.7

    def test_continuation_by_short_ratio_stops_at_body(self):
        pages = {
            8: ["本文本文本文本文本文本文本文本文本文本文"] * 8,
            9: self.TOC_P9,
            10: self.TOC_P10,
            11: self.TOC_P10,
            14: self.BODY_P14,
        }
        pages[12] = self.TOC_P10
        pages[13] = self.TOC_P10
        # 10~13쪽은 표지어가 없어 0.7에 못 미치지만 짧은 행 비율로 이어진다.
        # 14쪽(본문 첫 쪽)에서 끊긴다.
        assert toc_page_score(self.TOC_P10) < 0.7
        assert detect_toc_pages(pages) == [9, 10, 11, 12, 13]

    def test_entries_skip_seo_tail_and_strip_counts(self):
        entries = extract_toc_entries_rule({9: self.TOC_P9, 10: self.TOC_P10}, [9, 10])
        titles = [e.title for e in entries]
        assert not any(t.startswith("之辭") for t in titles)  # 序 꼬리는 항목이 아니다
        assert "雲養集總目" not in titles and "雲養集総目" not in titles
        first = entries[0]
        assert (first.title, first.level) == (
            "第一卷詩一百九十八首",
            1,
        )  # 卷 줄은 층위 1, 편수 그대로
        by = {e.title: e for e in entries}
        assert by["賦"].count == "五" and by["序"].count == "四十五"
        assert "三十" not in titles  # 편수만 남은 조각은 버린다
        assert by["撃磬集"].level == 2

    def test_short_titles_match_strictly(self):
        assert title_similarity("月", "月") > 0.9 and title_similarity("月", "月流会棟向憐") == 0.0
        assert (
            title_similarity("同六", "同六") == 1.0 and title_similarity("同六", "同六人談話") > 0.9
        )
        assert title_similarity("同六", "同六人談話甚長不可勝記也") == 0.0


# ── 글자 단위 앵커 (D-090 2단계) ───────────────────────────────────────────

# 澹齋日錄류: 개행 없이 「○七日…○八日…」로 날이 바뀐다. 행 끝에서 날이 바뀌지 않는다.
DAM_L0 = "○七日晴朝食後往訪金生歸路遇雨○八日雨終日在家讀書"
DAM_L1 = "夜半風止○九日晴與客論詩至暮"
DAM_L2 = BODY


class TestCharAnchors:
    def _lines(self):
        return [Line(1, 0, DAM_L0), Line(1, 1, DAM_L1), Line(1, 2, DAM_L2)]

    def test_inline_mark_dates_become_proposals_with_offsets(self):
        r = propose_boundaries(self._lines(), None)
        got = [
            (p["line_index"], p["char_offset"], p["date"]["day"], p["accepted"])
            for p in r["proposals"]
        ]
        k8 = DAM_L0.index("○八日")
        k9 = DAM_L1.index("○九日")
        assert got == [(0, 0, 7, True), (0, k8, 8, True), (1, k9, 9, True)]
        assert all("mark" in p["reasons"] for p in r["proposals"])
        # 긴 행이라도 ○ 표지가 있으면 long_line 감점을 받지 않는다
        assert not any("long_line" in p["reasons"] for p in r["proposals"])
        # 구간은 행 중간에서 끝난다: 첫 구간은 같은 행의 ○八日 앞까지
        sp = r["spans"]
        assert [s["kind"] for s in sp] == ["", "", ""]
        assert sp[0]["start"] == {"page": 1, "line_index": 0, "char_offset": 0}
        assert sp[0]["end"] == {"page": 1, "line_index": 0, "char_end": k8}
        assert sp[1]["start"]["char_offset"] == k8 and sp[1]["end"] == {
            "page": 1,
            "line_index": 1,
            "char_end": k9,
        }
        assert sp[2]["start"] == {"page": 1, "line_index": 1, "char_offset": k9}
        assert sp[2]["end"] == {"page": 1, "line_index": 2, "char_end": None}

    def test_span_text_and_refs_cut_inside_lines(self):
        from src.core.segmentation import anchor_from_refs, span_to_text_and_refs

        lines = self._lines()
        # collect_document_lines가 넣는 char_start를 흉내 낸다
        off = 0
        for ln in lines:
            ln.char_start = off
            off += len(ln.text) + 1
        page_text = "\n".join(ln.text for ln in lines)
        r = propose_boundaries(lines, None)
        k8 = DAM_L0.index("○八日")
        k9 = DAM_L1.index("○九日")
        t0, refs0 = span_to_text_and_refs(r["spans"][0], lines, {1: page_text}, "d", "v1")
        t1, refs1 = span_to_text_and_refs(r["spans"][1], lines, {1: page_text}, "d", "v1")
        t2, refs2 = span_to_text_and_refs(r["spans"][2], lines, {1: page_text}, "d", "v1")
        assert t0 == DAM_L0[:k8]
        assert t1 == DAM_L0[k8:] + "\n" + DAM_L1[:k9]
        assert t2 == DAM_L1[k9:] + "\n" + DAM_L2
        assert refs0[0]["char_range"] == [0, k8]
        assert refs1[0]["char_range"] == [k8, len(DAM_L0) + 1 + k9]
        assert refs2[0]["char_range"] == [len(DAM_L0) + 1 + k9, len(page_text)]
        # 출처 → 앵커: 행과 행 안 글자가 돌아온다. 끝이 행 끝이면 offset=None
        a1 = anchor_from_refs(refs1, {1: page_text})
        assert a1["start"] == {"page": 1, "line": 0, "offset": k8}
        assert a1["end"] == {"page": 1, "line": 1, "offset": k9}
        a2 = anchor_from_refs(refs2, {1: page_text})
        assert a2["start"] == {"page": 1, "line": 1, "offset": k9}
        assert a2["end"] == {"page": 1, "line": 2, "offset": None}

    def test_plain_lines_keep_line_level_shape(self):
        """행 첫머리 경계만 있는 문헌은 예전과 같은 결과(오프셋 0·행 끝)."""
        r = _doc(["壬午三月二十二日海關署談草", "十二日海關署談草"], {"title_words": ["談草"]})
        assert all(p["char_offset"] == 0 for p in r["proposals"])
        assert all(
            s["start"]["char_offset"] == 0 and s["end"]["char_end"] is None for s in r["spans"]
        )

    def test_mark_alone_is_not_a_boundary(self):
        """○만 있고 날짜가 없으면(구두점·표기 부호) 후보가 아니다."""
        r = propose_boundaries([Line(1, 0, "○七日晴○雨止○又雨")], None)
        assert [p["char_offset"] for p in r["proposals"]] == [0]


def _doc_with_two_lines(tmp_path, direction="vertical_rtl"):
    """L2 행 좌표와 L4 확정본을 가진 최소 문헌. 세로쓰기 두 줄, 각 8자."""
    import json

    doc = tmp_path / "documents" / "d"
    (doc / "L4_text" / "pages").mkdir(parents=True)
    (doc / "L2_ocr").mkdir()
    (doc / "manifest.json").write_text(
        json.dumps({"document_id": "d", "parts": [{"part_id": "v1"}]}), encoding="utf-8"
    )
    l0, l1 = "○七日晴○八日雨", "夜半風止○九日晴"
    (doc / "L4_text" / "pages" / "v1_page_001.txt").write_text(l0 + "\n" + l1, encoding="utf-8")
    boxes = [[900, 100, 940, 900], [840, 100, 880, 900]]  # 세로쓰기: 오른쪽 줄이 먼저
    l2 = {
        "part_id": "v1",
        "page_number": 1,
        "image_width": 1000,
        "image_height": 1500,
        "ocr_results": [
            {
                "layout_block_id": "b",
                "writing_direction": direction,
                "lines": [
                    {"text": l0, "bbox": boxes[0]},
                    {"text": l1, "bbox": boxes[1]},
                ],
            }
        ],
    }
    (doc / "L2_ocr" / "v1_page_001.json").write_text(json.dumps(l2), encoding="utf-8")
    return doc, l0, l1


def test_interp_cannot_write_a_foreign_documents_boundary(client, tmp_path):
    """해석 저장소를 통해 남의 문헌에 경계를 만들 수 없다 (D-097).

    왜 시험하는가: 화면에서 문헌을 바꿔도 앞 문헌의 해석 저장소가 붙어 있던 버그와 겹쳐,
    운양집 저장소에 천진담초 경계 42개가 들어갔다(실측 2026-09-04). 편성 API는 이제 경로가
    문헌이라 남의 문헌을 가리킬 방법 자체가 없지만, 해석 저장소를 통해 단위를 만드는 길
    (entities/unit/compose)은 여전히 source_refs의 문헌을 믿는다 — 거기서 막는다.
    """
    lib, part_id = _setup(client, tmp_path)
    r = client.post(
        "/api/interpretations/i1/entities/unit/compose",
        json={
            "sequence_index": 0,
            "original_text": "남의 문헌",
            "part_id": part_id,
            "source_refs": [
                {
                    "document_id": "d2",
                    "page": 1,
                    "layout_block_id": None,
                    "char_range": [0, 3],
                }
            ],
        },
    )
    assert r.status_code >= 400, r.text
    assert "d1" in r.text  # 이 저장소의 문헌 이름을 알려 준다


def test_role_estimated_marks_boundaries_without_a_role(client, tmp_path):
    """역할이 없는 옛 경계는 «추정»이라고 알린다 — 파일에 적어 굳히지 않는다.

    왜: 추정값을 파일에 넣으면 사람이 정한 것과 구별되지 않고, 나중에 추정 규칙이 좋아져도
    옛 데이터가 따라오지 않는다. 그래서 저장은 그대로 두고 «어림한 값»이라고 표시만 한다.
    """
    from pathlib import Path as _P

    lib, part_id = _setup(client, tmp_path)
    span = {
        "title": "기사",
        "kind": "manual",
        "role": "article",  # 화면은 언제나 역할을 함께 보낸다(제안이 추정한 값 또는 사람이 고른 값)
        "start": {"page": 1, "line_index": 0, "char_offset": 0},
        "end": {"page": 1, "line_index": 0, "char_end": None},
    }
    r = client.post(
        "/api/documents/d1/segmentation/apply",
        json={"document_id": "d1", "part_id": part_id, "spans": [span]},
    )
    assert r.status_code == 200, r.text
    url = f"/api/documents/d1/boundaries?part_id={part_id}"
    row = client.get(url).json()["boundaries"][0]
    assert row["role_estimated"] is False  # 적용은 역할을 적는다

    # 역할을 지운 옛 파일을 흉내 낸다
    import json as _json

    f = _P(lib) / "documents" / "d1" / "boundaries" / f"{part_id}.json"
    data = _json.loads(f.read_text(encoding="utf-8"))
    for b in data["boundaries"]:
        b.pop("role", None)
    f.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")

    row = client.get(url).json()["boundaries"][0]
    assert row["role_estimated"] is True
    assert row["role"] == "article"  # 깊이 2 → 기사로 어림


class TestReferenceExcerpt:
    """긴 해제는 앞을 자르지 말고 «권별 서술»을 골라 간추린다.

    왜 시험하는가: 한국문집총간류 해제는 «생애 → 교유 → 간행과 권별 내용» 순서다. 앞에서
    자르면 정확히 쓸모 있는 데(뒤의 권별 서술)만 사라진다. 운양집 해제 실측(2026-09-03)에서
    전체 23,894자 중 권별 서술은 43% 지점에서 시작했고, 옛 방식(앞 4,000자)에는 「권N」이
    **하나도** 들어가지 않았다.
    """

    def _long_heje(self):
        bio = ["김윤식은 1835년에 태어나 여러 관직을 거쳤다." * 12 for _ in range(30)]
        vols = [f"권{i}에는 시 {i * 20}수가 수록되어 있다." * 3 for i in range(1, 16)]
        return "머리말 《운양집》 해제\n" + "\n".join(bio + ["3. 간행과 그 권별 내용"] + vols)

    def test_short_text_is_untouched(self):
        from src.core.toc import reference_excerpt

        assert reference_excerpt("짧은 해제", 8000) == "짧은 해제"
        assert reference_excerpt("", 8000) == ""

    def test_volume_paragraphs_survive_and_head_is_kept(self):
        import re

        from src.core.toc import reference_excerpt

        text = self._long_heje()
        assert len(text) > 8000
        got = reference_excerpt(text, 4000)
        assert len(got) <= 4000 + len("…(이하 줄임)…") + 40
        assert got.startswith("머리말 《운양집》 해제")  # 문헌을 가리키는 머리는 남는다
        # 옛 방식(앞에서 자르기)에는 「권N」이 하나도 없다
        assert not re.findall(r"권\d+", text[:4000])
        assert len(set(re.findall(r"권\d+", got))) >= 10  # 간추린 글에는 권별 서술이 남는다

    def test_order_is_preserved_and_gaps_are_marked(self):
        import re

        from src.core.toc import reference_excerpt

        got = reference_excerpt(self._long_heje(), 4000)
        nums = [int(n) for n in re.findall(r"권(\d+)에는", got)]
        assert nums == sorted(nums)  # 권1·권2… 순서 자체가 정보다
        assert "…(중략)…" in got or "…(이하 줄임)…" in got

    def test_text_with_no_structure_words_falls_back_to_the_head(self):
        from src.core.toc import reference_excerpt

        text = "\n".join(["아무 말이나 적은 문단입니다." * 20 for _ in range(50)])
        got = reference_excerpt(text, 2000)
        assert len(got) <= 2000 + 40
        assert got.startswith("아무 말이나")


class TestTitleWordSuggest:
    """해제·본문 표본에서 표제 어휘 뽑기 (D-092 남은 것).

    왜 시험하는가: LLM이 그럴듯한 한자어를 지어내면 규칙이 오염되고, 그 규칙으로 만든 경계는
    누구도 되짚지 못한다. «표본에 실제로 있는 말만»이 이 기능의 안전장치다.
    """

    def test_sampler_takes_short_lines_spread_over_the_volume(self):
        from src.core.segmentation import Line, sample_heading_lines

        lines = [Line(1, i, "표제談草" if i % 2 == 0 else BODY) for i in range(40)]
        got = sample_heading_lines(lines, 14, limit=5)
        assert got == ["표제談草"]  # 중복은 하나로
        many = [Line(1, i, f"표제{i}談草") for i in range(40)]
        got2 = sample_heading_lines(many, 14, limit=5)
        assert len(got2) == 5 and got2[0] == "표제0談草" and len(set(got2)) == 5

    def test_only_words_present_in_the_sample_survive(self):
        import asyncio

        from src.core.segmentation import extract_title_words_llm

        router = _FakeRouter(
            '{"title_words": ["談草", "筆談", "日記"], "suppress": ["雲養集卷之一"],'
            ' "note": "표본의 표제가 談草로 끝난다"}'
        )
        sample = ["辛巳十一月二十八日保定督署談草", "是月十八日周玉山筆談"]
        got, meta = asyncio.run(extract_title_words_llm("해제", sample, router))
        # 日記는 표본에 없다 — 지어낸 것으로 보고 버린다
        assert got["title_words"] == ["談草", "筆談"]
        assert got["suppress"] == ["雲養集卷之一"]  # 억제는 표본 밖 행도 받는다
        assert got["note"].startswith("표본의")
        assert meta["model"] == "fake-1" and meta["error"] is None

    def test_json_is_forced_and_thinking_off_and_reference_goes_in(self):
        import asyncio

        from src.core.segmentation import extract_title_words_llm

        router = _FakeRouter('{"title_words": []}')
        asyncio.run(
            extract_title_words_llm(
                "운양집 중간본 16권 8책.", ["아무행談草"], router, "ollama", "m"
            )
        )
        kw = router.calls[0]
        assert kw["response_format"] == "json" and kw["think"] is False
        assert kw["force_provider"] == "ollama" and kw["force_model"] == "m"

    def test_llm_failure_is_reported_not_raised(self):
        import asyncio

        from src.core.segmentation import extract_title_words_llm

        class _Dead:
            async def call(self, prompt, **kwargs):
                raise RuntimeError("프로바이더를 사용할 수 없습니다")

        got, meta = asyncio.run(extract_title_words_llm("", ["아무행談草"], _Dead()))
        assert got["title_words"] == [] and "사용할 수 없습니다" in meta["error"]

    def test_nothing_to_look_at_is_said_plainly(self):
        import asyncio

        from src.core.segmentation import extract_title_words_llm

        got, meta = asyncio.run(extract_title_words_llm("", [], _FakeRouter("{}")))
        assert got["title_words"] == [] and "해제도 본문 표본도 없습니다" in meta["error"]


class TestVolumeHead:
    """卷 표제를 본문에서 직접 잡는다 (D-092 남은 것 — 목차가 없거나 대조가 빗나가도 묶음이 선다).

    왜 «행 끝»인가: 卷頭는 짧은 한 행이고 卷 이름이 그 행을 끝맺는다. 본문 속의 卷은 말이
    이어진다. 이 구분이 무너지면 「弁諸卷首乎余曰序者所」 같은 본문 행이 묶음이 된다.
    """

    def test_volume_lines_are_caught_including_shinjitai_and_title_prefix(self):
        from src.core.segmentation import volume_head

        assert volume_head("卷之一", 14) == "卷之一"
        assert volume_head("雲養集巻之一", 14) == "卷之一"  # NDL 신자체 + 서명 앞머리
        assert volume_head("第一巻", 14) == "第一卷"
        assert volume_head("附錄", 14) == "附錄"
        assert volume_head("卷之十七", 14) == "卷之十七"

    def test_volume_word_inside_a_sentence_is_not_a_heading(self):
        from src.core.segmentation import volume_head

        assert volume_head("巻螺巾車", 14) is None  # 卷이 낱말로 쓰인 제목
        assert volume_head("弁諸巻首乎余曰序者所", 14) is None  # 본문 속 卷
        assert volume_head("第一巻詩一百九十八首", 14) is None  # 총목의 편수 꼬리
        assert volume_head("雲養集第一巻篇数", 14) is None
        assert volume_head("天津談草", 14) is None

    def test_long_line_is_not_a_heading(self):
        from src.core.segmentation import volume_head

        assert volume_head("이 행은 아주 길어서 표제로 보지 않는다 卷之一", 8) is None

    def test_repeated_volume_name_is_the_printing_gutter_not_a_new_volume(self):
        """같은 卷 이름이 또 나오면 판심(版心)이다 — 거기서 卷이 새로 시작하지 않는다.

        운양집 실측(2026-09-03): 「卷之一」이 14·18·21·25쪽에 나왔다. 고서는 판심에 卷 이름을
        잎마다 되풀이해 적고 OCR이 그 열을 행으로 읽는다. 되풀이를 그냥 두면 卷 하나가
        네 조각으로 갈린다.
        """
        from src.core.segmentation import Line, propose_boundaries

        lines = [
            Line(1, 0, "雲養集巻之一"),
            Line(1, 1, BODY),
            Line(2, 0, "巻之一"),  # 판심
            Line(2, 1, BODY),
            Line(3, 0, "巻之二"),  # 다른 卷 — 이건 새 卷이다
            Line(3, 1, BODY),
        ]
        r = propose_boundaries(lines, None)
        vol = {(p["page"], p["line_index"]): p for p in r["proposals"] if p["kind"] == "volume"}
        assert vol[(1, 0)]["accepted"] is True
        assert vol[(2, 0)]["accepted"] is False
        assert "volume_repeat" in vol[(2, 0)]["reasons"]
        assert vol[(3, 0)]["accepted"] is True  # 이름이 다르면 되풀이가 아니다

    def test_proposal_gets_volume_kind_level_and_role(self):
        from src.core.segmentation import Line, propose_boundaries

        lines = [
            Line(1, 0, "雲養集巻之一"),
            Line(1, 1, BODY),
            Line(1, 2, "壬午正月初十日天津海關道署談草"),
            Line(1, 3, BODY),
        ]
        r = propose_boundaries(lines, {"title_words": ["談草"]})
        by = {p["title"]: p for p in r["proposals"]}
        vol = by["雲養集巻之一"]
        assert vol["kind"] == "volume"
        assert vol["level"] == 1 and vol["role"] == "container"
        assert vol["accepted"] is True
        assert any(x.startswith("volume:") for x in vol["reasons"])
        # 기사는 그대로 기사다
        art = by["壬午正月初十日天津海關道署談草"]
        assert art["kind"] == "談草" and art["role"] == "article"


class TestPositionAtPoint:
    """원본 이미지에서 찍은 점 → (행·글자). anchor_bbox의 역 (B-002).

    왜 시험하는가: 화면이 아니라 서버가 L4 행(빈 행 포함)과 L2 행(비어 있지 않은 행만)의
    대응을 푼다. 이 대응이 어긋나면 엉뚱한 행에 경계가 놓인다 — 자동 테스트가 아니면
    «한 행 밀림»을 알아보기 어렵다.
    """

    def test_point_inside_a_line_gives_line_and_char(self, tmp_path):
        from src.core.segmentation import position_at_point

        doc, l0, _ = _doc_with_two_lines(tmp_path)
        # 첫 줄(x 900~940) 한가운데 높이 → 8자의 절반인 4자째
        hit = position_at_point(doc, "v1", 1, 920, 500)
        assert hit["line"] == 0 and hit["offset"] == 4 and hit["inside"] is True
        assert hit["anchor_text"] == l0[4:]
        # 줄 머리
        assert position_at_point(doc, "v1", 1, 920, 100)["offset"] == 0

    def test_second_line_is_the_second_box(self, tmp_path):
        from src.core.segmentation import position_at_point

        doc, _, l1 = _doc_with_two_lines(tmp_path)
        hit = position_at_point(doc, "v1", 1, 860, 100)
        assert hit["line"] == 1 and hit["offset"] == 0 and hit["anchor_text"] == l1[:8]

    def test_point_outside_falls_back_to_nearest_line(self, tmp_path):
        from src.core.segmentation import position_at_point

        doc, _, _ = _doc_with_two_lines(tmp_path)
        hit = position_at_point(doc, "v1", 1, 990, 500)  # 첫 줄보다 오른쪽 여백
        assert hit["line"] == 0 and hit["inside"] is False

    def test_screen_width_is_converted_to_l2_width(self, tmp_path):
        from src.core.segmentation import position_at_point

        doc, _, _ = _doc_with_two_lines(tmp_path)
        # 화면 캔버스가 500px이면 좌표는 절반. 결과는 L2 기준과 같아야 한다
        a = position_at_point(doc, "v1", 1, 920, 500)
        b = position_at_point(doc, "v1", 1, 460, 250, image_width=500)
        assert (a["line"], a["offset"]) == (b["line"], b["offset"])

    def test_horizontal_text_cuts_left_to_right(self, tmp_path):
        from src.core.segmentation import position_at_point

        doc, _, _ = _doc_with_two_lines(tmp_path, direction="horizontal_lr")
        # 가로쓰기는 x가 글자 번호를 정한다. 상자 폭 900~940의 한가운데
        hit = position_at_point(doc, "v1", 1, 920, 500)
        assert hit["line"] == 0 and hit["offset"] == 4

    def test_line_count_mismatch_gives_nothing(self, tmp_path):
        from src.core.segmentation import position_at_point

        doc, _, _ = _doc_with_two_lines(tmp_path)
        # 확정본에 행을 하나 더 넣으면 L2와 수가 어긋난다 — 틀린 자리보다 없는 게 낫다
        pp = doc / "L4_text" / "pages" / "v1_page_001.txt"
        pp.write_text(pp.read_text(encoding="utf-8") + "\n덧붙인행", encoding="utf-8")
        assert position_at_point(doc, "v1", 1, 920, 500) is None

    def test_page_without_l2_gives_nothing(self, tmp_path):
        from src.core.segmentation import position_at_point

        doc, _, _ = _doc_with_two_lines(tmp_path)
        assert position_at_point(doc, "v1", 9, 920, 500) is None


def test_anchor_bbox_cuts_line_box_by_char_fraction(tmp_path):
    """행 중간 앵커는 행 bbox를 글자 비율로 자른다(세로쓰기는 위아래)."""
    import json

    from src.core.segmentation import anchor_bbox, boundary_bbox

    doc = tmp_path / "documents" / "d"
    (doc / "L4_text" / "pages").mkdir(parents=True)
    (doc / "L2_ocr").mkdir()
    (doc / "manifest.json").write_text(
        json.dumps({"document_id": "d", "parts": [{"part_id": "v1"}]}), encoding="utf-8"
    )
    text = "○七日晴○八日雨"  # 8자
    (doc / "L4_text" / "pages" / "v1_page_001.txt").write_text(text, encoding="utf-8")
    l2 = {
        "part_id": "v1",
        "page_number": 1,
        "image_width": 1000,
        "image_height": 1500,
        "ocr_results": [
            {
                "layout_block_id": "b",
                "writing_direction": "vertical_rtl",
                "lines": [{"text": text, "bbox": [900, 100, 940, 900]}],
            }
        ],
    }
    (doc / "L2_ocr" / "v1_page_001.json").write_text(json.dumps(l2), encoding="utf-8")
    whole = anchor_bbox(doc, "v1", 1, 0)
    assert whole["bbox"] == [900, 100, 940, 900]
    half = anchor_bbox(doc, "v1", 1, 0, offset=4)  # ○八日雨 — 뒤 절반
    assert half["bbox"] == [900, 500, 940, 900]
    head = anchor_bbox(doc, "v1", 1, 0, offset=0, offset_end=4)  # 앞 절반
    assert head["bbox"] == [900, 100, 940, 500]
    bb = boundary_bbox(
        doc, "v1", {"page": 1, "line": 0, "offset": 4}, {"page": 1, "line": 0, "offset": None}
    )
    assert bb["start_line"] == [900, 500, 940, 900] and bb["end_line"] == [900, 100, 940, 900]


def test_char_boundary_apply_and_move_via_api(client, tmp_path):
    """행 중간 경계가 적용·색인·옮기기(이웃 재잇기)까지 글자 단위로 돈다 (D-090 2단계)."""
    lib, part_id = _setup(client, tmp_path)
    from pathlib import Path

    pages = Path(lib) / "documents" / "d1" / "L4_text" / "pages"
    for n in (1, 2, 3):
        (pages / f"{part_id}_page_{n:03d}.txt").unlink()
    (pages / f"{part_id}_page_001.txt").write_text(
        DAM_L0 + "\n" + DAM_L1 + "\n" + DAM_L2, encoding="utf-8"
    )
    client.put("/api/documents/d1/segmentation-rules", json={"rules": None})
    body = {"document_id": "d1", "part_id": part_id, "pages": [1]}
    data = client.post("/api/documents/d1/segmentation/propose", json=body).json()
    k8 = DAM_L0.index("○八日")
    k9 = DAM_L1.index("○九日")
    assert [(p["line_index"], p["char_offset"]) for p in data["proposals"]] == [
        (0, 0),
        (0, k8),
        (1, k9),
    ]
    keep = ("title", "kind", "start", "end")
    r = client.post(
        "/api/documents/d1/segmentation/apply",
        json={
            "part_id": part_id,
            "pages": [1],
            "spans": [{k: v for k, v in s.items() if k in keep} for s in data["spans"]],
        },
    )
    assert r.status_code == 200, r.text
    url = f"/api/documents/d1/boundaries?part_id={part_id}"
    lst = client.get(url).json()["boundaries"]
    assert [(b["start"]["line"], b["start"]["offset"]) for b in lst] == [(0, 0), (0, k8), (1, k9)]
    assert lst[0]["end"] == {"page": 1, "line": 0, "offset": k8}
    assert lst[1]["end"] == {"page": 1, "line": 1, "offset": k9}
    assert lst[2]["end"] == {"page": 1, "line": 2, "offset": None}
    tb = client.get(f"/api/interpretations/i1/entities/unit/{lst[1]['id']}").json()
    assert tb["original_text"] == DAM_L0[k8:] + "\n" + DAM_L1[:k9]

    # 둘째 블록의 시작을 두 글자 앞으로(○八日 앞의 「遇雨」부터) — 앞 블록의 끝이 같이 줄어든다
    r = client.put(
        f"/api/documents/d1/boundaries/{lst[1]['id']}",
        json={"start": {"page": 1, "line": 0, "offset": k8 - 2}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["boundary"]["start"] == {"page": 1, "line": 0, "offset": k8 - 2}
    lst2 = client.get(url).json()["boundaries"]
    assert lst2[0]["end"] == {"page": 1, "line": 0, "offset": k8 - 2}
    t0 = client.get(f"/api/interpretations/i1/entities/unit/{lst2[0]['id']}").json()
    t1 = client.get(f"/api/interpretations/i1/entities/unit/{lst2[1]['id']}").json()
    assert t0["original_text"] == DAM_L0[: k8 - 2]
    assert t1["original_text"].startswith("遇雨○八日")

    # 행 단위로 옮기면(▼) 오프셋은 행 첫머리로 돌아간다
    r = client.put(f"/api/documents/d1/boundaries/{lst[1]['id']}", json={"shift_start": 1})
    assert r.status_code == 200, r.text
    assert r.json()["boundary"]["start"] == {"page": 1, "line": 1, "offset": 0}


# ── 경계 목록이 정본 (D-092): 삽입·삭제·쪼개기 API ────────────────────────


def test_boundary_insert_delete_split_via_api(client, tmp_path):
    """경계 넣기 = 쪼개기(새 id는 뒤에), 지우기 = 합치기(앞 id 유지), 층위 바꾸기, 조각 쪼개기."""
    lib, part_id = _setup(client, tmp_path)
    from pathlib import Path

    pages = Path(lib) / "documents" / "d1" / "L4_text" / "pages"
    for n in (1, 2, 3):
        (pages / f"{part_id}_page_{n:03d}.txt").unlink()
    (pages / f"{part_id}_page_001.txt").write_text(
        DAM_L0 + "\n" + DAM_L1 + "\n" + DAM_L2, encoding="utf-8"
    )
    base = "/api/documents/d1/boundaries"
    url = f"{base}?document_id=d1&part_id={part_id}"
    k8 = DAM_L0.index("○八日")
    k9 = DAM_L1.index("○九日")
    # 1) 첫 경계(쪽 첫머리) — 단위 하나가 권 전체
    r = client.post(
        base,
        json={
            "part_id": part_id,
            "start": {"page": 1, "line": 0, "offset": 0},
            "title": "七日",
        },
    )
    assert r.status_code == 200, r.text
    a_id = r.json()["boundary"]["id"]
    # 2) 행 중간에 경계 삽입 → 앞 단위(a)는 그 글자 앞까지, 새 id는 뒤 단위
    r = client.post(
        base,
        json={
            "part_id": part_id,
            "start": {"page": 1, "line": 0, "offset": k8},
            "title": "八日",
        },
    )
    assert r.status_code == 200, r.text
    b_id = r.json()["boundary"]["id"]
    lst = client.get(url).json()["boundaries"]
    assert [b["id"] for b in lst] == [a_id, b_id]
    assert lst[0]["end"] == {"page": 1, "line": 0, "offset": k8}
    ta = client.get(f"/api/interpretations/i1/entities/unit/{a_id}").json()
    assert ta["original_text"] == DAM_L0[:k8]
    # 3) 층위 3 조각을 안에 넣어도 층위 2의 id·끝은 그대로
    r = client.post(
        base,
        json={
            "part_id": part_id,
            "start": {"page": 1, "line": 1, "offset": 0},
            "level": 3,
            "title": "조각",
        },
    )
    assert r.status_code == 200, r.text
    c_id = r.json()["boundary"]["id"]
    lst = client.get(url).json()["boundaries"]
    assert [(b["id"], b["level"]) for b in lst] == [(a_id, 2), (b_id, 2), (c_id, 3)]
    tb = client.get(f"/api/interpretations/i1/entities/unit/{b_id}").json()
    assert (
        tb["original_text"] == DAM_L0[k8:] + "\n" + DAM_L1 + "\n" + DAM_L2
    )  # 층위 3은 끝을 정하지 않는다
    # 4) 층위를 2로 올리면 b가 거기서 끝난다
    r = client.put(f"{base}/{c_id}", json={"level": 2})
    assert r.status_code == 200, r.text
    tb = client.get(f"/api/interpretations/i1/entities/unit/{b_id}").json()
    assert tb["original_text"] == DAM_L0[k8:]
    # 5) 쪼개기 API: 조각 텍스트로 자리를 찾아 경계를 넣는다. 원본 id는 첫 조각으로 남는다
    r = client.post(
        "/api/documents/d1/composition/split",
        json={
            "original_unit_id": c_id,
            "part_id": part_id,
            "pieces": [DAM_L1[:k9], DAM_L1[k9:] + "\n" + DAM_L2],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["created_count"] == 1 and r.json()["deprecated_id"] is None
    d_id = r.json()["created_ids"][0]
    lst = client.get(url).json()["boundaries"]
    assert [b["id"] for b in lst] == [a_id, b_id, c_id, d_id]
    assert lst[3]["start"] == {"page": 1, "line": 1, "offset": k9}
    # 쪼개기는 «기사 안 조각»만 만든다 — 원본보다 한 단 깊고 역할은 조각이다.
    # 같은 층위로 넣으면 원본과 나란한 별도 기사가 되어 기사가 쪼개진다(v1.3.0까지 그랬다).
    # 별도 기사는 사이드바 「경계 넣기」에서만 만든다 — 한 곳에서만 되게 해야 헷갈리지 않는다.
    orig_level = next(b["level"] for b in lst if b["id"] == c_id)
    assert lst[3]["level"] == orig_level + 1
    assert lst[3]["role"] == "fragment"
    # 원본 기사는 쪼개지지 않고 조각을 품는다 — 본문이 그대로다
    tb_c = client.get(f"/api/interpretations/i1/entities/unit/{c_id}").json()
    assert tb_c["original_text"] == DAM_L1 + "\n" + DAM_L2
    # 6) 지우기 = 앞 단위에 합치기(앞 id 유지)
    r = client.delete(f"{base}/{d_id}")
    assert r.status_code == 200, r.text
    assert r.json()["merged_into"] == c_id
    tc = client.get(f"/api/interpretations/i1/entities/unit/{c_id}").json()
    assert tc["original_text"] == DAM_L1 + "\n" + DAM_L2
    # 7) 옛 단위 파일은 생기지 않는다
    assert not list(
        (Path(lib) / "interpretations" / "i1" / "core_entities" / "blocks").glob("*.json")
    )
    # 8) 역할은 깊이와 따로 바꾼다 — 3단에 그대로 있는 채로 «기사»가 된다
    r = client.put(f"{base}/{c_id}", json={"role": "article"})
    assert r.status_code == 200, r.text
    assert r.json()["boundary"]["role"] == "article"
    assert client.put(f"{base}/{c_id}", json={"role": "묶음"}).status_code == 400
    rows = {b["id"]: b for b in client.get(url).json()["boundaries"]}
    assert rows[c_id]["role"] == "article"
    assert rows[a_id]["role"] == "article"  # 역할을 안 준 옛 경계는 깊이로 추정


class TestLevelFromIndent:
    """층위 추정 (D-092): 들여쓰기 최빈값이 기사(2), 그보다 얕으면 卷·편(1), 깊으면 조각(3)."""

    def _line(self, page, i, text, top):
        # 세로쓰기: bbox[1]=top. 글자 한 자 26px. 본문 열은 top 120.
        # 행 길이(px)는 글자 수 × 26 — 글자 크기 추정(char_px)이 행 길이/글자수로 나오기 때문
        return Line(
            page, i, text, bbox=[1000 - i * 40, top, 1000 - i * 40 + 30, top + len(text) * 26]
        )

    def test_levels_follow_indent_mode(self):
        BODYT = BODY
        lines = []
        # 集 표제(頂格, 0자), 기사 표제(2자 내려쓰기 ×3), 부기(4자 내려쓰기), 본문
        lines.append(self._line(1, 0, "十一月談草集", 120))
        lines.append(self._line(1, 1, BODYT, 120))
        lines.append(self._line(1, 2, "十二月十九日北洋衙門談草", 120 + 26 * 2))
        lines.append(self._line(1, 3, BODYT, 120))
        lines.append(self._line(1, 4, "二十日海關署談草", 120 + 26 * 2))
        lines.append(self._line(1, 5, BODYT, 120))
        lines.append(self._line(1, 6, "廿一日海關署談草", 120 + 26 * 2))
        lines.append(self._line(1, 7, "是日談草附記", 120 + 26 * 4))
        lines.append(self._line(1, 8, BODYT, 120))
        r = propose_boundaries(lines, {"title_words": ["談草"]})
        by = {p["title"]: p for p in r["proposals"]}
        assert by["十二月十九日北洋衙門談草"]["level"] == 2
        assert by["十一月談草"]["level"] == 1 and "indent_shallow" in by["十一月談草"]["reasons"]
        assert by["是日談草"]["level"] == 3 and "indent_deep" in by["是日談草"]["reasons"]
        assert all(s["level"] in (1, 2, 3) for s in r["spans"])
        # 역할은 깊이와 따로 매겨진다 — 얕으면 묶음, 깊으면 조각, 나머지는 기사
        assert by["十二月十九日北洋衙門談草"]["role"] == "article"
        assert by["十一月談草"]["role"] == "container"
        assert by["是日談草"]["role"] == "fragment"

    def test_no_bbox_means_level_2(self):
        r = _doc(["壬午三月二十二日海關署談草", "十二日海關署談草"], {"title_words": ["談草"]})
        assert [p["level"] for p in r["proposals"]] == [2, 2]
        # 들여쓰기를 하나도 몰라도 역할은 매겨진다(옛날엔 이 자리에서 함수가 먼저 돌아갔다)
        assert [p["role"] for p in r["proposals"]] == ["article", "article"]


class TestTocReferenceText:
    def test_reference_text_goes_into_prompt_and_rules_keep_it(self):
        import asyncio

        class _R(_FakeRouter):
            prompts = []

            async def call(self, prompt, **kwargs):
                _R.prompts.append(prompt)
                return await super().call(prompt, **kwargs)

        router = _R('{"is_toc": true, "entries": [{"title": "感懷", "level": 2}]}')
        asyncio.run(
            extract_toc_entries_llm(
                {2: TOC_PAGE}, [2], router, reference_text="운양집 중간본 16권 8책. 권1~6 시."
            )
        )
        assert "운양집 중간본 16권 8책" in _R.prompts[0]
        assert normalize_rules({"reference_text": "  해제  "})["reference_text"] == "해제"
        assert normalize_rules(None)["reference_text"] == ""


def test_apply_replaces_proposal_boundaries_and_keeps_manual(client, tmp_path):
    """적용은 누적이 아니다 — 체크 상태가 곧 트리. 손으로 넣은 경계는 남는다 (D-092 후속)."""
    lib, part_id = _setup(client, tmp_path)
    client.put(
        "/api/documents/d1/segmentation-rules", json={"rules": {"title_words": ["談草", "口談"]}}
    )
    body = {"document_id": "d1", "part_id": part_id}
    data = client.post("/api/documents/d1/segmentation/propose", json=body).json()
    keep = ("title", "kind", "level", "start", "end")
    spans = [
        {k: v for k, v in s.items() if k in keep} for s in data["spans"] if s["kind"] != "front"
    ]
    assert len(spans) >= 2
    url = f"/api/documents/d1/boundaries?part_id={part_id}"
    r = client.post(
        "/api/documents/d1/segmentation/apply",
        json={"document_id": "d1", "part_id": part_id, "spans": spans},
    )
    assert r.status_code == 200, r.text
    n_all = client.get(url).json()["total"]
    assert n_all == len(spans)
    # 손으로 경계 하나 넣는다
    r = client.post(
        "/api/documents/d1/boundaries",
        json={
            "part_id": part_id,
            "start": {"page": 3, "line": 0, "offset": 2},
            "title": "손",
        },
    )
    assert r.status_code == 200, r.text
    manual_id = r.json()["boundary"]["id"]
    # 첫 구간만 체크해 다시 적용 → 제안 경계는 1개만 남고 손 경계는 그대로
    r = client.post(
        "/api/documents/d1/segmentation/apply",
        json={"document_id": "d1", "part_id": part_id, "spans": spans[:1]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == len(spans) - 1
    lst = client.get(url).json()["boundaries"]
    assert len(lst) == 2 and any(b["id"] == manual_id for b in lst)
    # replace="none"이면 예전처럼 더하기만
    r = client.post(
        "/api/documents/d1/segmentation/apply",
        json={
            "part_id": part_id,
            "spans": spans,
            "replace": "none",
        },
    )
    assert r.status_code == 200 and r.json()["removed"] == 0
    assert client.get(url).json()["total"] == len(spans) + 1


def test_segmentation_auto_builds_tree_in_one_call(client, tmp_path):
    """자동 트리: 제안 → 승인 → 적용을 한 번에. 다시 부르면 새로 세운다(누적 없음)."""
    lib, part_id = _setup(client, tmp_path)
    client.put(
        "/api/documents/d1/segmentation-rules", json={"rules": {"title_words": ["談草", "口談"]}}
    )
    r = client.post(
        "/api/documents/d1/segmentation/auto",
        json={"part_id": part_id},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["applied"] >= 2 and d["toc_pages"] == [] and d["toc_only"] is False
    url = f"/api/documents/d1/boundaries?part_id={part_id}"
    n1 = client.get(url).json()["total"]
    r = client.post("/api/documents/d1/segmentation/auto", json={"part_id": part_id})
    assert r.status_code == 200 and client.get(url).json()["total"] == n1


def test_auto_tree_makes_a_container_from_a_volume_heading(client, tmp_path):
    """卷 표제는 목차가 없어도 묶음(container) 경계가 된다 (D-092 남은 것).

    왜 시험하는가: «목차 항목만 기본 선택»이 들어오면서 목차 없는 문헌의 卷이 통째로
    빠질 수 있었다. 卷이 빠지면 트리에 묶음이 하나도 없어 개요가 평평해진다.
    """
    from pathlib import Path

    lib, part_id = _setup(client, tmp_path)
    pages = Path(lib) / "documents" / "d1" / "L4_text" / "pages"
    # 첫 쪽 첫 행을 卷頭로 바꾼다 (NDL 신자체 그대로 — 정자로 맞춘 뒤 보아야 한다)
    (pages / f"{part_id}_page_001.txt").write_text(
        "雲養集巻之一\n" + BODY + "\n辛巳十一月二十八日保定督署談草\n" + BODY,
        encoding="utf-8",
    )
    client.put(
        "/api/documents/d1/segmentation-rules", json={"rules": {"title_words": ["談草", "口談"]}}
    )
    r = client.post(
        "/api/documents/d1/segmentation/auto",
        json={"part_id": part_id},
    )
    assert r.status_code == 200, r.text
    rows = client.get(f"/api/documents/d1/boundaries?part_id={part_id}").json()["boundaries"]
    vol = [b for b in rows if b["kind"] == "volume"]
    assert len(vol) == 1, [b["title"] for b in rows]
    assert vol[0]["level"] == 1 and vol[0]["role"] == "container"
    assert vol[0]["start"] == {"page": 1, "line": 0, "offset": 0}
    # 기사도 함께 선다 — 卷만 남고 나머지가 사라지면 안 된다
    assert any(b["role"] == "article" for b in rows)


def test_boundary_bbox_falls_back_to_text_match_when_counts_differ(tmp_path):
    """교정으로 행이 합쳐져 L4 행 수가 L2와 달라도, 글자로 닮은 L2 행을 찾아 좌표를 준다.

    2026-09-06 실측: 자동 트리 문헌에서 L2 11행·L4 10행이라 트리를 눌러도 시작 행 점선이
    안 보였다. 위치↔텍스트 연동은 핵심 기능이라 «수가 다르면 포기»는 안 된다.
    """
    import json

    from src.core.segmentation import anchor_bbox

    doc = tmp_path / "documents" / "d"
    (doc / "L4_text" / "pages").mkdir(parents=True)
    (doc / "L2_ocr").mkdir()
    manifest = {"document_id": "d", "parts": [{"part_id": "v1"}]}
    (doc / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # L4는 앞 두 행이 한 행으로 합쳐졌다(2행) — L2는 3행
    l4 = doc / "L4_text" / "pages" / "v1_page_001.txt"
    l4.write_text("甲乙丙丁戊己\n黄李語録劉尹綱目", encoding="utf-8")
    lines = [
        {"text": "甲乙丙", "bbox": [900, 100, 940, 500]},
        {"text": "丁戊己", "bbox": [800, 100, 840, 500]},
        {"text": "黄李語錄劉尹網目", "bbox": [700, 100, 740, 500]},  # 두 자가 이체자
    ]
    l2 = {
        "part_id": "v1",
        "page_number": 1,
        "image_width": 1000,
        "image_height": 1500,
        "ocr_results": [{"layout_block_id": "b", "lines": lines}],
    }
    (doc / "L2_ocr" / "v1_page_001.json").write_text(json.dumps(l2), encoding="utf-8")
    a = anchor_bbox(doc, "v1", 1, 1)
    assert a and a["bbox"] == [700, 100, 740, 500]
    # 닮은 행이 전혀 없으면 None — 틀린 좌표보다 안 보여 주는 게 낫다
    l4.write_text("甲乙丙丁戊己\n天地玄黃宇宙洪荒", encoding="utf-8")
    assert anchor_bbox(doc, "v1", 1, 1) is None


# ── 열·면·쪽 경계에서 갈린 날짜, 행갈음 월초 (D-115, 浩齋辰巳日錄 실측) ─────────────

from src.core.segmentation import Line as _SegLine  # noqa: E402

# 20자 본문 열(浩齋辰巳日錄 4쪽 첫 열 + 다음 열 첫 자)
_COL = "至斬火屋偸盗者六人官政悛也夕還主人家與金"


def _seg_lines(texts, page=1):
    return [_SegLine(page=page, line_index=i, text=t) for i, t in enumerate(texts)]


def test_date_split_across_lines_is_a_candidate():
    """「…○三十|日雨意…」 — ○+숫자가 행 끝에 걸리고 다음 행이 日로 시작하면 30일 경계다.
    다음 행이 다른 쪽에 있어도 같다(면·쪽 경계)."""
    page1 = _seg_lines([_COL, _COL, "家○二十八日城主向略盧寺余乃返家○三十"], page=1)
    page2 = _seg_lines(["日雨意連日麥耕漸晩民事可憫外寇未退内亂", _COL, _COL], page=2)
    res = propose_boundaries(page1 + page2, normalize_rules(None))
    wrapped = [p for p in res["proposals"] if p["accepted"] and "date_wrap" in p["reasons"]]
    assert len(wrapped) == 1
    assert (wrapped[0]["page"], wrapped[0]["char_offset"], wrapped[0]["date"]["day"]) == (1, 16, 30)
    # 숫자만 남은 행 끝(「凡三十」)은 ○도 月도 없으니 날짜가 아니다
    res2 = propose_boundaries(
        _seg_lines(["軍粮凡三十", "日雨" + _COL, _COL]), normalize_rules(None)
    )
    assert not any("date_wrap" in p["reasons"] for p in res2["proposals"])


def test_mark_at_previous_line_end_counts_for_next_line_date():
    """「…事○|八日啓…」 — ○가 앞 행 끝에 남고 날짜가 다음 행 첫머리에 오면 ○+날짜다.
    긴 행 감점을 받지 않는다."""
    lines = _seg_lines(
        [
            _COL,
            "訪太白于郡齋與南環看飢民供館事○",
            "八日啓咸昌行歴路入率禮洞" + _COL[:8],
            _COL,
        ]
    )
    res = propose_boundaries(lines, normalize_rules(None))
    p = next(p for p in res["proposals"] if p["line_index"] == 2)
    assert p["accepted"] and "mark" in p["reasons"] and "date_wrap" in p["reasons"]
    assert "long_line" not in p["reasons"]
    # 앞 행 끝의 ○ 자체는 후보가 아니다(날짜가 없다)
    assert not any(q["line_index"] == 1 for q in res["proposals"])


def test_month_head_after_short_line_is_a_paragraph_start():
    """「달이 바뀔 때만 행갈음」 — 앞 행이 열 용량보다 3자 이상 짧게 끝난 뒤의 날짜 행은
    긴 행이어도 경계다. 쪽의 첫 행에는 주지 않고, 날짜 없는 행은 후보조차 아니다."""
    body = [_COL] * 3 + [
        "將作我國人物將盡矣天不悔禍何其甚耶",  # 17자 — 글이 끝난 열
        "二月一日頗入郡城主又以主屹山祭向聞慶雨",
        _COL,
    ]
    res = propose_boundaries(_seg_lines(body), normalize_rules(None))
    p = next(p for p in res["proposals"] if p["line_index"] == 4)
    assert p["accepted"] and "after_short" in p["reasons"] and "long_line" not in p["reasons"]
    assert not any(q["line_index"] == 5 for q in res["proposals"])
    # 같은 행이 쪽의 첫 행이면 신호가 없다 — 앞 쪽에서 이어지는 열일 수 있다
    res2 = propose_boundaries(
        _seg_lines(["二月一日頗入郡城主又以主屹山祭向聞慶雨"] + [_COL] * 4, page=2),
        normalize_rules(None),
    )
    assert not any("after_short" in q["reasons"] for q in res2["proposals"])


def test_explicit_month_head_with_layout_signal_beats_date_chain():
    """OCR이 「二十日」을 「二日」로 읽으면 사슬이 달을 잘못 넘긴다(month_rolled). 그 뒤의
    행갈음 월초 「八月一日」은 사슬보다 글자를 믿어 date_jump로 떨어지지 않는다."""
    body = [
        _COL,
        "○七月十五日" + _COL[:14],
        "○二日" + _COL[:17],  # 15 → 2: 사슬은 8월로 넘어갔다고 본다
        "○二十日" + _COL[:15],
        "將作我國人物將盡矣天不悔禍何其甚耶",
        "八月一日越到陣中間春陽義" + _COL[:8],
        _COL,
    ]
    res = propose_boundaries(_seg_lines(body), normalize_rules(None))
    assert any("month_rolled" in p["reasons"] for p in res["proposals"])
    p = next(p for p in res["proposals"] if p["line_index"] == 5)
    assert p["accepted"] and "date_jump" not in p["reasons"] and p["date"]["month"] == 8


def test_auto_tree_reports_pages_without_l4(client, tmp_path):
    """L4가 일부 쪽에만 있으면 응답이 그 수를 말한다 — 「후보 0」의 까닭을 화면이 보이려면 필요하다.
    浩齋辰巳日錄 실측: OCR 77쪽 중 L4는 序 한 쪽이어서 날짜 340개를 두고 개요가 비었다."""
    lib, part_id = _setup(client, tmp_path)
    from pathlib import Path

    (Path(lib) / "documents" / "d1" / "L4_text" / "pages" / f"{part_id}_page_003.txt").unlink()
    r = client.post("/api/documents/d1/segmentation/auto", json={"part_id": part_id})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["pages_total"] == 3 and d["pages_with_text"] == 2
