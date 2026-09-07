"""경계 목록(D-092) — 단위의 정본. 순수 계산·파일·마이그레이션·entity 보기를 고정한다.

왜 이 시험이 필요한가:
    단위를 없애고 경계 목록으로 바꾸는 것은 저장 형식 변경이다. «끝은 다음 경계가 정한다»,
    «id는 시작 경계에 붙는다», «층위 n을 손대도 더 얕은 층위의 id는 그대로», «옛 blocks/는 손실
    없이 옮겨진다»가 깨지면 관계·태그·표점 파일의 참조가 끊긴다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core import boundaries as B
from src.core.segmentation import Line

L0 = "○七日晴朝食後往訪金生歸路遇雨○八日雨終日在家讀書"
L1 = "夜半風止○九日晴與客論詩至暮"
L2 = "本文本文本文本文本文本文本文本文本文本文"
PAGE1 = "\n".join([L0, L1, L2])
K8 = L0.index("○八日")
K9 = L1.index("○九日")


def _lines():
    lines = [Line(1, 0, L0), Line(1, 1, L1), Line(1, 2, L2)]
    off = 0
    for ln in lines:
        ln.char_start = off
        off += len(ln.text) + 1
    return lines, {1: PAGE1}


def _data(*items):
    return {"document_id": "d", "part_id": "v1", "boundaries": list(items)}


class TestPositions:
    def test_char_and_position_round_trip(self):
        pt = {1: PAGE1}
        pos = B.position_from_char(pt, 1, len(L0) + 1 + K9)
        assert pos == {"page": 1, "line": 1, "offset": K9}
        assert B.char_from_position(pt, pos) == len(L0) + 1 + K9
        assert B.anchor_text_at(pt, pos) == L1[K9 : K9 + B.ANCHOR_TEXT_LEN]
        assert B.char_from_position(pt, {"page": 1, "line": 9, "offset": 0}) is None


class TestUnits:
    def test_end_is_next_boundary_of_same_or_shallower_level(self):
        lines, pt = _lines()
        b1 = B.new_boundary(
            {"page": 1, "line": 0, "offset": 0}, level=2, title="七日", page_texts=pt
        )
        b2 = B.new_boundary(
            {"page": 1, "line": 0, "offset": K8}, level=3, title="八日 조각", page_texts=pt
        )
        b3 = B.new_boundary(
            {"page": 1, "line": 1, "offset": K9}, level=2, title="九日", page_texts=pt
        )
        units = B.compute_units(_data(b1, b2, b3), lines, pt)
        by = {u["metadata"]["title"]: u for u in units}
        # 층위 2 단위 «七日»은 층위 3 경계를 건너뛰고 다음 층위 2 경계(九日) 앞까지
        assert by["七日"]["original_text"] == L0 + "\n" + L1[:K9]
        # 층위 3 조각은 다음 경계(층위 2도 «같은 층위 이상»)에서 끝난다
        assert by["八日 조각"]["original_text"] == L0[K8:] + "\n" + L1[:K9]
        assert by["九日"]["original_text"] == L1[K9:] + "\n" + L2
        assert by["九日"]["source_refs"][0]["char_range"] == [len(L0) + 1 + K9, len(PAGE1)]
        assert [u["sequence_index"] for u in units] == [0, 1, 2]
        assert (
            by["七日"]["metadata"]["level"] == 2
            and by["八日 조각"]["metadata"]["anchor"]["level"] == 3
        )

    def test_deprecated_boundary_makes_no_unit_and_does_not_end_others(self):
        lines, pt = _lines()
        b1 = B.new_boundary({"page": 1, "line": 0, "offset": 0}, title="a", page_texts=pt)
        b2 = B.new_boundary(
            {"page": 1, "line": 1, "offset": 0}, title="b", status="deprecated", page_texts=pt
        )
        units = B.compute_units(_data(b1, b2), lines, pt)
        assert [u["metadata"]["title"] for u in units] == ["a"]
        assert units[0]["original_text"] == PAGE1

    def test_missing_line_gives_empty_unit_not_crash(self):
        lines, pt = _lines()
        b = B.new_boundary({"page": 1, "line": 7, "offset": 0}, title="x")
        units = B.compute_units(_data(b), lines, pt)
        assert units[0]["original_text"] == "" and units[0]["source_refs"][0]["char_range"] is None


class TestCrud:
    def test_delete_merges_into_previous_and_keeps_front_id(self):
        lines, pt = _lines()
        a = B.new_boundary({"page": 1, "line": 0, "offset": 0}, title="a", page_texts=pt)
        b = B.new_boundary({"page": 1, "line": 1, "offset": 0}, title="b", page_texts=pt)
        data = _data(a, b)
        B.delete_boundary(data, b["id"])
        units = B.compute_units(data, lines, pt)
        assert [u["id"] for u in units] == [a["id"]] and units[0]["original_text"] == PAGE1

    def test_insert_splits_and_new_id_goes_to_back_part(self):
        lines, pt = _lines()
        a = B.new_boundary({"page": 1, "line": 0, "offset": 0}, title="a", page_texts=pt)
        data = _data(a)
        c = B.new_boundary({"page": 1, "line": 0, "offset": K8}, title="c", page_texts=pt)
        B.insert_boundary(data, c)
        units = B.compute_units(data, lines, pt)
        assert [u["id"] for u in units] == [a["id"], c["id"]]
        assert units[0]["original_text"] == L0[:K8] and units[1]["original_text"].startswith(
            "○八日"
        )

    def test_move_only_changes_start_and_neighbours_follow(self):
        lines, pt = _lines()
        a = B.new_boundary({"page": 1, "line": 0, "offset": 0}, title="a", page_texts=pt)
        b = B.new_boundary({"page": 1, "line": 1, "offset": 0}, title="b", page_texts=pt)
        data = _data(a, b)
        B.move_boundary(data, b["id"], {"page": 1, "line": 0, "offset": K8}, pt)
        units = B.compute_units(data, lines, pt)
        assert units[0]["original_text"] == L0[:K8]
        assert B.find_boundary(data, b["id"])["anchor_text"] == L0[K8 : K8 + B.ANCHOR_TEXT_LEN]

    def test_insert_duplicate_id_refused(self):
        a = B.new_boundary({"page": 1, "line": 0, "offset": 0}, boundary_id="x")
        data = _data(a)
        with pytest.raises(FileExistsError):
            B.insert_boundary(
                data, B.new_boundary({"page": 1, "line": 1, "offset": 0}, boundary_id="x")
            )


class TestRematch:
    def test_shifted_text_is_found_by_anchor_text(self):
        pt_old = {1: PAGE1}
        b = B.new_boundary(
            {"page": 1, "line": 1, "offset": K9}, title="九日", page_texts=pt_old, l4_commit="c1"
        )
        data = _data(b)
        # 교감으로 앞 행이 두 자 늘었다 — 오프셋은 틀리고 anchor_text는 그대로
        pt_new = {1: "\n".join([L0, "追記" + L1, L2])}
        n = B.rematch(data, pt_new, "c2")
        assert n == 1
        assert b["start"] == {"page": 1, "line": 1, "offset": K9 + 2} and b["l4_commit"] == "c2"
        assert b.get("anchor_status") != "stale"

    def test_vanished_anchor_is_marked_stale_not_moved(self):
        pt_old = {1: PAGE1}
        b = B.new_boundary({"page": 1, "line": 1, "offset": K9}, page_texts=pt_old, l4_commit="c1")
        data = _data(b)
        B.rematch(data, {1: "\n".join([L0, "全然別文", L2])}, "c2")
        assert b["anchor_status"] == "stale" and b["start"]["offset"] == K9

    def test_same_commit_is_untouched(self):
        b = B.new_boundary(
            {"page": 1, "line": 1, "offset": K9}, page_texts={1: PAGE1}, l4_commit="c1"
        )
        data = _data(b)
        assert B.rematch(data, {1: "\n".join([L0, "追記" + L1, L2])}, "c1") == 0


class TestFilesAndMigration:
    def _interp(self, tmp_path: Path):
        lib = tmp_path / "lib"
        doc = lib / "documents" / "d"
        (doc / "L4_text" / "pages").mkdir(parents=True)
        (doc / "manifest.json").write_text(
            json.dumps({"document_id": "d", "parts": [{"part_id": "v1", "page_count": 1}]}),
            encoding="utf-8",
        )
        (doc / "L4_text" / "pages" / "v1_page_001.txt").write_text(PAGE1, encoding="utf-8")
        interp = lib / "interpretations" / "i"
        (interp / "core_entities" / "blocks").mkdir(parents=True)
        # D-097: 경계는 문헌에 산다. 어느 문헌인지는 dependency.json이 말한다.
        (interp / "dependency.json").write_text(
            json.dumps({"source": {"document_id": "d"}}), encoding="utf-8"
        )
        return lib, interp

    def test_save_load_validates_and_sorts(self, tmp_path):
        _lib, interp = self._interp(tmp_path)
        b2 = B.new_boundary({"page": 1, "line": 1, "offset": 0}, title="b")
        b1 = B.new_boundary({"page": 1, "line": 0, "offset": 0}, title="a")
        B.save_boundaries(interp, _data(b2, b1))
        data = B.load_boundaries(interp, "d", "v1")
        assert [x["title"] for x in data["boundaries"]] == ["a", "b"]
        assert B.list_boundary_parts(interp) == [("d", "v1")]
        with pytest.raises(Exception):
            B.save_boundaries(interp, _data({"id": "x", "level": 2}))  # start·status 빠짐

    def test_migrate_keeps_ids_and_positions_and_renames_blocks(self, tmp_path):
        lib, interp = self._interp(tmp_path)
        blocks = interp / "core_entities" / "blocks"
        old = {
            "id": "11111111-1111-1111-1111-111111111111",
            "sequence_index": 1,
            "original_text": L1[K9:] + "\n" + L2,
            "source_refs": [
                {
                    "document_id": "d",
                    "part_id": "v1",
                    "page": 1,
                    "layout_block_id": None,
                    "char_range": [len(L0) + 1 + K9, len(PAGE1)],
                    "layer": "L4",
                }
            ],
            "status": "active",
            "metadata": {
                "part_id": "v1",
                "title": "九日",
                "kind": "date",
                "anchor": {"level": 2, "status": "approved", "confidence": 0.9, "l4_commit": "c0"},
            },
        }
        first = {
            **old,
            "id": "33333333-3333-3333-3333-333333333333",
            "sequence_index": 0,
            "original_text": L0 + "\n" + L1[:K9],
            "source_refs": [
                {
                    "document_id": "d",
                    "part_id": "v1",
                    "page": 1,
                    "layout_block_id": None,
                    "char_range": [0, len(L0) + 1 + K9],
                    "layer": "L4",
                }
            ],
            "metadata": {"part_id": "v1", "title": "七日"},
        }
        legacy = {
            **old,
            "id": "44444444-4444-4444-4444-444444444444",
            "status": "deprecated",
            "source_refs": [
                {
                    "document_id": "d",
                    "part_id": "v1",
                    "page": 1,
                    "layout_block_id": "p01_b01",
                    "char_range": None,
                    "layer": "L4",
                }
            ],
        }
        for blk in (old, first, legacy):
            (blocks / f"{blk['id']}.json").write_text(
                json.dumps(blk, ensure_ascii=False), encoding="utf-8"
            )
        assert B.needs_migration(interp)
        result = B.migrate_from_blocks(interp, lib)
        assert result["parts"] == [("d", "v1", 3)] and result["skipped"] == []
        assert not blocks.exists() and (interp / "core_entities" / "blocks_migrated_v1").exists()
        data = B.load_boundaries(interp, "d", "v1")
        ids = [b["id"] for b in data["boundaries"]]
        assert ids[0] == first["id"] and old["id"] in ids and legacy["id"] in ids
        nine = B.find_boundary(data, old["id"])
        assert nine["start"] == {"page": 1, "line": 1, "offset": K9} and nine["level"] == 2
        assert (
            nine["confidence"] == 0.9
            and nine["l4_commit"] == "c0"
            and nine["anchor_text"] == L1[K9 : K9 + 8]
        )
        # char_range 없는 옛 참조는 쪽 첫 행, deprecated는 단위를 만들지 않는다
        assert B.find_boundary(data, legacy["id"])["start"] == {"page": 1, "line": 0, "offset": 0}
        from src.core.segmentation import collect_document_lines

        lines, pt = collect_document_lines(lib / "documents" / "d", "v1", None)
        units = B.compute_units(data, lines, pt)
        assert [u["id"] for u in units] == [first["id"], old["id"]]
        assert units[1]["original_text"] == L1[K9:] + "\n" + L2  # 옛 본문과 같다 — 손실 없음
        assert not B.needs_migration(interp)


class TestEntityView:
    def test_entity_api_reads_and_writes_boundaries(self, tmp_path):
        from src.core import entity as E

        lib = tmp_path / "lib"
        doc = lib / "documents" / "d"
        (doc / "L4_text" / "pages").mkdir(parents=True)
        (doc / "manifest.json").write_text(
            json.dumps({"document_id": "d", "parts": [{"part_id": "v1", "page_count": 1}]}),
            encoding="utf-8",
        )
        (doc / "L4_text" / "pages" / "v1_page_001.txt").write_text(PAGE1, encoding="utf-8")
        interp = lib / "interpretations" / "i"
        (interp / "core_entities" / "works").mkdir(parents=True)
        (interp / "dependency.json").write_text(
            json.dumps({"source": {"document_id": "d"}}), encoding="utf-8"
        )
        created = E.create_entity(
            interp,
            "unit",
            {
                "sequence_index": 0,
                "original_text": "무시된다 — 본문은 L4에서 온다",
                "source_refs": [
                    {
                        "document_id": "d",
                        "part_id": "v1",
                        "page": 1,
                        "layout_block_id": None,
                        "char_range": [len(L0) + 1 + K9, len(PAGE1)],
                        "layer": "L4",
                    }
                ],
                "status": "draft",
                "metadata": {"part_id": "v1", "title": "九日", "anchor": {"level": 2}},
            },
        )
        # D-097: 경계는 원본 저장소에 산다 — 자리는 서고 루트 기준으로 적힌다
        assert created["file_path"] == "documents/d/boundaries/v1.json"
        assert not (interp / "core_entities" / "blocks").exists() or not list(
            (interp / "core_entities" / "blocks").glob("*.json")
        )
        got = E.get_entity(interp, "unit", created["id"])
        assert got["original_text"] == L1[K9:] + "\n" + L2 and got["metadata"]["title"] == "九日"
        assert E.list_entities(interp, "unit", {"status": "draft"})[0]["id"] == created["id"]
        E.update_entity(
            interp,
            "unit",
            created["id"],
            {"status": "active", "metadata": {"title": "九日談"}},
        )
        got2 = E.get_entity(interp, "unit", created["id"])
        assert got2["status"] == "active" and got2["metadata"]["title"] == "九日談"
        with pytest.raises(ValueError):
            E.update_entity(interp, "unit", created["id"], {"status": "draft"})  # 역전이 금지
        with pytest.raises(FileNotFoundError):
            E.get_entity(interp, "unit", "없는-id")
        tree = E.doc_contents(lib / "documents" / "d", "d")
        unit = tree["parts"][0]["units"][0]
        assert unit["level"] == 2 and unit["title"] == "九日談"


class TestRoles:
    """역할(role)은 깊이(level)와 따로 산다 — «기사»가 2단에도 3단에도 오는 책이 있다(D-092 후속).

    왜 시험하는가: 숫자에 뜻을 붙이면 다층 문집(集 > 卷 > 기사 > 협주)에서 기사가 3단으로
    내려가는 순간 번역·주석의 단위가 조용히 «조각»이 된다.
    """

    def test_new_boundary_keeps_only_known_roles(self):
        pos = {"page": 1, "line": 0, "offset": 0}
        assert B.new_boundary(pos, role="container")["role"] == "container"
        assert B.new_boundary(pos, role="기사")["role"] is None  # 모르는 값은 버린다
        assert B.new_boundary(pos)["role"] is None  # 안 주면 비워 두고 깊이로 추정

    def test_missing_role_is_guessed_from_level(self):
        assert B.role_for_level(1) == "container"
        assert B.role_for_level(2) == "article"
        assert B.role_for_level(7) == "fragment"
        lines, pt = _lines()
        old = B.new_boundary({"page": 1, "line": 0, "offset": 0}, level=1, page_texts=pt)
        del old["role"]  # 역할 칸이 없던 옛 파일
        units = B.compute_units(_data(old), lines, pt)
        assert units[0]["metadata"]["role"] == "container"

    def test_deep_level_can_still_be_an_article(self):
        lines, pt = _lines()
        b = B.new_boundary(
            {"page": 1, "line": 0, "offset": 0}, level=5, role="article", page_texts=pt
        )
        units = B.compute_units(_data(b), lines, pt)
        assert units[0]["metadata"]["level"] == 5
        assert units[0]["metadata"]["role"] == "article"

    def test_update_boundary_can_change_role(self):
        b = B.new_boundary({"page": 1, "line": 0, "offset": 0}, level=2, role="article")
        data = _data(b)
        B.update_boundary(data, b["id"], {"role": "fragment"})
        assert data["boundaries"][0]["role"] == "fragment"


def test_insert_at_same_place_and_level_is_idempotent():
    """경계 제안을 두 번 적용해도 같은 자리에 경계가 둘 생기지 않는다(먼저 있던 id가 남는다)."""
    a = B.new_boundary({"page": 1, "line": 0, "offset": 0}, level=2, title="a")
    data = _data(a)
    again = B.new_boundary({"page": 1, "line": 0, "offset": 0}, level=2, title="a2")
    kept = B.insert_boundary(data, again)
    assert kept is a and len(data["boundaries"]) == 1
    # 층위가 다르면 같은 자리라도 다른 경계다(기사 첫머리에 서는 조각)
    frag = B.new_boundary({"page": 1, "line": 0, "offset": 0}, level=3, title="frag")
    assert B.insert_boundary(data, frag) is frag and len(data["boundaries"]) == 2


class TestBoundariesLiveInTheDocument:
    """D-097 — 편성은 원본 저장소(문헌)의 것이다.

    무엇을 고정하는가:
      - 경계 파일 자리는 documents/{doc}/boundaries/{part}.json
      - 해석 저장소 안에 남아 있던 옛 경계는 열 때 문헌으로 옮겨지고, 옛 폴더는 이름만 바뀐다
      - 남의 문헌 파일은 옮기지 않는다(화면 버그로 섞여 든 것을 그 문헌의 편성으로 삼으면 안 된다)
      - 한 문헌에 해석 저장소가 둘이면 편성을 공유한다
    """

    def _lib(self, tmp_path: Path, doc_ids=("d",)):
        lib = tmp_path / "lib"
        for doc_id in doc_ids:
            doc = lib / "documents" / doc_id
            (doc / "L4_text" / "pages").mkdir(parents=True)
            (doc / "manifest.json").write_text(
                json.dumps(
                    {"document_id": doc_id, "parts": [{"part_id": "v1", "page_count": 1}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (doc / "L4_text" / "pages" / "v1_page_001.txt").write_text(PAGE1, encoding="utf-8")
        return lib

    def _interp(self, lib: Path, name: str, doc_id: str) -> Path:
        interp = lib / "interpretations" / name
        (interp / "core_entities").mkdir(parents=True)
        (interp / "dependency.json").write_text(
            json.dumps({"source": {"document_id": doc_id}}), encoding="utf-8"
        )
        return interp

    def _old_file(self, interp: Path, doc_id: str, part_id: str, boundaries: list) -> Path:
        """옛 자리(해석 저장소 안)에 경계 파일을 놓는다 — v1.3.0까지의 모습."""
        d = interp / "core_entities" / "boundaries"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{doc_id}__{part_id}.json"
        f.write_text(
            json.dumps(
                {"document_id": doc_id, "part_id": part_id, "boundaries": boundaries},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return f

    def test_save_goes_to_the_document_not_the_interpretation(self, tmp_path):
        lib = self._lib(tmp_path)
        interp = self._interp(lib, "i", "d")
        b = B.new_boundary({"page": 1, "line": 0, "offset": 0}, title="a")
        B.save_boundaries(interp, _data(b))
        assert (lib / "documents" / "d" / "boundaries" / "v1.json").exists()
        assert not (interp / "core_entities" / "boundaries").exists()

    def test_old_files_move_into_the_document_and_the_folder_is_kept(self, tmp_path):
        from src.core.entity import list_entities

        lib = self._lib(tmp_path)
        interp = self._interp(lib, "i", "d")
        b = B.new_boundary({"page": 1, "line": 0, "offset": 0}, title="옛것")
        self._old_file(interp, "d", "v1", [b])

        assert B.needs_boundary_move(interp) is True
        units = list_entities(interp, "unit")  # 열기만 해도 옮겨진다

        assert [u["metadata"]["title"] for u in units] == ["옛것"]
        assert (lib / "documents" / "d" / "boundaries" / "v1.json").exists()
        # 옛 폴더는 지우지 않는다 — 이름만 바꿔 남긴다(D-092와 같은 방식)
        assert not (interp / "core_entities" / "boundaries").exists()
        assert (interp / "core_entities" / "boundaries_migrated_v2" / "d__v1.json").exists()
        assert B.needs_boundary_move(interp) is False

    def test_a_foreign_documents_file_is_not_moved(self, tmp_path):
        """남의 문헌 경계는 옮기지 않는다 — 화면 버그로 섞여 들어간 것이기 때문이다."""
        lib = self._lib(tmp_path, ("d", "d2"))
        interp = self._interp(lib, "i", "d")
        b = B.new_boundary({"page": 1, "line": 0, "offset": 0}, title="남의 것")
        self._old_file(interp, "d2", "v1", [b])

        result = B.move_boundaries_to_document(interp)

        assert result["moved"] == []
        assert len(result["skipped"]) == 1 and "d" in result["skipped"][0]
        assert not (lib / "documents" / "d2" / "boundaries").exists()

    def test_the_document_wins_when_both_have_a_file(self, tmp_path):
        """문헌에 이미 편성이 있으면 덮어쓰지 않는다 — 먼저 옮겨진 것이 그 문헌의 편성이다."""
        lib = self._lib(tmp_path)
        first = self._interp(lib, "i1", "d")
        second = self._interp(lib, "i2", "d")
        B.save_boundaries(first, _data(B.new_boundary({"page": 1, "line": 0}, title="먼저")))
        self._old_file(second, "d", "v1", [B.new_boundary({"page": 1, "line": 1}, title="나중")])

        result = B.move_boundaries_to_document(second)

        assert result["moved"] == [] and len(result["kept"]) == 1
        data = B.load_boundaries(second, "d", "v1")
        assert [b["title"] for b in data["boundaries"]] == ["먼저"]

    def test_two_interpretations_share_one_composition(self, tmp_path):
        """원본 하나에 해석 저장소 여럿이면 편성은 하나다 (사용자 확인 2026-09-04)."""
        from src.core.entity import list_entities

        lib = self._lib(tmp_path)
        first = self._interp(lib, "i1", "d")
        second = self._interp(lib, "i2", "d")
        B.save_boundaries(first, _data(B.new_boundary({"page": 1, "line": 0}, title="한 기사")))

        assert [u["metadata"]["title"] for u in list_entities(second, "unit")] == ["한 기사"]


class TestWorkIsGone:
    """D-099 — Work 엔티티를 없앴다. 옛 폴더는 지우지 않고 이름만 바꾼다."""

    def test_work_is_not_an_entity_type_any_more(self):
        from src.core import entity as E

        assert "work" not in E.ENTITY_TYPES
        assert "work" not in E.SCHEMA_FILES
        assert not hasattr(E, "auto_create_work")

    def test_old_works_folder_is_retired_on_open(self, tmp_path):
        """저장소를 열면 works/가 works_removed_v1/로 바뀐다 — 지우지 않는다."""
        import json as _json

        from src.core import entity as E

        lib = tmp_path / "lib"
        doc = lib / "documents" / "d"
        (doc / "L4_text" / "pages").mkdir(parents=True)
        (doc / "manifest.json").write_text(
            _json.dumps({"document_id": "d", "parts": [{"part_id": "v1", "page_count": 1}]}),
            encoding="utf-8",
        )
        (doc / "L4_text" / "pages" / "v1_page_001.txt").write_text(PAGE1, encoding="utf-8")
        interp = lib / "interpretations" / "i"
        works = interp / "core_entities" / "works"
        works.mkdir(parents=True)
        (interp / "dependency.json").write_text(
            _json.dumps({"source": {"document_id": "d"}}), encoding="utf-8"
        )
        (works / "w1.json").write_text(
            _json.dumps({"id": "w1", "title": "옛 작품", "status": "draft"}), encoding="utf-8"
        )

        E.list_entities(interp, "unit")  # 열기만 해도 물린다

        assert not works.exists()
        retired = interp / "core_entities" / "works_removed_v1" / "w1.json"
        assert retired.exists()
        assert _json.loads(retired.read_text(encoding="utf-8"))["title"] == "옛 작품"

    # 코어 스키마에서 «없앤 엔티티»를 가리키던 필드 이름. D-099에서 Work를 없앴는데
    # unit.schema.json의 required에 `work_id`가 2026-09-07까지 남아 있었다 — 그 스키마는
    # 어떤 저장 경로에서도 검증에 쓰이지 않아(단위는 파일이 아니다) 아무것도 빨간불을 켜지
    # 않았다. 엔티티를 또 없애면 여기에 이름을 보태면 된다.
    REMOVED_ENTITIES = ("work",)

    @staticmethod
    def _walk_schema(node, found: set[str]):
        """스키마 트리를 돌며 properties의 키와 required의 항목을 모은다 ($defs 안까지)."""
        if isinstance(node, dict):
            for key in ("properties",):
                props = node.get(key)
                if isinstance(props, dict):
                    found.update(props.keys())
            req = node.get("required")
            if isinstance(req, list):
                found.update(str(r) for r in req)
            for v in node.values():
                TestWorkIsGone._walk_schema(v, found)
        elif isinstance(node, list):
            for v in node:
                TestWorkIsGone._walk_schema(v, found)

    def test_no_core_schema_still_refers_to_a_removed_entity(self):
        """없앤 엔티티의 스키마 파일도, 그것을 가리키는 `<이름>_id`·`scope_<이름>`도 남지 않는다.

        왜 test_doc_drift.py가 아니라 여기인가: 그 시험은 «셀 수 있는 수치»를 문서에서
        정규식으로 찾아 코드 실측과 견주는 검사기(scripts/check_doc_drift.py)를 그대로
        부른다. 스키마의 모양은 수치가 아니라 이름이라 그 검사기 틀에 맞지 않고, D-099의
        회귀는 이 클래스가 맡고 있다.
        """
        import json as _json

        core_dir = Path(__file__).resolve().parent.parent / "schemas" / "core"
        assert core_dir.is_dir()
        for name in self.REMOVED_ENTITIES:
            assert not (core_dir / f"{name}.schema.json").exists(), (
                f"없앤 엔티티 '{name}'의 스키마 파일이 아직 있다"
            )
            stale = {f"{name}_id", f"scope_{name}"}
            for schema_path in sorted(core_dir.glob("*.json")):
                found: set[str] = set()
                self._walk_schema(
                    _json.loads(schema_path.read_text(encoding="utf-8")), found
                )
                left = stale & found
                assert not left, (
                    f"{schema_path.name}이 없앤 엔티티 '{name}'을 아직 가리킨다: {sorted(left)}"
                )

    def test_unit_view_matches_unit_schema(self, tmp_path):
        """경계 목록에서 만든 단위(읽기 보기)가 unit.schema.json에 맞는다.

        왜: create/update 경로는 단위를 경계로 바꿔 저장하므로 `_validate_entity`가
        unit.schema.json을 열 일이 없다. 스키마가 «적어 둔 것»으로 남으면 D-099 뒤에도
        `work_id`가 required에 남듯 조용히 어긋난다(D-101의 문제 의식). 여기서 실제
        보기를 스키마에 넣어 본다.
        """
        import json as _json

        import jsonschema

        from src.core import entity as E

        lib = tmp_path / "lib"
        doc = lib / "documents" / "d"
        (doc / "L4_text" / "pages").mkdir(parents=True)
        (doc / "manifest.json").write_text(
            _json.dumps({"document_id": "d", "parts": [{"part_id": "v1", "page_count": 1}]}),
            encoding="utf-8",
        )
        (doc / "L4_text" / "pages" / "v1_page_001.txt").write_text(PAGE1, encoding="utf-8")
        interp = lib / "interpretations" / "i"
        (interp / "core_entities").mkdir(parents=True)
        (interp / "dependency.json").write_text(
            _json.dumps({"source": {"document_id": "d"}}), encoding="utf-8"
        )
        pt = {1: PAGE1}
        B.save_boundaries(
            interp,
            _data(
                B.new_boundary({"page": 1, "line": 0, "offset": 0}, title="七日", page_texts=pt),
                B.new_boundary(
                    {"page": 1, "line": 0, "offset": K8}, level=3, title="조각", page_texts=pt
                ),
                B.new_boundary({"page": 1, "line": 1, "offset": K9}, title="九日", page_texts=pt),
            ),
        )
        units = E.list_entities(interp, "unit")
        assert len(units) == 3

        schema_path = Path(__file__).resolve().parent.parent / "schemas" / "core" / "unit.schema.json"
        validator = jsonschema.Draft202012Validator(
            _json.loads(schema_path.read_text(encoding="utf-8")),
            format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
        )
        for u in units:
            errors = [e.message for e in validator.iter_errors(u)]
            assert not errors, f"단위 {u['id']}가 unit.schema.json에 어긋난다: {errors}"
