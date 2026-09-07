"""편성(composition) API 라우터 — 경계·제안·목차·적용·쪼개기.

편성은 **원본 저장소(문헌)의 것**이다 (D-097).

왜 여기에 따로 있는가:
    v1.3.0까지 편성은 해석 저장소의 엔티티였고 경로도 `/api/interpretations/…`였다.
    그런데 편성이 하는 일은 «이 원문이 어디서 글이 바뀌는가»를 정하는 것이지 해석이 아니다.
    한 문헌에 해석 저장소가 여럿이면 저장소마다 같은 편성을 다시 해야 했고, 화면은 문헌을
    고른 뒤 해석 저장소를 또 골라야 편성 탭이 열렸다. 편성을 문헌으로 옮기면 그 둘이 사라진다 —
    문헌 하나만 고르면 편성까지 가고, 그 문헌의 해석 저장소들은 같은 편성을 공유한다.
    표점·현토·번역·주석부터가 해석 저장소의 일이다.

저장 자리: documents/{doc_id}/boundaries/{part_id}.json (core.boundaries)
커밋 자리: 그 문헌 저장소. 해석 저장소는 dependency.json의 base_commit으로 «어느 편성을
          보고 해석했는가»를 가리킨다(fork/upstream).
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app._state import get_library_path, require_repo_path
from core.entity import doc_contents, doc_part_lines, doc_units

logger = logging.getLogger(__name__)

router = APIRouter(tags=["composition"])


# =========================================
#   Pydantic 모델 (요청 본문)
# =========================================


class SegmentationProposeRequest(BaseModel):
    """글 경계 제안 요청 (D-088)."""

    part_id: str
    pages: list[int] | None = None  # None이면 권 전체
    rules: dict | None = None  # None이면 manifest.segmentation_rules → 기본값
    # 목차 신호 (D-089). use_toc=True면 toc가 없을 때 규칙으로 자동 판별·추출한다.
    use_toc: bool = True
    toc: dict | None = None  # {"pages": [...], "entries": [{"title","level","page_hint"}]}


class SegmentationTocRequest(BaseModel):
    """목차 판별·추출 요청 (D-089). 아무것도 저장하지 않는다."""

    part_id: str
    toc_pages: list[int] | None = None  # None이면 앞쪽 쪽에서 자동 판별
    use_llm: bool = False  # True면 LLM으로 항목 구조화(실패 시 규칙으로)
    force_provider: str | None = None
    force_model: str | None = None


class SegmentationSpan(BaseModel):
    title: str
    kind: str = ""
    level: int | None = None  # 깊이 (D-092). 없으면 volume → 1, 그 밖 2
    role: str | None = None  # container·article·fragment. 없으면 깊이로 추정
    start: dict  # {"page": int, "line_index": int, "char_offset"?}
    end: dict


class SegmentationApplyRequest(BaseModel):
    """승인한 구간을 단위로 만든다."""

    part_id: str
    spans: list[SegmentationSpan]
    pages: list[int] | None = None  # 제안 때와 같은 범위여야 행 번호가 맞는다
    # 적용은 누적이 아니다 — «제안 패널의 체크 상태가 곧 트리»(사용자, 2026-09-03).
    # replace="proposal": 전에 제안으로 만든 경계 중 이번 선택에 없는 것은 지운다
    #   (손으로 넣은 것은 둔다).
    # replace="all": 이 권의 살아 있는 경계를 전부 지우고 새로 세운다(자동 트리).
    # replace="none": 예전처럼 더하기만.
    replace: str = "proposal"


class SegmentationAutoRequest(BaseModel):
    """자동 트리 — 목차 감지 → 제안 → 적용을 한 번에."""

    part_id: str
    use_llm_toc: bool | None = None  # None이면 저장된 규칙의 toc_llm을 따른다 (D-116)
    force_provider: str | None = None
    force_model: str | None = None
    replace: str = "all"
    toc_only: bool | None = None  # None이면 목차가 잡혔을 때만 True
    # 편성 탭 목차 줄(D-116): 껐으면 목차 감지·대조·LLM을 모두 건너뛰고, 쪽을 적었으면 그 쪽을 쓴다
    use_toc: bool = True
    toc_pages: list[int] | None = None


class SegmentationSignalsRequest(BaseModel):
    """전문에서 경계 규약(신호)을 세는 요청 (D-116·D-117). 저장하지 않는다."""

    part_id: str
    toc_pages: list[int] | None = None  # 사람이 적은 목차 쪽(없으면 규칙으로 판별)


class SegmentationSignalsLlmRequest(BaseModel):
    """4단 — LLM에 시작 표지의 공통점을 묻는 요청 (D-117). 저장하지 않는다."""

    part_id: str
    force_provider: str | None = None
    force_model: str | None = None


class BoundaryUpdateRequest(BaseModel):
    """단위의 경계를 옮기거나 제목·상태를 바꾼다 (D-090).

    start·end는 {"page", "line", "offset"}. offset은 행 안의 글자(2단계 — 澹齋日錄류처럼
    행 중간에서 날이 바뀌는 판식). start.offset 생략 = 행 첫머리, end.offset 생략 = 행 끝.
    shift_start·shift_end는 행 단위이며 옮긴 뒤 오프셋은 행 첫머리·행 끝이 된다.
    """

    title: str | None = None
    status: str | None = None
    start: dict | None = None  # {"page", "line", "offset"?}
    end: dict | None = None
    level: int | None = None  # 깊이 바꾸기 (D-092)
    role: str | None = None  # 역할 바꾸기: container·article·fragment
    shift_start: int | None = None
    shift_end: int | None = None


class BoundaryInsertRequest(BaseModel):
    """경계 넣기 (D-092) — 임의 행·행 중간에서 단위를 나눈다. 새 id는 뒤 단위(이 경계)에 붙는다."""

    part_id: str
    start: dict  # {"page", "line", "offset"}
    level: int = 2
    role: str | None = None  # container·article·fragment (없으면 깊이로 추정)
    title: str | None = None
    kind: str = "manual"


class SplitUnitRequest(BaseModel):
    """단위 쪼개기 요청 본문.

    쪼개기는 언제나 «기사 **안**을 문단으로 나누는» 일이다 — 별도 기사를 만들지 않는다
    (사용자 명시 2026-09-03). 기사 단위는 경계 제안·«경계 넣기»가 정한다.
    """

    original_unit_id: str
    part_id: str
    pieces: list[str]  # === 구분선으로 나눈 텍스트 조각들


class ResetCompositionRequest(BaseModel):
    """편성 리셋 요청 본문."""

    part_id: str
    unit_ids: list[str]  # deprecated로 전환할 단위 ID 목록


# =========================================
#   공통 헬퍼
# =========================================


def _doc(doc_id: str):
    """(문헌 경로, 오류 응답). 서고·문헌이 없으면 두 번째가 응답이다."""
    if get_library_path() is None:
        return None, JSONResponse({"error": "서고가 설정되지 않았습니다."}, status_code=500)
    doc_path = require_repo_path("documents", doc_id)
    if not doc_path.exists():
        return None, JSONResponse({"error": f"문헌을 찾을 수 없습니다: {doc_id}"}, status_code=404)
    return doc_path, None


def _document_head(doc_path) -> str | None:
    """원본 저장소의 현재 커밋. 앵커가 어느 확정본 기준인지 남긴다."""
    try:
        import git as _git

        return _git.Repo(doc_path).head.commit.hexsha
    except Exception:  # noqa: BLE001
        return None


def _boundary_rows(doc_path, document_id: str, part_id: str | None) -> list[dict]:
    """경계 색인 «보기» (D-090): 단위를 원본 위치 순서로 늘어놓고 행 앵커를 계산한다.

    경계는 별도 데이터가 아니다. 위치의 정본은 경계 목록이고, 행 번호·좌표는 여기서
    계산한다. 그래서 합치기·쪼개기·옮기기 어느 경로로 바꿔도 색인이 어긋나지 않는다.
    """
    from core.segmentation import anchor_from_refs

    rows = []
    page_cache: dict[tuple, dict] = {}
    for blk in doc_units(doc_path, document_id, part_id):
        if blk.get("status") == "deprecated":
            continue
        refs = [r for r in (blk.get("source_refs") or []) if r and r.get("page")]
        if not refs:
            continue
        pid = refs[0].get("part_id") or (blk.get("metadata") or {}).get("part_id")
        key = (document_id, pid or "")
        if key not in page_cache:
            _ls, texts = doc_part_lines(doc_path, document_id, pid) if pid else ([], {})
            page_cache[key] = texts
        anchor_pos = anchor_from_refs(refs, page_cache[key]) or {}
        meta = blk.get("metadata") or {}
        a = meta.get("anchor") or {}
        rows.append(
            {
                "id": blk["id"],
                "document_id": document_id,
                "part_id": pid,
                "sequence_index": blk.get("sequence_index"),
                "title": meta.get("title") or (blk.get("original_text") or "").strip()[:20],
                "kind": a.get("kind") or meta.get("kind") or "manual",
                "level": int(a.get("level", 2) or 2),
                "role": meta.get("role"),
                "role_estimated": bool(meta.get("role_estimated")),
                "status": a.get("status") or blk.get("status"),
                "anchor_status": a.get("status"),
                "unit_status": blk.get("status"),
                "confidence": a.get("confidence"),
                "reasons": a.get("reasons") or [],
                "start": anchor_pos.get("start"),
                "end": anchor_pos.get("end"),
                "bbox": a.get("bbox"),
                "l4_commit": a.get("l4_commit"),
            }
        )
    rows.sort(
        key=lambda r: (
            r["part_id"] or "",
            (r["start"] or {}).get("page", 0),
            (r["start"] or {}).get("line", 0),
            r["sequence_index"] or 0,
        )
    )
    for i, r in enumerate(rows):
        r["order"] = i
    return rows


def _is_proposal_boundary(b: dict) -> bool:
    """제안(날짜·어휘·목차·front)에서 온 경계인가.

    손으로 넣은 것(kind manual, source 없음)과 구분한다.
    """
    if (b.get("metadata") or {}).get("source") == "proposal":
        return True
    return (b.get("kind") or "manual") != "manual"


def _replace_boundaries(data: dict, spans, mode: str) -> int:
    """적용 전에 바꿔치기 대상 경계를 지운다. 지운 수를 돌려준다.

    proposal — 제안에서 온 경계 중 이번 선택(같은 자리·층위)에 없는 것.
    all — 살아 있는 경계 전부(자동 트리가 다시 세운다).
    none — 지우지 않는다(예전 동작).
    """
    if mode == "none":
        return 0
    keep_keys = set()
    for s in spans:
        st = s.start or {}
        keep_keys.add(
            (
                int(st.get("page", 0)),
                int(st.get("line_index", 0)),
                int(st.get("char_offset") or 0),
                int(s.level) if s.level else (1 if s.kind == "volume" else 2),
            )
        )
    before = data.get("boundaries") or []
    kept = []
    removed = 0
    for b in before:
        st = b.get("start") or {}
        key = (
            int(st.get("page", 0)),
            int(st.get("line", 0)),
            int(st.get("offset", 0)),
            int(b.get("level", 2)),
        )
        live = b.get("status") not in ("deprecated", "archived")
        if not live or key in keep_keys:
            kept.append(b)
            continue
        if mode == "all" or (mode == "proposal" and _is_proposal_boundary(b)):
            removed += 1
            continue
        kept.append(b)
    data["boundaries"] = kept
    return removed


def _find_home(doc_path, doc_id: str, unit_id: str, part_hint: str | None = None):
    """경계 id가 든 (권 id, 경계 목록, 항목). 없으면 (None, None, None).

    part_hint를 주면 그 권부터 본다 — 화면은 늘 보고 있는 권을 안다.
    """
    from core.boundaries import find_boundary, list_doc_parts, load_doc_boundaries

    parts = list_doc_parts(doc_path)
    if part_hint and part_hint in parts:
        parts = [part_hint] + [p for p in parts if p != part_hint]
    for pid in parts:
        data = load_doc_boundaries(doc_path, doc_id, pid)
        item = find_boundary(data, unit_id)
        if item is not None:
            return pid, data, item
    return None, None, None


def _dangling_tags(doc_id: str, unit_id: str) -> list[str]:
    """이 문헌의 해석 저장소들에서 지운 단위를 가리키고 있는 태그 id.

    편성은 문헌의 것이고 태그는 해석의 것이다(D-097) — 경계를 지우면 저장소마다 남는
    태그가 생길 수 있으므로, 지우지 않고 응답에 실어 사람이 옮기게 한다.
    """
    from core.boundaries import document_of
    from core.entity import list_entities

    lib = get_library_path()
    root = (lib / "interpretations") if lib else None
    if root is None or not root.exists():
        return []
    out: list[str] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or document_of(d) != doc_id:
            continue
        try:
            out += [t["id"] for t in list_entities(d, "tag") if t.get("block_id") == unit_id]
        except Exception as e:  # noqa: BLE001 — 한 저장소의 실패가 지우기를 막지 않는다
            logger.warning("태그를 읽지 못했습니다 (%s): %s", d.name, e)
    return out


# =========================================
#   경계 제안·목차 (아무것도 저장하지 않는다)
# =========================================


@router.post("/api/documents/{doc_id}/segmentation/propose")
async def api_segmentation_propose(doc_id: str, body: SegmentationProposeRequest):
    """L4 확정 텍스트에서 글 단위 경계 후보를 제안한다 (D-088). 아무것도 저장하지 않는다.

    목적: 일기·담초처럼 글마다 표제가 서는 문헌에서 «어디서 글이 바뀌는가»를 기계가
          먼저 찍고, 사용자가 승인한 것만 단위가 된다.
    입력: part_id, pages(None=전체), rules(None=문헌 설정 → 기본값).
    출력: core.segmentation.propose_boundaries() 결과 + "lines"(화면 표시용 행 목록).
    """
    from core.document import get_document_info
    from core.segmentation import collect_document_lines, normalize_rules, propose_boundaries

    doc_path, err = _doc(doc_id)
    if err is not None:
        return err

    rules = body.rules
    if rules is None:
        try:
            rules = get_document_info(doc_path).get("segmentation_rules")
        except FileNotFoundError:
            rules = None
    rules = normalize_rules(rules)

    lines, page_texts = collect_document_lines(doc_path, body.part_id, body.pages)
    if not lines:
        return JSONResponse(
            {"error": "확정 텍스트(L4)가 있는 쪽이 없습니다. OCR·교정을 먼저 하세요."},
            status_code=400,
        )

    # 목차 신호 (D-089): 목차 쪽은 본문 후보에서 빼고, 항목을 본문 행에 순서대로 대응시킨다
    toc_info = None
    toc_matches = None
    if body.use_toc:
        from core.toc import (
            TocEntry,
            align_toc_to_body,
            detect_toc_pages,
            extract_toc_entries_rule,
        )

        page_lines = {p: t.split("\n") for p, t in page_texts.items()}
        if body.toc and body.toc.get("entries"):
            toc_pages = [int(p) for p in (body.toc.get("pages") or [])]
            entries = [
                TocEntry(
                    title=str(e.get("title", "")).strip(),
                    level=int(e.get("level", 2)),
                    page_hint=e.get("page_hint"),
                )
                for e in body.toc["entries"]
                if str(e.get("title", "")).strip()
            ]
        else:
            toc_pages = detect_toc_pages(page_lines, rules["max_title_chars"])
            entries = extract_toc_entries_rule(page_lines, toc_pages) if toc_pages else []
        if entries:
            body_lines = [ln for ln in lines if ln.page not in set(toc_pages)]
            matches, unmatched = align_toc_to_body(entries, body_lines)
            toc_matches = [m.to_dict() for m in matches]
            toc_info = {
                "pages": toc_pages,
                "entries": [e.to_dict() for e in entries],
                "matches": toc_matches,
                "unmatched": [entries[i].to_dict() | {"index": i} for i in unmatched],
            }
            lines = body_lines
            for p in toc_pages:
                page_texts.pop(p, None)

    result = propose_boundaries(lines, rules, toc_matches=toc_matches)
    result["lines"] = [
        {"page": ln.page, "line_index": ln.line_index, "text": ln.text} for ln in lines
    ]
    result["pages"] = sorted(page_texts)
    result["toc"] = toc_info
    return result


@router.post("/api/documents/{doc_id}/segmentation/signals")
async def api_segmentation_signals(doc_id: str, body: SegmentationSignalsRequest):
    """전문에서 이 책의 경계 규약을 센다 (D-116). 아무것도 저장하지 않는다.

    목적: 책마다 다른 글의 시작 표지(○+날짜·談草·卷頭·행머리 어휘)를 코드가 아니라 전문에서
          찾아 신호 목록으로 돌려준다. 화면은 이것을 체크박스로 보이고, 고른 것이 규칙이 된다.
    입력: part_id.
    출력: core.rule_induction.induce_signals() 결과 + "source"(L4·L2 쪽 수)
          + "saved_rules"(지금 저장된 규칙 — 체크 상태의 출발점) + "recommended_rules".
    확정본(L4)이 없는 쪽은 OCR 결과(L2)로 센다 — 규약을 세는 데는 글자 몇이 틀려도 되지만,
    제안·적용은 L4만 읽으므로 source를 화면이 알려 준다.
    """
    from core.document import get_document_info
    from core.rule_induction import collect_lines_any_layer, induce_signals, rules_from_signals
    from core.segmentation import normalize_rules

    doc_path, err = _doc(doc_id)
    if err is not None:
        return err
    try:
        saved = get_document_info(doc_path).get("segmentation_rules")
    except FileNotFoundError:
        saved = None
    lines, source = collect_lines_any_layer(doc_path, body.part_id)
    if not lines:
        return JSONResponse(
            {"error": "텍스트가 있는 쪽이 없습니다. OCR을 먼저 하세요."}, status_code=400
        )
    # 1단 — 목차부터(D-117). 사람이 쪽을 적었으면 그 쪽으로 대조한다.
    toc = _toc_signal_for(lines, saved, body.toc_pages)
    result = induce_signals(lines, saved, toc=toc)
    result["source"] = source
    result["saved_rules"] = normalize_rules(saved) if saved else None
    result["recommended_rules"] = rules_from_signals(result, saved)
    return result


def _toc_signal_for(lines, rules, toc_pages: list[int] | None):
    """층계 1단 — 목차 요약. 사람이 쪽을 적었으면 판별을 건너뛰고 그 쪽으로 대조한다."""
    from core.rule_induction import toc_decisive, toc_signal
    from core.segmentation import normalize_rules
    from core.toc import align_toc_to_body, extract_toc_entries_rule

    if not toc_pages:
        return toc_signal(lines, rules)
    rules = normalize_rules(rules)
    page_lines: dict[int, list[str]] = {}
    for ln in lines:
        page_lines.setdefault(ln.page, []).append(ln.text)
    pages = [int(p) for p in toc_pages if int(p) in page_lines]
    entries = extract_toc_entries_rule(page_lines, pages) if pages else []
    if not entries:
        return None
    body_lines = [ln for ln in lines if ln.page not in set(pages) and ln.text.strip()]
    matches, _un = align_toc_to_body(entries, body_lines)
    ratio = len(matches) / max(1, len(entries))
    return {
        "pages": pages,
        "entries": len(entries),
        "matched": len(matches),
        "ratio": round(ratio, 2),
        "decisive": toc_decisive(len(matches), len(entries)),
    }


@router.post("/api/documents/{doc_id}/segmentation/signals/llm")
async def api_segmentation_signals_llm(doc_id: str, body: SegmentationSignalsLlmRequest):
    """층계 4단 (D-117): 통계가 못 찾은 책 — LLM에 «시작 표지의 공통점»을 묻는다. 저장하지 않는다.

    모델은 경계를 찍지 않는다. 표본 행(짧은 행·행갈음 뒤·내려쓴 행 ≤ 80줄)만 보고 정해진
    종류(행머리 어휘·행끝 어휘·기호·없음)로 답하고, 코드가 전문에서 세어 되풀이되는 것만
    신호 행으로 돌려준다. 화면은 그 행을 신호 목록에 넣고, 켤지는 사람이 정한다.
    출력: {"signals": [...], "provider", "model", "error", "raw", "note", "sample_count"}
    """
    from app._state import _get_llm_router
    from core.document import get_document_info
    from core.rule_induction import (
        collect_lines_any_layer,
        extract_start_patterns_llm,
        sample_start_lines,
    )
    from core.segmentation import normalize_rules

    doc_path, err = _doc(doc_id)
    if err is not None:
        return err
    try:
        rules = normalize_rules(get_document_info(doc_path).get("segmentation_rules"))
    except FileNotFoundError:
        rules = normalize_rules(None)
    lines, _source = collect_lines_any_layer(doc_path, body.part_id)
    if not lines:
        return JSONResponse(
            {"error": "텍스트가 있는 쪽이 없습니다. OCR을 먼저 하세요."}, status_code=400
        )
    rows, meta = await extract_start_patterns_llm(
        lines,
        rules,
        _get_llm_router(),
        body.force_provider,
        body.force_model,
        reference_text=rules.get("reference_text") or "",
    )
    return {"signals": rows, "sample_count": len(sample_start_lines(lines, rules)), **meta}


@router.post("/api/documents/{doc_id}/segmentation/toc")
async def api_segmentation_toc(doc_id: str, body: SegmentationTocRequest):
    """목차 쪽을 판별하고 항목을 뽑는다 (D-089). 저장하지 않는다.

    규칙(짧은 행 비율·目錄/卷之 표지·葉 번호 꼬리)으로 앞쪽 쪽을 고르고, use_llm이면 LLM이
    항목을 구조화한다(텍스트만 넘긴다 — 비전 불필요, JSON 강제, 사고 끔). 실패하면 규칙 추출.
    출력: {"toc_pages", "entries", "method", "meta"}
    """
    from app._state import _get_llm_router
    from core.segmentation import collect_document_lines
    from core.toc import detect_toc_pages, extract_toc_entries_llm, extract_toc_entries_rule

    doc_path, err = _doc(doc_id)
    if err is not None:
        return err

    _lines, page_texts = collect_document_lines(doc_path, body.part_id, None)
    if not page_texts:
        return JSONResponse({"error": "확정 텍스트(L4)가 있는 쪽이 없습니다."}, status_code=400)
    page_lines = {p: t.split("\n") for p, t in page_texts.items()}
    toc_pages = body.toc_pages or detect_toc_pages(page_lines)
    if not toc_pages:
        return {
            "toc_pages": [],
            "entries": [],
            "method": "rule",
            "meta": {"reason": "목차로 보이는 쪽이 없습니다"},
        }
    if body.use_llm:
        # 사람이 붙여 넣은 해제·참고 텍스트(문헌 설정)를 LLM에 같이 준다
        try:
            from core.document import get_document_info
            from core.segmentation import normalize_rules

            _rules = normalize_rules(get_document_info(doc_path).get("segmentation_rules"))
            reference_text = _rules.get("reference_text") or ""
        except Exception:  # noqa: BLE001 — 참고는 없어도 된다
            reference_text = ""
        entries, meta = await extract_toc_entries_llm(
            page_lines,
            toc_pages,
            _get_llm_router(),
            body.force_provider,
            body.force_model,
            reference_text=reference_text,
        )
    else:
        entries, meta = extract_toc_entries_rule(page_lines, toc_pages), {"method": "rule"}
    return {
        "toc_pages": toc_pages,
        "entries": [e.to_dict() for e in entries],
        "method": meta.get("method", "rule"),
        "meta": meta,
    }


# =========================================
#   경계 적용·자동 트리 (문헌 저장소에 쓴다)
# =========================================


@router.post("/api/documents/{doc_id}/segmentation/apply")
async def api_segmentation_apply(doc_id: str, body: SegmentationApplyRequest):
    """승인한 구간들을 경계로 만든다 (D-088).

    D-092: 구간 하나 = 경계 하나. 행 목록은 한 번만 읽고, 경계 파일은 한 번만 쓰고, 커밋도 한 번.
    전에는 구간마다 create_entity → 권 전체 확정본을 다시 읽어(208쪽 0.6초) 43구간에 30초가
    걸렸고, 그 사이에 «내용 새로고침»을 누르면 아직 없는 것으로 보였다(실측 2026-09-03).
    """
    from core.boundaries import (
        git_commit_boundaries,
        insert_boundary,
        load_doc_boundaries,
        new_boundary,
        save_doc_boundaries,
    )
    from core.segmentation import boundary_bbox, collect_document_lines

    doc_path, err = _doc(doc_id)
    if err is not None:
        return err
    if not body.spans:
        return JSONResponse({"error": "적용할 구간이 없습니다."}, status_code=400)

    lines, page_texts = collect_document_lines(doc_path, body.part_id, body.pages)
    keys = [(ln.page, ln.line_index) for ln in lines]
    l4_commit = _document_head(doc_path)
    data = load_doc_boundaries(doc_path, doc_id, body.part_id)
    removed = _replace_boundaries(data, body.spans, body.replace)
    created = []
    errors = []
    for span in body.spans:
        s = span.start or {}
        key = (int(s.get("page", 0)), int(s.get("line_index", 0)))
        if key not in keys:
            errors.append(f"구간을 찾을 수 없습니다: {span.title}")
            continue
        start = {"page": key[0], "line": key[1], "offset": int(s.get("char_offset") or 0)}
        level = int(span.level) if span.level else (1 if span.kind == "volume" else 2)
        item = new_boundary(
            start=start,
            level=level,
            role=span.role or None,
            title=span.title or None,
            kind=span.kind or "manual",
            status="draft",
            anchor_status="approved",
            page_texts=page_texts,
            l4_commit=l4_commit,
            bbox=boundary_bbox(
                doc_path,
                body.part_id,
                {"page": start["page"], "line": start["line"], "offset": start["offset"]},
                {"page": start["page"], "line": start["line"], "offset": None},
            ),
        )
        item["metadata"] = {"source": "proposal"}  # 제안에서 온 경계 — 다음 적용 때 바꿔치기 대상
        try:
            kept = insert_boundary(data, item)  # 같은 자리·층위가 있으면 그것(중복 없음)
            created.append(
                {"id": kept["id"], "title": kept.get("title"), "sequence_index": len(created)}
            )
        except Exception as e:  # noqa: BLE001 — 한 구간의 실패가 나머지를 막지 않는다
            errors.append(f"{span.title}: {e}")
    git = None
    if created or removed:
        save_doc_boundaries(doc_path, data)
        git = git_commit_boundaries(
            doc_path,
            f"feat: 경계 제안 적용 — 경계 {len(created)}개, 바꿔치기로 {removed}개 제거 "
            "(D-088·D-092)",
        )
    return {"created": created, "removed": removed, "errors": errors, "git": git}


@router.post("/api/documents/{doc_id}/segmentation/auto")
async def api_segmentation_auto(doc_id: str, body: SegmentationAutoRequest):
    """자동 트리: 목차 감지 → (LLM 구조화) → 경계 제안(층위 추정) → 승인된 것을 적용(바꿔치기).

    사용자가 원한 것: Workflowy처럼 사이드바에 개요가 자동으로 서고, 그 안에서 고친다.
    편성 탭의 제안 패널은 검토용이고 이것이 기본 경로다.
    출력: {"toc_pages", "proposals", "accepted", "applied", "removed", "unmatched_toc", "git"}
    """
    from app._state import _get_llm_router
    from core.document import get_document_info
    from core.rule_induction import (
        collect_lines_any_layer,
        induce_signals,
        induction_found_something,
        rules_are_empty,
        rules_from_signals,
        save_segmentation_rules,
    )
    from core.segmentation import (
        _list_part_pages,
        collect_document_lines,
        normalize_rules,
        propose_boundaries,
    )
    from core.toc import (
        align_toc_to_body,
        detect_toc_pages,
        extract_toc_entries_llm,
        extract_toc_entries_rule,
    )

    doc_path, err = _doc(doc_id)
    if err is not None:
        return err
    try:
        saved_rules = get_document_info(doc_path).get("segmentation_rules")
    except FileNotFoundError:
        saved_rules = None
    induced = None
    if rules_are_empty(saved_rules):
        # 아직 규칙이 없으면 전문에서 먼저 찾아 저장한다(D-116). 사람이 화면에서 고친 것이
        # 있으면(origin이 있으면) 그것을 따르고 다시 세지 않는다.
        all_lines, _src = collect_lines_any_layer(doc_path, body.part_id)
        if all_lines:
            # 층계 1단(D-117): 목차가 규약인 책은 텍스트 규약을 저장하지 않는다
            toc_sig = (
                _toc_signal_for(all_lines, saved_rules, body.toc_pages) if body.use_toc else None
            )
            induced = induce_signals(all_lines, saved_rules, toc=toc_sig)
            saved_rules = rules_from_signals(induced, saved_rules)
            # 아무것도 못 찾았으면 저장하지 않는다 — 확정본이 늘면 다음 자동 트리가 다시 센다
            if induction_found_something(induced):
                save_segmentation_rules(doc_path, saved_rules)
    rules = normalize_rules(saved_rules)
    use_llm_toc = rules["toc_llm"] if body.use_llm_toc is None else bool(body.use_llm_toc)
    lines, page_texts = collect_document_lines(doc_path, body.part_id, None)
    if not lines:
        return JSONResponse(
            {"error": "확정 텍스트(L4)가 있는 쪽이 없습니다. OCR·교정을 먼저 하세요."},
            status_code=400,
        )
    # 규칙은 확정본(L4)만 읽는다. 일부 쪽에만 L4가 있으면 나머지 쪽의 날짜·권점은 보지도 못하고
    # 「후보 0」이 나온다 — 그 사실을 화면이 말해 주어야 한다(浩齋辰巳日錄 실측 2026-09-06:
    # OCR 77쪽 중 L4는 序 한 쪽이어서 날짜 340개를 두고 개요가 비었다).
    pages_total = len(_list_part_pages(doc_path, body.part_id, get_document_info))
    page_lines = {p: t.split("\n") for p, t in page_texts.items()}
    if not body.use_toc:
        toc_pages = []
    elif body.toc_pages:
        toc_pages = [int(p) for p in body.toc_pages if int(p) in page_lines]
    else:
        toc_pages = detect_toc_pages(page_lines, rules["max_title_chars"])
    toc_matches = None
    unmatched = []
    toc_meta = None
    if toc_pages:
        if use_llm_toc:
            entries, toc_meta = await extract_toc_entries_llm(
                page_lines,
                toc_pages,
                _get_llm_router(),
                body.force_provider,
                body.force_model,
                reference_text=rules.get("reference_text") or "",
            )
        else:
            entries = extract_toc_entries_rule(page_lines, toc_pages)
        body_lines = [ln for ln in lines if ln.page not in set(toc_pages)]
        matches, un = align_toc_to_body(entries, body_lines)
        toc_matches = [m.to_dict() for m in matches]
        unmatched = [entries[i].to_dict() for i in un]
        lines = body_lines
    result = propose_boundaries(lines, rules, toc_matches=toc_matches)
    toc_only = body.toc_only if body.toc_only is not None else bool(toc_matches)
    # 목차만 고르는 기본값에서도 卷 표제(kind="volume")는 남긴다 — 빠지면 트리에 묶음이 없다
    chosen = [
        p
        for p in result["proposals"]
        if p["accepted"]
        and (
            not toc_only or p["kind"] == "volume" or any(r.startswith("toc:") for r in p["reasons"])
        )
    ]
    # 구간은 «다음 선택 경계 앞까지»가 아니라 경계 목록이 알아서 정하므로 시작만 넘기면 된다
    spans = [
        SegmentationSpan(
            title=p["title"],
            kind=p["kind"] or "",
            level=int(p.get("level") or 2),
            role=p.get("role"),
            start={
                "page": p["page"],
                "line_index": p["line_index"],
                "char_offset": p.get("char_offset") or 0,
            },
            end={"page": p["page"], "line_index": p["line_index"], "char_end": None},
        )
        for p in chosen
    ]
    applied = await api_segmentation_apply(
        doc_id,
        SegmentationApplyRequest(
            part_id=body.part_id,
            spans=spans,
            replace=body.replace,
        ),
    )
    if isinstance(applied, JSONResponse):
        return applied
    return {
        "toc_pages": toc_pages,
        "toc_meta": toc_meta,
        "llm_toc": bool(use_llm_toc),
        # 이번에 규칙을 새로 찾았으면 무엇을 켰는지 — 화면 토스트가 «○+날짜·談草로 세웠다»고 말한다
        "induced": ([s["id"] for s in induced["signals"] if s["recommended"]] if induced else None),
        "stage": induced["stage"] if induced else None,
        "rules_origin": rules["origin"],
        "pages_total": pages_total,
        "pages_with_text": len(page_texts),
        "proposals": len(result["proposals"]),
        "accepted": sum(1 for p in result["proposals"] if p["accepted"]),
        "toc_only": toc_only,
        "applied": len(applied.get("created", [])),
        "removed": applied.get("removed", 0),
        "errors": applied.get("errors", []),
        "unmatched_toc": unmatched,
        "git": applied.get("git"),
    }


# =========================================
#   경계 색인 (읽기)
# =========================================


@router.get("/api/documents/{doc_id}/boundaries/export.csv")
async def api_export_boundaries_csv(doc_id: str, part_id: str | None = Query(None)):
    """경계 색인을 CSV로 (D-090). 열 이름은 연구자 DB의 article_index 관례. UTF-8 BOM."""
    import csv
    import io as _io

    from fastapi.responses import Response

    doc_path, err = _doc(doc_id)
    if err is not None:
        return err
    rows = _boundary_rows(doc_path, doc_id, part_id)
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "기사id",
            "문헌",
            "권",
            "순서",
            "유형",
            "층위",
            "제목",
            "시작쪽",
            "시작행",
            "끝쪽",
            "끝행",
            "상태",
            "신뢰도",
            "근거",
            "l4_commit",
        ]
    )
    for r in rows:
        s_, e_ = r.get("start") or {}, r.get("end") or {}
        w.writerow(
            [
                r["id"],
                r.get("document_id"),
                r.get("part_id"),
                r["order"],
                r.get("kind"),
                r.get("level", 2),
                r.get("title"),
                s_.get("page", ""),
                s_.get("line", ""),
                e_.get("page", ""),
                e_.get("line", ""),
                r.get("status"),
                r.get("confidence") if r.get("confidence") is not None else "",
                " ".join(r.get("reasons") or []),
                (r.get("l4_commit") or "")[:12],
            ]
        )
    data = ("﻿" + buf.getvalue()).encode("utf-8")
    name = f"boundaries_{doc_id}{('_' + part_id) if part_id else ''}.csv"
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/api/documents/{doc_id}/contents")
async def api_contents_tree(doc_id: str):
    """내용 트리 — 문헌 > 권 > 단위 (D-085 → B-004).

    목적: 교감 뒤에는 쪽이 아니라 내용으로 찾아가야 한다. 사이드바 「내용」 트리가 이 응답으로
          그려지고, 단위를 누르면 pages[].page로 가며 해석 편집기 다섯이 그 단위로 맞춰진다(D-096).
    출력: core.entity.doc_contents() 참조.

    왜 Work로 묶지 않는가: Work(저작)는 해석 저장소의 엔티티인데 편성은 문헌의 것이다(D-097).
    저작이 여럿인 문집은 층위 1의 «묶음» 경계가 나타낸다 — 트리의 층위 그대로다(B-004).
    """
    doc_path, err = _doc(doc_id)
    if err is not None:
        return err
    try:
        return doc_contents(doc_path, doc_id)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"내용 트리 조회 실패: {e}"}, status_code=400)


@router.get("/api/documents/{doc_id}/boundaries")
async def api_list_boundaries(doc_id: str, part_id: str | None = Query(None)):
    """경계 색인 보기 (D-090): 단위를 원본 위치 순서로, 시작·끝 행과 좌표 캐시를 붙여."""
    doc_path, err = _doc(doc_id)
    if err is not None:
        return err
    rows = _boundary_rows(doc_path, doc_id, part_id)
    return {"boundaries": rows, "total": len(rows)}


# =========================================
#   경계 넣기·옮기기·지우기
# =========================================


@router.post("/api/documents/{doc_id}/boundaries")
async def api_insert_boundary(doc_id: str, body: BoundaryInsertRequest):
    """경계를 넣는다 = 그 자리에서 단위를 쪼갠다. 앞 단위의 id는 그대로, 새 id는 뒤 단위에."""
    from core.boundaries import (
        git_commit_boundaries,
        insert_boundary,
        load_doc_boundaries,
        new_boundary,
        save_doc_boundaries,
    )
    from core.segmentation import boundary_bbox, collect_document_lines

    doc_path, err = _doc(doc_id)
    if err is not None:
        return err
    lines, page_texts = collect_document_lines(doc_path, body.part_id, None)
    keys = [(ln.page, ln.line_index) for ln in lines]
    start = {
        "page": int(body.start.get("page", 0)),
        "line": int(body.start.get("line", 0)),
        "offset": int(body.start.get("offset") or 0),
    }
    if (start["page"], start["line"]) not in keys:
        return JSONResponse({"error": "그 쪽·행에 확정 텍스트(L4)가 없습니다."}, status_code=400)
    data = load_doc_boundaries(doc_path, doc_id, body.part_id)
    item = new_boundary(
        start=start,
        level=max(1, int(body.level)),
        role=body.role or None,
        title=body.title
        or (
            lines[keys.index((start["page"], start["line"]))].text[start["offset"] :].strip()[:20]
            or None
        ),
        kind=body.kind or "manual",
        status="draft",
        page_texts=page_texts,
        l4_commit=_document_head(doc_path),
    )
    item["bbox"] = boundary_bbox(
        doc_path,
        body.part_id,
        {"page": start["page"], "line": start["line"], "offset": start["offset"]},
        {"page": start["page"], "line": start["line"], "offset": None},
    )
    try:
        kept = insert_boundary(data, item)  # 같은 자리·층위가 이미 있으면 그것을 돌려준다
    except FileExistsError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    existing = kept is not item
    git = None
    if not existing:
        save_doc_boundaries(doc_path, data)
        git = git_commit_boundaries(
            doc_path, f"feat: 경계 넣기 — {item.get('title') or item['id'][:8]} (D-092)"
        )
    row = next(
        (r for r in _boundary_rows(doc_path, doc_id, body.part_id) if r["id"] == kept["id"]),
        None,
    )
    return {"boundary": row, "existing": existing, "git": git}


@router.put("/api/documents/{doc_id}/boundaries/{unit_id}")
async def api_update_boundary(
    doc_id: str,
    unit_id: str,
    body: BoundaryUpdateRequest,
    part_id: str | None = Query(None),
):
    """경계를 옮기거나 제목·층위·역할·상태를 바꾼다.

    행 단위. 끝은 저장하지 않으므로 «끝을 옮긴다»는 곧 «다음 경계(같은 층위 이상)를 옮긴다»이다.
    """
    from core.boundaries import (
        find_boundary,
        git_commit_boundaries,
        move_boundary,
        save_doc_boundaries,
        unit_end,
        update_boundary,
    )
    from core.segmentation import boundary_bbox, collect_document_lines

    doc_path, err = _doc(doc_id)
    if err is not None:
        return err
    pid, data, item = _find_home(doc_path, doc_id, unit_id, part_id)
    if item is None:
        return JSONResponse({"error": f"경계를 찾을 수 없습니다: {unit_id}"}, status_code=404)
    lines, page_texts = collect_document_lines(doc_path, pid, None)
    keys = [(ln.page, ln.line_index) for ln in lines]
    if not keys:
        return JSONResponse({"error": "확정 텍스트(L4)가 없습니다."}, status_code=400)

    def _norm(pos: dict | None, fallback: dict) -> dict:
        if not pos:
            return dict(fallback)
        return {
            "page": int(pos.get("page", fallback["page"])),
            "line": int(pos.get("line", 0)),
            "offset": int(pos.get("offset") or 0),
        }

    def _shift_lines(pos: dict, delta: int) -> dict:
        k = (int(pos["page"]), int(pos["line"]))
        i = keys.index(k) if k in keys else 0
        i = max(0, min(len(keys) - 1, i + int(delta)))
        return {"page": keys[i][0], "line": keys[i][1], "offset": 0}

    touched: list[str] = []
    start_moved = False
    start = _norm(body.start, item["start"])
    if body.shift_start:
        start = _shift_lines(start, body.shift_start)
    if (int(start["page"]), int(start["line"])) not in keys:
        return JSONResponse({"error": "시작 행이 현재 확정본에 없습니다."}, status_code=400)
    if start != item["start"]:
        move_boundary(data, unit_id, start, page_texts)
        item["l4_commit"] = _document_head(doc_path)
        touched.append(unit_id)
        start_moved = True
    if body.end or body.shift_end:
        bounds = data["boundaries"]
        idx = next(i for i, b in enumerate(bounds) if b.get("id") == unit_id)
        nxt_start = unit_end(bounds, idx)
        _dead = ("deprecated", "archived")
        nxt = next(
            (
                b
                for b in bounds[idx + 1 :]
                if b.get("start") == nxt_start and b.get("status") not in _dead
            ),
            None,
        )
        if nxt is not None:
            # end는 «이 단위의 마지막 자리»이므로 다음 경계는 그 바로 뒤
            # (end.offset이 있으면 그 글자)
            if body.end:
                e = _norm(body.end, nxt["start"])
                new_next = e if e.get("offset") else _shift_lines(e, 1)
            else:
                new_next = _shift_lines(nxt["start"], body.shift_end)
            if new_next != nxt["start"]:
                move_boundary(data, nxt["id"], new_next, page_texts)
                nxt["l4_commit"] = _document_head(doc_path)
                touched.append(nxt["id"])
    fields: dict = {}
    if body.title is not None:
        fields["title"] = body.title
    if body.status is not None:
        fields["anchor_status"] = body.status
    if body.level is not None:
        fields["level"] = max(1, int(body.level))
    if body.role is not None:
        if body.role not in ("container", "article", "fragment"):
            return JSONResponse(
                {"error": "role은 container·article·fragment 중 하나입니다."}, status_code=400
            )
        fields["role"] = body.role
    if fields:
        update_boundary(data, unit_id, fields)
        if unit_id not in touched:
            touched.append(unit_id)
    # 시작 행의 L2 좌표 캐시(화면 표시용). 시작이 그대로면 다시 재지 않는다 —
    # 제목·역할·층위만 바꿔도 L2를 읽어 오던 군더더기였다.
    b = find_boundary(data, unit_id)
    if start_moved or b.get("bbox") is None:
        b["bbox"] = boundary_bbox(
            doc_path,
            pid,
            {
                "page": b["start"]["page"],
                "line": b["start"]["line"],
                "offset": b["start"]["offset"],
            },
            {"page": b["start"]["page"], "line": b["start"]["line"], "offset": None},
        )
    save_doc_boundaries(doc_path, data)
    title = b.get("title") or unit_id[:8]
    git = git_commit_boundaries(doc_path, f"fix: 경계 수정 — {title} (D-092)")
    row = next((r for r in _boundary_rows(doc_path, doc_id, pid) if r["id"] == unit_id), None)
    return {"boundary": row, "touched": touched, "git": git}


@router.delete("/api/documents/{doc_id}/boundaries/{unit_id}")
async def api_delete_boundary(doc_id: str, unit_id: str, part_id: str | None = Query(None)):
    """경계를 지운다 = 그 단위를 앞 단위에 합친다. 앞 단위의 id가 남는다 (D-092).

    관계·태그가 지운 id를 가리키고 있으면 그대로 두고 응답에 알린다 — 사람이 옮긴다.
    """
    from core.boundaries import delete_boundary, git_commit_boundaries, save_doc_boundaries

    doc_path, err = _doc(doc_id)
    if err is not None:
        return err
    pid, data, item = _find_home(doc_path, doc_id, unit_id, part_id)
    if item is None:
        return JSONResponse({"error": f"경계를 찾을 수 없습니다: {unit_id}"}, status_code=404)
    ids = [b["id"] for b in data["boundaries"]]
    idx = ids.index(unit_id)
    prev_id = next(
        (
            b["id"]
            for b in reversed(data["boundaries"][:idx])
            if b.get("status") not in ("deprecated", "archived")
        ),
        None,
    )
    removed = delete_boundary(data, unit_id)
    save_doc_boundaries(doc_path, data)
    dangling = _dangling_tags(doc_id, unit_id)
    git = git_commit_boundaries(
        doc_path, f"fix: 경계 지우기 — {removed.get('title') or unit_id[:8]} (D-092)"
    )
    return {
        "deleted": unit_id,
        "merged_into": prev_id,
        "dangling_tags": dangling,
        "git": git,
    }


# =========================================
#   단위 안 손보기 (쪼개기·리셋)
# =========================================


@router.post("/api/documents/{doc_id}/composition/split")
async def api_split_unit(doc_id: str, body: SplitUnitRequest, bg: BackgroundTasks):
    """단위를 여러 조각으로 쪼갠다 (백그라운드 git commit).

    D-092: 쪼개기 = 원본 단위 **안에** 경계를 더 넣는 것. 원본 id는 첫 조각으로 그대로 남고,
    둘째 조각부터 새 경계(새 id)가 선다. 새 경계는 원본보다 한 단 깊은 «조각»이라 원본 기사가
    그것들을 품는다 — 기사 자체는 쪼개지지 않는다. 본문은 저장하지 않으므로 «조각 텍스트»는
    자리를 찾는 열쇠일 뿐이다 — 조각의 첫 글자들이 원본 본문에서 나오는 자리에 경계를 놓는다.
    """
    from core.boundaries import (
        git_commit_boundaries,
        insert_boundary,
        new_boundary,
        position_from_char,
        save_doc_boundaries,
    )
    from core.segmentation import collect_document_lines

    doc_path, err = _doc(doc_id)
    if err is not None:
        return err
    pieces = [str(piece).strip() for piece in (body.pieces or []) if str(piece).strip()]
    if len(pieces) < 2:
        return JSONResponse({"error": "쪼개기 조각은 2개 이상이어야 합니다."}, status_code=400)
    pid, data, orig_b = _find_home(doc_path, doc_id, body.original_unit_id, body.part_id)
    if orig_b is None:
        return JSONResponse(
            {"error": f"원본 단위를 찾을 수 없습니다: {body.original_unit_id}"}, status_code=404
        )
    original = next(
        (u for u in doc_units(doc_path, doc_id, pid) if u["id"] == body.original_unit_id), None
    )
    refs = [r for r in ((original or {}).get("source_refs") or []) if r and r.get("page")]
    if not refs:
        return JSONResponse({"error": "원본 단위에 출처(쪽)가 없습니다."}, status_code=400)
    _lines, page_texts = collect_document_lines(doc_path, pid, None)
    text = original.get("original_text") or ""

    def _to_page_abs(idx: int):
        """단위 본문 안의 오프셋 → (쪽, 쪽 텍스트 절대 오프셋). 본문은 쪽 조각을 개행으로 이었다."""
        consumed = 0
        for r in refs:
            cr = r.get("char_range")
            if not cr:
                continue
            seg = int(cr[1]) - int(cr[0])
            if idx <= consumed + seg:
                return int(r["page"]), int(cr[0]) + (idx - consumed)
            consumed += seg + 1
        return None

    created_ids: list[str] = []
    errors: list[str] = []
    cursor = 0
    for i, piece in enumerate(pieces[1:], start=2):
        key = piece[:6]
        at = text.find(key, cursor) if key else -1
        if at < 0:
            errors.append(f"조각 {i}: 첫 글자 «{key}»를 원본 본문에서 찾지 못했습니다")
            continue
        cursor = at + max(1, len(key))
        where = _to_page_abs(at)
        if where is None:
            errors.append(f"조각 {i}: 자리를 쪽으로 옮기지 못했습니다")
            continue
        page, abs_off = where
        # 조각은 원본보다 «한 단 안쪽»이다. 같은 층위로 넣으면 원본과 나란한 별도 기사가 되어
        # 기사가 쪼개져 버린다 — v1.3.0까지 그렇게 동작했다(사용자 지적).
        item = new_boundary(
            start=position_from_char(page_texts, page, abs_off),
            level=int(orig_b.get("level", 2)) + 1,
            role="fragment",
            title=piece[:20],
            kind="manual",
            status="draft",
            page_texts=page_texts or None,
            l4_commit=_document_head(doc_path),
        )
        try:
            insert_boundary(data, item)
            created_ids.append(item["id"])
        except Exception as e:  # noqa: BLE001
            errors.append(f"조각 {i}: {e}")
    if created_ids:
        save_doc_boundaries(doc_path, data)
    commit_msg = f"feat: 단위 쪼개기 — 경계 {len(created_ids)}개 삽입 (D-092)"
    bg.add_task(git_commit_boundaries, doc_path, commit_msg)
    if errors:
        return JSONResponse(
            {
                "created_count": len(created_ids),
                "created_ids": created_ids,
                "errors": errors,
                "git": "background",
            },
            status_code=207,
        )
    return {
        "created_count": len(created_ids),
        "created_ids": created_ids,
        "deprecated_id": None,  # 원본은 첫 조각으로 남는다(D-092: 앞 id 유지)
        "git": "background",
    }


@router.post("/api/documents/{doc_id}/composition/reset")
async def api_reset_composition(doc_id: str, body: ResetCompositionRequest, bg: BackgroundTasks):
    """여러 단위를 한꺼번에 deprecated 전환한다 (백그라운드 git commit).

    목적: 편성 리셋. 지우지 않고 deprecated로 두는 것은 관계·태그가 가리키던 id가
          갑자기 사라지지 않게 하기 위해서다 — 되살릴 수도 있다.
    """
    from core.boundaries import (
        git_commit_boundaries,
        load_doc_boundaries,
        save_doc_boundaries,
        update_boundary,
    )

    doc_path, err = _doc(doc_id)
    if err is not None:
        return err
    data = load_doc_boundaries(doc_path, doc_id, body.part_id)
    deprecated_count = 0
    errors = []
    for tb_id in body.unit_ids:
        try:
            update_boundary(data, tb_id, {"status": "deprecated"})
            deprecated_count += 1
        except Exception as e:  # noqa: BLE001 — 하나가 없어도 나머지는 처리한다
            errors.append(f"{tb_id[:8]}: {e}")
    if deprecated_count:
        save_doc_boundaries(doc_path, data)
    commit_msg = f"fix: 단위 편성 리셋 — {deprecated_count}개 deprecated"
    bg.add_task(git_commit_boundaries, doc_path, commit_msg)

    if errors:
        return JSONResponse(
            {
                "deprecated_count": deprecated_count,
                "errors": errors,
                "git": "background",
            },
            status_code=207,
        )

    return {
        "deprecated_count": deprecated_count,
        "git": "background",
    }
