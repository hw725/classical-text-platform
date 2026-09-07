"""LLM 4단 폴백 + OCR 엔진 연동 라우터.

server.py의 Phase 10-2 (LLM) / Phase 10-1 (OCR) 엔드포인트를 분리한 파일.

포함 라우트:
    GET  /api/llm/status
    GET  /api/llm/models
    GET  /api/llm/usage
    POST /api/llm/analyze-layout/{doc_id}/{page}
    POST /api/llm/compare-layout/{doc_id}/{page}
    POST /api/llm/drafts/{draft_id}/review
    POST /api/ocr/detect-layout/{doc_id}/{page}
    GET  /api/ocr/engines
    POST /api/documents/{doc_id}/parts/{part_id}/pages/{page_number}/ocr
    POST /api/documents/{doc_id}/parts/{part_id}/pages/{page_number}/ocr/stream
    GET  /api/documents/{doc_id}/parts/{part_id}/pages/{page_number}/ocr
    DELETE /api/documents/{doc_id}/parts/{part_id}/pages/{page_number}/ocr
    DELETE /api/documents/{doc_id}/parts/{part_id}/pages/{page_number}/ocr/{block_id}
    POST /api/documents/{doc_id}/parts/{part_id}/pages/{page_number}/ocr/{block_id}
    POST /api/documents/{doc_id}/parts/{part_id}/ocr/batch
"""

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app._state import (
    _get_llm_router,
    _get_ocr_pipeline,
    get_library_path,
    get_llm_drafts,
    require_repo_path,
)

router = APIRouter(tags=["llm_ocr"])
logger = logging.getLogger(__name__)


# ===========================================================================
#  Pydantic 요청 모델
# ===========================================================================


class DraftReviewRequest(BaseModel):
    """Draft 검토 요청 본문."""

    action: str  # "accept" | "modify" | "reject"
    quality_rating: int | None = None
    quality_notes: str | None = None
    modifications: str | None = None


class CompareLayoutRequest(BaseModel):
    """레이아웃 비교 요청 본문."""

    targets: list[str] | None = None


class OcrRunRequest(BaseModel):
    """OCR 실행 요청 본문."""

    engine_id: str | None = None  # None이면 기본 엔진
    block_ids: list[str] | None = None  # None이면 전체 블록
    force_provider: str | None = None  # LLM 프로바이더 지정 (llm_vision 엔진 전용)
    force_model: str | None = None  # LLM 모델 지정 (llm_vision 엔진 전용)
    # PaddleOCR 언어 코드 (paddleocr 엔진 전용: ch, chinese_cht, korean, japan, en)
    paddle_lang: str | None = None
    # 추론(thinking) 제어 (llm_vision 엔진 전용, D-083).
    #   None → 엔진 기본(사고 끔, D-074). True → 켬. "low"/"medium"/"high" → 강도 지정.
    #   예산은 답변 예산에 더해진다. 잘리면 엔진이 사고를 끄고 한 번 더 시도한다.
    llm_think: bool | str | None = None
    llm_thinking_budget: int | None = None


class OcrBatchRequest(BaseModel):
    """권(part) 단위 일괄 OCR 요청 본문."""

    engine_id: str | None = None  # None이면 기본 엔진
    pages: list[int] | None = None  # None이면 전체 쪽
    # 이미 L2 결과가 있는 쪽을 건너뛴다. 중단 후 이어서 돌리는 기본 동작이다.
    skip_existing: bool = True
    # 레이아웃을 OCR 이후에 다시 잡은 쪽은 건너뛰지 않고 다시 돌린다.
    #
    # 왜 기본값이 True인가: 논문 수십 쪽을 한 번에 돌린 뒤 결과가 나쁜 몇 쪽만
    # 레이아웃 탭에서 영역을 나누는 것이 실제 작업 흐름이다. 그런데
    # skip_existing이 그 쪽까지 건너뛰면 손으로 고친 작업이 반영되지 않는다.
    # 어느 쪽을 고쳤는지 사용자가 기억해 입력하게 만들지 않는다.
    redo_changed_layout: bool = True
    # 덮어쓰기 전에 기존 OCR 결과를 한 벌 남긴다.
    #
    # 왜 기본값이 True인가: L2는 Git으로 추적되지 않아 되돌릴 방법이 없다.
    # 모델을 바꿔 다시 돌려 보는 것이 추출 흐름의 일부인데(D-057), 결과가
    # 이전만 못해도 돌아갈 길이 없으면 «다시 돌려 보기»가 위험한 선택이 된다.
    # 쪽마다 파일 한 벌. 교정 저장도 같은 규칙을 따른다(documents.py).
    backup_before_overwrite: bool = True
    # 레이아웃이 없는 쪽에 페이지 전면 블록을 자동 생성한다.
    # 근현대 단일 컬럼 문헌용. 고서에서는 꺼야 한다.
    auto_full_page_block: bool = True
    writing_direction: str = "horizontal_ltr"
    # OCR 결과를 교정 텍스트(L4)에도 넣는다.
    #
    # 왜 기본값이 True인가: 교정 탭은 L4를 읽는다. 배치 OCR은 L2까지만 쓰므로
    # L4가 비고, 교정 탭이 **빈 화면**이 된다. 고서 흐름에서는 「OCR 채우기」
    # 단추를 쪽마다 눌러 이 복사를 하는데(ocr-panel.js), 추출 흐름에는 그
    # 단계가 없다. 결과를 원본과 대조하려면 교정 탭이 채워져 있어야 한다.
    #
    # 사람이 이미 고쳐 둔 L4는 덮지 않는다 — 아래 루프에서 그 쪽을 실제로
    # 새로 OCR 했을 때만 쓴다. 건너뛴 쪽은 손대지 않는다.
    fill_text_layer: bool = True
    # OCR이 끝나면 텍스트 레이어 PDF까지 만든다.
    #
    # 왜 기본값이 True인가: OCR 결과는 L2 JSON에만 들어가므로, 입히기를 따로
    # 실행하지 않으면 PDF는 여전히 스캔본이다. "OCR을 돌렸는데 왜 검색이
    # 안 되나"라는 기대 어긋남을 없앤다. 입히기는 LLM을 부르지 않고
    # 쪽당 1KB 미만이며 원본을 건드리지 않으므로 이어붙여도 안전하다.
    embed_after: bool = True
    force_provider: str | None = None
    force_model: str | None = None
    paddle_lang: str | None = None
    # 추론 제어 (llm_vision 전용, D-083). 일괄 OCR의 기본은 사고 끔이다 —
    # 사고는 D-082의 2단계(정밀 판독)에서만 켠다.
    llm_think: bool | str | None = None
    llm_thinking_budget: int | None = None
    # LLM 교정 패스 (D-082 1단계). "off" | "selected"(기계적 선별) | "all"(전량).
    #
    # 왜 기본이 off인가: 엔진 OCR은 무료·빠름인데 LLM 교정은 쪽당 수십 초에 비용이
    # 든다. 켜는 것은 사용자의 선택이어야 한다. "selected"는 신뢰도가 낮은 블록·
    # 협주·한글 미지원 엔진의 결과만 다시 본다. 결과는 L4 초안이고 자동 수용 기준을
    # 넘은 블록만 L4에 들어간다(나머지는 엔진 결과 그대로).
    llm_correction: str = "off"
    # "fast"(사고 끔) | "precise"(사고 켬·문맥 확대). 일괄에서는 fast가 기본이다.
    llm_correction_mode: str = "fast"


def _usage_log_path():
    """LLM 사용 기록 파일 경로를 돌려준다. (UsageTracker와 같은 규칙)"""
    from pathlib import Path

    library_path = get_library_path()
    if library_path is not None:
        return Path(library_path) / "llm_usage_log.jsonl"
    return Path.home() / ".classical-text-browser" / "llm_usage_log.jsonl"


def _usage_snapshot() -> int:
    """지금까지 쌓인 사용 기록의 줄 수를 센다.

    배치 전후로 이 값을 비교해 **이번 실행에서 쓴 것만** 집계하기 위함이다.
    파일이 없으면 0.
    """
    path = _usage_log_path()
    if not path.exists():
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _usage_since(start_line: int) -> dict:
    """start_line 이후에 기록된 LLM 사용량을 집계한다.

    입력: start_line — 배치 시작 시점의 기록 줄 수.
    출력: {calls, tokens_in, tokens_out, cost_usd, models}

    왜 필요한가:
        어느 모델이 얼마를 썼는지 **사용자가 그 자리에서 알아야 한다.**
        실제로 폴백 순서만 보고 «무료 로컬로 돌 것»이라 여겼는데
        기록에는 유료 API가 찍히고 있던 일이 있었다. 모르는 채로
        API가 소모되는 상황을 없애려고 완료 응답에 실어 보낸다.
    """
    import json as _json

    path = _usage_log_path()
    result = {
        "calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
        "models": [],
    }
    seen: list[str] = []
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                for idx, line in enumerate(f):
                    if idx < start_line:
                        continue
                    try:
                        rec = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    if rec.get("purpose") != "ocr":
                        continue
                    result["calls"] += 1
                    result["tokens_in"] += rec.get("tokens_in") or 0
                    result["tokens_out"] += rec.get("tokens_out") or 0
                    result["cost_usd"] += rec.get("cost_usd") or 0.0
                    label = f"{rec.get('provider') or '?'}/{rec.get('model') or '?'}"
                    if label not in seen:
                        seen.append(label)
        except OSError:
            # 읽지 못해도 아래에서 billing·note를 채운 형태로 돌려준다.
            # 조기 반환하면 호출부가 기대하는 키가 빠져 화면이 깨진다.
            pass

    result["models"] = seen
    result["cost_usd"] = round(result["cost_usd"], 6)
    result["billing"] = _billing_kind(seen)
    result["note"] = _billing_note(result["billing"], result)
    return result


def _billing_kind(model_labels: list[str]) -> str:
    """쓴 프로바이더·모델로 과금 방식을 판정한다.

    입력: ["gemini/gemini-2.5-flash", "ollama/qwen3.5:cloud", ...]
    출력: "metered" | "subscription" | "free" | "mixed" | "unknown"

    왜 이 판정이 필요한가: 구독형(Ollama 클라우드·OpenAI OAuth)은 금액이
    0으로 기록된다. 그대로 «$0.00»이라고 띄우면 **공짜로 오해**하지만
    실제로는 계정 한도를 쓰고 있다. 표시 문구를 다르게 하려고 가른다.
    """
    if not model_labels:
        return "unknown"

    kinds = set()
    for label in model_labels:
        provider, _, model = label.partition("/")
        if provider == "ollama":
            kinds.add("subscription" if "cloud" in model else "free")
        elif provider == "openai_oauth":
            kinds.add("subscription")
        elif provider in ("gemini", "openai", "anthropic"):
            kinds.add("metered")
        else:
            kinds.add("unknown")

    if len(kinds) == 1:
        return kinds.pop()
    return "mixed"


def _billing_note(kind: str, usage: dict) -> str:
    """사용자에게 보여 줄 한 줄 안내를 만든다.

    금액이 0이라고 «무료»라고 말하지 않는다 — 구독 한도는 눈에 보이지 않게
    소모되기 때문이다. Ollama·OpenAI는 남은 한도를 API로 알려 주지 않으므로
    (실측 2026-07-25: 응답 헤더에 rate limit 정보 없음) 대시보드로 안내한다.
    """
    calls = usage.get("calls", 0)
    if kind == "metered":
        return f"종량 과금 — 이번 실행 ${usage.get('cost_usd', 0):.4f}"
    if kind == "subscription":
        return (
            f"구독 한도를 사용했습니다 (호출 {calls}회). 금액은 청구되지 않지만 "
            "한도가 소모됩니다 — 남은 한도는 제공자 대시보드에서 확인하세요."
        )
    if kind == "free":
        return f"로컬 모델로 처리했습니다 (호출 {calls}회). 비용·한도 소모 없음."
    if kind == "mixed":
        return (
            f"여러 프로바이더가 쓰였습니다 (호출 {calls}회, "
            f"종량 과금분 ${usage.get('cost_usd', 0):.4f}). 구독 한도도 함께 소모됐습니다."
        )
    return ""


def _resolve_page_count(doc_path, part: dict) -> int:
    """이 권의 쪽 수를 구한다. manifest에 없으면 PDF를 열어 센다.

    입력: doc_path — 문헌 디렉토리. part — manifest의 parts 항목.
    출력: 쪽 수. 알 수 없으면 0.

    왜 manifest만 믿으면 안 되는가:
        `add_document()`로 만든 문헌(CLI·URL 등록)은 `page_count`가 null이다.
        사이드바는 PDF를 직접 열어 쪽 수를 알아내므로 화면에는 쪽이 보이는데,
        서버가 manifest만 보면 «쪽이 0개»라고 판단해 배치 OCR이
        «OCR 할 쪽이 없습니다»로 죽는다. 실제로 그 사고가 있었다 —
        화면에는 쪽이 멀쩡히 있는데 OCR만 안 되는 상태였다.
    """
    declared = part.get("page_count")
    if isinstance(declared, int) and declared > 0:
        return declared

    # manifest에 없으면 실제 파일에서 센다.
    try:
        import fitz

        from core.document import get_pdf_path

        with fitz.open(str(get_pdf_path(doc_path, part.get("part_id")))) as pdf:
            return pdf.page_count
    except Exception:  # noqa: BLE001 — 셀 수 없으면 0으로 두고 호출부가 안내한다
        return 0


# 학습 데이터에 한글이 없어 한글을 인식하지 못하는 엔진들.
# 근거는 각 엔진 파일의 docstring이다 (ndlocr_engine.py, ndlkotenocr_engine.py,
# ndlkotenocr_full_engine.py). 추측이 아니라 문서화된 제약이다.
HANGUL_INCAPABLE_ENGINES = ("ndlocr", "ndlkotenocr", "ndlkotenocr-full")


def _add_llm_reasoning_kwargs(engine_kwargs: dict, body) -> dict:
    """요청 본문의 추론 옵션을 엔진 kwargs로 옮긴다 (D-083).

    입력: engine_kwargs — 엔진 recognize()에 넘길 dict (제자리 수정).
          body — llm_think / llm_thinking_budget 필드를 가질 수 있는 요청 모델.
    출력: 같은 dict.

    네 라우트(쪽 OCR·스트림·블록 재실행·권 일괄)가 같은 옵션을 받으므로 한 곳에
    둔다. 지정하지 않으면 아무것도 넣지 않는다 — 엔진 기본(사고 끔)이 그대로다.
    """
    think = getattr(body, "llm_think", None)
    if think is not None:
        engine_kwargs["think"] = think
    budget = getattr(body, "llm_thinking_budget", None)
    if budget:
        engine_kwargs["thinking_budget"] = int(budget)
    return engine_kwargs


# ===========================================================================
#  헬퍼 함수
# ===========================================================================


def _load_page_image(doc_id: str, page: int, part_id: str | None = None) -> bytes | None:
    """페이지 이미지를 바이트로 로드한다 (LLM 전송용 리사이즈 포함).

    L1_source에서 PDF를 찾아 해당 페이지를 이미지로 변환.
    또는 이미 이미지 파일이면 직접 읽는다.
    LLM 비전 모델에 보내기 위해 최대 2000px, JPEG 압축을 적용한다.

    왜 part_id가 필요한가: 다권본에서 이것이 없으면 **첫 권의 같은 쪽**을
    읽어 LLM에 넘긴다. 오류가 나지 않고 그럴듯한 결과가 나오므로 가장
    발견하기 어려운 종류의 오답이 된다.

    왜 리사이즈하는가:
        PDF에서 144 DPI로 추출하면 10MB+ PNG가 된다.
        base64 인코딩 시 14MB+ → Ollama 클라우드 프록시가 타임아웃/거부.
        LLM 비전 모델은 내부적으로 리사이즈하므로 2000px이면 충분하다.
    """
    from ocr.image_utils import resize_for_llm, resolve_part_pdf

    library_path = get_library_path()
    if library_path is None:
        return None

    doc_dir = library_path / "documents" / doc_id

    # 1. L1_source에서 이미지 파일 직접 찾기 (JPEG)
    source_dir = doc_dir / "L1_source"
    if source_dir.exists():
        # 페이지 번호에 해당하는 이미지 찾기
        for pattern in [
            f"*_p{page:03d}.*",
            f"*_p{page:04d}.*",
            f"*_{page:03d}.*",
            f"*_{page:04d}.*",
            f"page_{page}.*",
            f"p{page}.*",
        ]:
            matches = list(source_dir.glob(pattern))
            for m in matches:
                if m.suffix.lower() in (".jpg", ".jpeg", ".png", ".tiff", ".tif"):
                    raw = m.read_bytes()
                    return resize_for_llm(raw, max_long_side=2000)

    # 2. PDF에서 페이지 추출 (pymupdf/fitz 사용)
    pdf_path = resolve_part_pdf(doc_dir, part_id)
    if pdf_path is not None and pdf_path.exists():
        try:
            import fitz  # pymupdf
        except ImportError:
            return None  # pymupdf가 없으면 건너뜀

        # with를 쓰는 이유: 렌더 도중 예외가 나도 파일 핸들이 닫힌다.
        # Windows에서 핸들이 남으면 그 PDF가 잠겨 문헌 삭제·이동이 부분 실패한다.
        try:
            with fitz.open(str(pdf_path)) as doc:
                # page는 1-indexed (API 경로), fitz는 0-indexed
                page_idx = page - 1
                if 0 <= page_idx < len(doc):
                    # scale=2.0 → 144 DPI (기본 72 DPI × 2)
                    pix = doc[page_idx].get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
                    raw = pix.tobytes("png")
                    return resize_for_llm(raw, max_long_side=2000)
        except Exception:
            return None

    return None


# ===========================================================================
#  Phase 10-2: LLM 4단 폴백 아키텍처 API
# ===========================================================================


@router.get("/api/llm/status")
async def api_llm_status():
    """각 provider의 가용 상태."""
    router_inst = _get_llm_router()
    return await router_inst.get_status()


@router.get("/api/llm/accounts")
async def api_llm_accounts():
    """프로바이더별 «쓸 수 있는 상태인가»와 «아니면 무엇을 해야 하는가».

    출력: {"providers": [{
        "provider_id": "ollama",
        "display_name": "Ollama",
        "billing_model": "free",      # metered | subscription | free
        "reachable": true,            # 서비스에 닿는가
        "authenticated": true,        # 로그인·키가 있는가 (null이면 확인 불가)
        "account": "user@example.com",# 알 수 있을 때만
        "plan": "pro",
        "setup_kind": "cli_signin",   # env_key | cli_signin | local
        "setup_steps": [...],
        "status": "ready" | "needs_signin" | "needs_key" | "offline",
        "note": "사람이 읽을 한 줄"
    }, ...]}

    왜 /api/llm/status와 따로 두는가:
        status는 «가용한가»만 준다. 그런데 구독형은 **서비스가 떠 있는 것과
        로그인된 것이 다르다.** Ollama 서버는 로그인 없이도 뜨므로 status는
        available=True를 주지만, 클라우드 모델을 부르면 실패하고 라우터가
        조용히 유료 API로 넘어간다(D-056에서 실제로 겪은 사고다).

        배포판에서는 이 구분이 특히 중요하다. API 키는 «.env에 넣으세요»로
        끝나지만 구독형은 터미널 로그인이 필요하고, 그것을 앱이 대신할 수 없다.
        무엇을 해야 하는지 화면에 적어 주는 것이 이 라우트의 목적이다.
    """
    import asyncio

    router_inst = _get_llm_router()

    async def _one(provider):
        entry = {
            "provider_id": provider.provider_id,
            "display_name": provider.display_name,
            "billing_model": provider.billing_model,
            "setup_kind": getattr(provider, "setup_kind", "env_key"),
            "setup_steps": list(getattr(provider, "setup_steps", ())),
            "account": None,
            "plan": None,
        }

        try:
            reachable = await provider.is_available()
        except Exception as e:  # noqa: BLE001 — 한 프로바이더 실패로 화면이 비면 안 된다
            reachable = False
            entry["error"] = str(e)
        entry["reachable"] = reachable

        # 로그인 여부는 조회할 방법이 있는 프로바이더만 확인한다.
        # 없으면 None으로 둔다 — 모르는 것을 안다고 하지 않는다.
        account = None
        if reachable:
            try:
                account = await provider.account_info()
            except Exception:  # noqa: BLE001
                account = None
        # 실제로 쓸 비전 모델을 알 수 있으면 그 모델의 과금 방식을 쓴다.
        #
        # 왜 클래스 값을 그대로 쓰면 안 되는가: Ollama의 billing_model은
        # "free"(로컬)지만, 이 PC에서 실제로 고르는 비전 모델은
        # `qwen3.5:397b-cloud`처럼 **구독 한도를 쓰는 클라우드 모델**일 수 있다.
        # 화면에 «로컬 무료»라고 띄우면 D-056에서 문제 삼은 그 오해가
        # 그대로 재발한다.
        if reachable and hasattr(provider, "_pick_vision_model"):
            # 모델 고르기는 «살아 있나» 호출(클라우드 왕복)을 포함해 2~10초가 걸릴 수 있다
            # (2026-09-05 실측). 설정 화면이 그것을 기다리게 하지 않는다 — 1.5초 안에 못 고르면
            # 뒤에서 마저 고르게 두고(캐시 10분) 이번에는 «확인 중»으로 보낸다.
            try:
                model = await asyncio.wait_for(provider._pick_vision_model(), timeout=1.5)
            except asyncio.TimeoutError:
                model = None
                entry["active_model_pending"] = True
                entry["billing_model"] = "unknown"  # 모델이 정해지기 전에는 과금도 모른다
                _schedule_warm(provider)
            except Exception:  # noqa: BLE001
                model = None
            if model:
                entry["active_model"] = model
                entry["billing_model"] = provider.billing_for_model(model)
                # 고른 모델이 실제로 깔려 있는가 — _pick_vision_model은 아무것도 없을 때
                # 기본 이름을 그대로 돌려준다. 그것을 «로컬 모델로 돕니다»라고 하면 거짓이다.
                try:
                    installed = {
                        m.get("name")
                        for m in await asyncio.wait_for(provider.list_models(), timeout=2.0)
                    }
                    entry["active_model_installed"] = model in installed
                    # 고르기가 «전부 응답 없음»으로 끝나 기본 이름만 돌려준 경우 — 깔려 있어도 못 쓴다
                    dead = (
                        provider._shared_get("vision_dead")
                        if hasattr(provider, "_shared_get")
                        else None
                    )
                    entry["active_model_dead"] = bool(dead) and dead == model
                    if model not in installed and provider.provider_id == "ollama":
                        # 비전 모델이 하나도 없으면 기본 모델을 저절로 받기 시작한다 — 사람이
                        # 단추를 찾게 하지 않는다(2026-09-06 지시). 프로세스당 한 번만 시도.
                        _auto_pull_default(provider, model)
                except Exception:  # noqa: BLE001
                    entry["active_model_installed"] = None

        if account:
            entry["account"] = account.get("account")
            entry["plan"] = account.get("plan")
            entry["authenticated"] = True
        elif entry["setup_kind"] == "cli_signin":
            # 조회 수단이 있는데 못 받았으면 «로그인 안 됨», 조회 수단 자체가
            # 없으면 «모름». Ollama는 /api/me가 있고 OAuth 프록시는 없다.
            entry["authenticated"] = False if provider.provider_id == "ollama" else None
        else:
            # API 키 방식은 is_available()이 곧 키 유무다.
            entry["authenticated"] = reachable

        entry["status"], entry["note"] = _account_status(entry)
        return entry

    # 프로바이더를 **동시에** 확인한다. 직렬로 하면 느린 하나(프록시 포트
    # 스캔 등)가 나머지 전부를 붙잡아 설정 화면이 그만큼 멈춘다.
    # 순서는 폴백 순서 그대로 유지한다 — 화면의 «위에서부터 시도합니다»와
    # 어긋나면 안 된다.
    return {"providers": list(await asyncio.gather(*(_one(p) for p in router_inst.providers)))}


# 프로바이더 주소별로 «뒤에서 고르는 중»인 태스크 하나만 — 화면이 3초마다 다시 물을 때마다
# /api/show 전부와 클라우드 generate가 겹쳐 나가면 안 된다(리뷰 실측: 동시 4회). 참조도 보관한다.
_warm_tasks: dict[str, object] = {}  # {주소: asyncio.Task}


def _schedule_warm(provider) -> None:
    import asyncio

    key = getattr(provider, "_url", provider.provider_id)
    task = _warm_tasks.get(key)
    if task is not None and not task.done():
        return
    task = asyncio.create_task(_warm_vision_model(provider))
    _warm_tasks[key] = task
    task.add_done_callback(lambda t: _warm_tasks.pop(key, None))


_auto_pull_started: set[str] = set()


def _auto_pull_default(provider, model: str) -> None:
    """Ollama에 비전 모델이 없을 때 기본 모델을 뒤에서 받기 시작한다(주소당 한 번)."""
    key = f"{getattr(provider, '_url', '')}:{model}"
    if key in _auto_pull_started:
        return
    _auto_pull_started.add(key)
    try:
        from urllib.parse import urlparse

        from core.ollama_signin import pull

        host = urlparse(provider._url).netloc or None

        def _after(ok: bool):
            from llm.providers.ollama import OllamaProvider

            OllamaProvider._SHARED.clear()

        result = pull(model, host, _after)
        if result.get("error"):
            _auto_pull_started.discard(key)  # 다음 확인 때 다시 시도
    except Exception:  # noqa: BLE001 — 자동 받기 실패는 카드의 «모델 없음»으로 드러난다
        _auto_pull_started.discard(key)


async def _warm_vision_model(provider) -> None:
    """설정 화면이 기다리지 않고 넘긴 «비전 모델 고르기»를 뒤에서 마친다.

    결과는 공유 캐시에 남는다.
    """
    try:
        await provider._pick_vision_model()
    except Exception:  # noqa: BLE001 — 뒤에서 도는 일이라 실패해도 알릴 데가 없다
        pass


def _account_status(entry: dict) -> tuple[str, str]:
    """프로바이더 상태를 한 단어와 한 줄로 요약한다.

    입력: api_llm_accounts()가 만든 항목 dict.
    출력: (status, note)

    구독형에서 «로그인 안 됨»을 «사용 가능»으로 보이게 하면 안 된다.
    실행하고 나서야 안 되는 것을 알게 되기 때문이다.
    """
    name = entry["display_name"]
    kind = entry["setup_kind"]

    if not entry["reachable"]:
        if kind == "env_key":
            return "needs_key", f"{name}: API 키가 없습니다. .env에 키를 넣으세요."
        if kind == "cli_signin":
            return "offline", f"{name}: 서비스가 실행 중이 아닙니다."
        return "offline", f"{name}: 연결할 수 없습니다."

    if entry.get("active_model_pending"):
        return (
            "checking",
            f"{name}: 서버가 떠 있습니다. 어느 모델로 돌지 확인하는 중입니다 — "
            "잠시 뒤 다시 봅니다.",
        )

    model = entry.get("active_model")
    if model and entry.get("active_model_dead"):
        return (
            "no_model",
            f"{name}: 비전 모델 {model}이(가) **응답하지 않습니다** — 은퇴했거나 로그인이 필요합니다. "
            "「로그인」을 확인하거나 「모델 받기」에서 다른 모델을 고르세요. 그때까지 이미지 작업은 "
            "다음 프로바이더로 넘어갑니다.",
        )
    if model and entry.get("active_model_installed") is False:
        if "cloud" in model:
            # 클라우드 모델은 내려받는 파일이 없다(등록만). 대신 로그인이 있어야 돈다(D-114).
            how = (
                "클라우드 모델이라 내려받는 파일은 없고 몇 초면 등록됩니다. 쓰려면 「로그인」이 "
                "필요합니다 — 로그인하지 않을 PC는 아래 「모델 받기」에서 내 PC용 모델을 고르세요."
            )
        else:
            how = "수 GB라 몇 분 걸립니다 — 아래 진행을 보세요."
        return (
            "no_model",
            f"{name}: 서버는 떠 있지만 **비전 모델이 없습니다.** 기본 모델 {model}을(를) "
            f"받기 시작했습니다. {how} 끝날 때까지 이미지 작업은 다음 프로바이더로 넘어갑니다.",
        )

    if entry["authenticated"] is False:
        # Ollama는 로그인 없이도 **로컬 모델로는 돈다.** 실제로 고른 비전 모델이 로컬이면
        # «사용 가능»이다 — 로그인 필요라고 하면 로컬만 쓰는 PC에서 «Ollama가 떠 있는데
        # 안 된다»로 보인다(2026-09-05 보고). 클라우드 모델을 고른 경우에만 로그인이 문제다.
        model = entry.get("active_model")
        if model and "cloud" not in model:
            return (
                "ready",
                f"{name}: 로컬 모델 {model}(으)로 돕니다. "
                "클라우드 모델까지 쓰려면 ollama.com 로그인이 필요합니다(`ollama signin`).",
            )
        return (
            "needs_signin",
            f"{name}: 서버는 떠 있지만 **로그인돼 있지 않습니다.** "
            "로컬 비전 모델이 없어 클라우드 모델을 골랐는데 로그인 없이는 실패하고, "
            "그러면 다음 프로바이더(유료 API)로 넘어갑니다. "
            "「로그인」을 누르거나, 로그인하지 않을 PC는 「모델 받기」에서 내 PC용(로컬) 모델을 골라 받으세요.",
        )

    if entry["account"]:
        plan = f", {entry['plan']} 요금제" if entry.get("plan") else ""
        note = f"{name}: {entry['account']}{plan}로 로그인돼 있습니다."
        # 로그인돼 있다는 사실만 적고 끝내면, 실제로 도는 모델이 구독 한도를
        # 쓰는 클라우드 모델일 때 그 사실이 가려진다.
        if entry["billing_model"] == "subscription":
            model = entry.get("active_model")
            which = f" {model}은(는)" if model else ""
            note += f"{which} **구독 한도를 씁니다** — 남은 한도는 제공자 대시보드에서 확인하세요."
        return "ready", note

    if entry["billing_model"] == "metered":
        return "ready", f"{name}: 키가 등록돼 있습니다. **쓴 만큼 청구됩니다.**"
    if entry["billing_model"] == "subscription":
        return (
            "ready",
            f"{name}: 사용 가능합니다. 금액은 0이지만 **구독 한도를 씁니다** — "
            "남은 한도는 제공자 대시보드에서 확인하세요.",
        )
    return "ready", f"{name}: 사용 가능합니다."


@router.get("/api/llm/models")
async def api_llm_models():
    """GUI 드롭다운용 모델 목록."""
    router_inst = _get_llm_router()
    return await router_inst.get_available_models()


@router.get("/api/llm/usage")
async def api_llm_usage():
    """이번 달 사용량 요약."""
    router_inst = _get_llm_router()
    return router_inst.usage_tracker.get_monthly_summary()


@router.post("/api/llm/analyze-layout/{doc_id}/{page}")
async def api_analyze_layout(
    doc_id: str,
    page: int,
    part_id: str | None = Query(None, description="권 식별자. 다권본에서는 반드시 넘길 것"),
    force_provider: str | None = Query(None),
    force_model: str | None = Query(None),
):
    """페이지 이미지를 LLM으로 레이아웃 분석. Draft 반환.

    왜 별도 엔드포인트인가:
        기존 layout-editor의 수동 블록 편집과 독립적으로,
        LLM이 제안하는 블록을 Draft로 관리한다.
    """
    library_path = get_library_path()
    if library_path is None:
        return JSONResponse({"error": "서고가 설정되지 않았습니다."}, status_code=500)

    from core.layout_analyzer import analyze_page_layout

    router_inst = _get_llm_router()

    # 페이지 이미지 로드
    page_image = _load_page_image(doc_id, page, part_id)
    if not page_image:
        return JSONResponse(
            {"error": f"페이지 이미지 없음: {doc_id} page {page}"},
            status_code=404,
        )

    try:
        draft = await analyze_page_layout(
            router_inst,
            page_image,
            force_provider=force_provider,
            force_model=force_model,
        )
    except Exception as e:
        return JSONResponse({"error": f"레이아웃 분석 실패: {e}"}, status_code=500)

    # Draft 저장
    drafts = get_llm_drafts()
    drafts[draft.draft_id] = draft
    return draft.to_dict()


@router.post("/api/llm/compare-layout/{doc_id}/{page}")
async def api_compare_layout(
    doc_id: str,
    page: int,
    body: CompareLayoutRequest,
    part_id: str | None = Query(None, description="권 식별자. 다권본에서는 반드시 넘길 것"),
):
    """여러 모델로 레이아웃 분석 비교."""
    library_path = get_library_path()
    if library_path is None:
        return JSONResponse({"error": "서고가 설정되지 않았습니다."}, status_code=500)

    from core.layout_analyzer import compare_layout_analysis

    router_inst = _get_llm_router()

    page_image = _load_page_image(doc_id, page, part_id)
    if not page_image:
        return JSONResponse(
            {"error": f"페이지 이미지 없음: {doc_id} page {page}"},
            status_code=404,
        )

    # targets 파싱: ["ollama", "gemini:gemini-2.5-flash"]
    parsed_targets = None
    if body.targets:
        parsed_targets = []
        for t in body.targets:
            if ":" in t:
                parts = t.split(":", 1)
                parsed_targets.append((parts[0], parts[1]))
            else:
                parsed_targets.append(t)

    try:
        draft_list = await compare_layout_analysis(
            router_inst,
            page_image,
            targets=parsed_targets,
        )
    except Exception as e:
        return JSONResponse({"error": f"레이아웃 비교 실패: {e}"}, status_code=500)

    # Draft들 저장
    drafts = get_llm_drafts()
    for d in draft_list:
        drafts[d.draft_id] = d

    return [d.to_dict() for d in draft_list]


@router.post("/api/llm/drafts/{draft_id}/review")
async def api_review_draft(draft_id: str, body: DraftReviewRequest):
    """Draft를 검토 (accept/modify/reject)."""
    drafts = get_llm_drafts()
    draft = drafts.get(draft_id)
    if not draft:
        return JSONResponse({"error": f"Draft 없음: {draft_id}"}, status_code=404)

    if body.action == "accept":
        draft.accept(
            quality_rating=body.quality_rating,
            notes=body.quality_notes or "",
        )
    elif body.action == "modify":
        draft.modify(
            modifications=body.modifications or "",
            quality_rating=body.quality_rating,
        )
    elif body.action == "reject":
        draft.reject(reason=body.quality_notes or "")
    else:
        return JSONResponse(
            {"error": f"알 수 없는 action: {body.action}"},
            status_code=400,
        )

    return draft.to_dict()


# ===========================================================================
#  Phase 10-1: OCR 엔진 연동 API
# ===========================================================================


@router.post("/api/ocr/detect-layout/{doc_id}/{page}")
async def api_detect_layout(
    doc_id: str,
    page: int,
    part_id: str = Query(..., description="파트 ID"),
    engine_id: str = Query(
        None,
        description="레이아웃 감지 엔진 ID (ndlocr 또는 ndlkotenocr). "
        "None이면 레이아웃 감지를 지원하는 첫 번째 엔진 사용.",
    ),
    conf_threshold: float = Query(0.3, description="감지 신뢰도 임계값 (0.0~1.0)"),
):
    """서버사이드 레이아웃 감지 (엔진 선택 가능).

    왜 필요한가:
        KotenLayout(브라우저 ONNX)은 5클래스(본문/삽화/인장)만 탐지한다.
        서버 엔진은 16~17클래스(본문/주석/두주/판심제/장차/도판 등)를 탐지하여
        고전적 레이아웃을 더 세밀하게 분석할 수 있다.

    지원 엔진:
        - ndlkotenocr: RTMDet 16클래스 (고전적 전용)
        - ndlocr: DEIM 17클래스 (근현대 범용)

    입력:
        doc_id: 문서 ID
        page: 페이지 번호 (1-indexed)
        part_id: 파트 ID (이미지 탐색에 사용)
        engine_id: 레이아웃 감지 엔진 ID (None이면 자동 선택)
        conf_threshold: 감지 신뢰도 임계값

    출력:
        { "blocks": [...], "image_width": int, "image_height": int,
          "analysis_method": "auto_detect", "engine": "<engine_id>" }
    """
    library_path = get_library_path()
    if library_path is None:
        return JSONResponse({"error": "서고가 설정되지 않았습니다."}, status_code=500)

    # 1. 레이아웃 감지 엔진 가져오기
    _pipeline, registry = _get_ocr_pipeline()

    if engine_id is not None:
        # 명시적으로 지정된 엔진 사용
        try:
            engine = registry.get_engine(engine_id)
        except Exception as e:
            return JSONResponse(
                {"error": f"'{engine_id}' 엔진을 사용할 수 없습니다: {e}"},
                status_code=400,
            )
        if not getattr(engine, "supports_layout_detection", False):
            return JSONResponse(
                {"error": f"'{engine_id}' 엔진은 레이아웃 감지를 지원하지 않습니다."},
                status_code=400,
            )
    else:
        # engine_id 미지정 → 레이아웃 감지를 지원하는 첫 번째 사용 가능 엔진 자동 선택
        engine = None
        for info in registry.list_engines():
            if info.get("supports_layout_detection") and info.get("available"):
                try:
                    engine = registry.get_engine(info["engine_id"])
                    break
                except Exception:
                    continue
        if engine is None:
            return JSONResponse(
                {"error": "레이아웃 감지를 지원하는 사용 가능한 엔진이 없습니다."},
                status_code=400,
            )

    # 2. 페이지 이미지 로드 (원본 해상도)
    import io as _io

    from ocr.image_utils import get_page_image_path, load_page_image, load_page_image_from_pdf

    image_path = get_page_image_path(str(library_path), doc_id, part_id, page)
    if image_path is not None:
        pil_image = load_page_image(image_path)
    else:
        pil_image = load_page_image_from_pdf(str(library_path), doc_id, page)

    if pil_image is None:
        return JSONResponse(
            {"error": f"페이지 이미지를 찾을 수 없습니다: {doc_id} page {page}"},
            status_code=404,
        )

    # PIL Image → PNG bytes
    buf = _io.BytesIO()
    pil_image.convert("RGB").save(buf, format="PNG")
    image_bytes = buf.getvalue()

    img_w, img_h = pil_image.size

    # 3. 레이아웃 감지
    try:
        blocks = engine.detect_layout(
            image_bytes,
            page_number=page,
            conf_threshold=conf_threshold,
        )
    except Exception as e:
        return JSONResponse(
            {"error": f"레이아웃 감지 실패: {e}"},
            status_code=500,
        )

    return {
        "blocks": blocks,
        "image_width": img_w,
        "image_height": img_h,
        "analysis_method": "auto_detect",
        "engine": engine.engine_id,
        "block_count": len(blocks),
    }


@router.get("/api/ocr/engines")
async def api_ocr_engines():
    """등록된 OCR 엔진 목록과 사용 가능 여부를 반환한다.

    목적: GUI의 OCR 실행 패널에서 엔진 드롭다운을 채우기 위해 사용한다.
    출력: {
        "engines": [
            {"engine_id": "paddleocr", "display_name": "PaddleOCR", "available": true, ...}
        ],
        "default_engine": "paddleocr"
    }
    """
    library_path = get_library_path()
    if library_path is None:
        return JSONResponse({"error": "서고가 설정되지 않았습니다."}, status_code=500)

    try:
        _pipeline, registry = _get_ocr_pipeline()
        engines = registry.list_engines()
    except Exception as e:  # noqa: BLE001
        # 엔진 등록·LLM 라우터 초기화(.env 읽기 등)에서 난 예외. 예전에는 그대로
        # 500 HTML로 새어 나가 화면이 «서고를 선택하면…»으로 잘못 안내했다.
        # 원인을 본문에 적어 드롭다운과 토스트에 보이게 하고, traceback은 서버 로그로.
        logger.exception("OCR 엔진 목록 초기화 실패")
        return JSONResponse(
            {
                "error": (
                    f"OCR 엔진 목록을 만들지 못했습니다: {type(e).__name__}: {e} "
                    "— 서버 콘솔의 오류 내용을 확인하세요."
                ),
                "error_type": type(e).__name__,
            },
            status_code=500,
        )
    return {
        "engines": engines,
        "default_engine": registry.default_engine_id,
    }


class CorrectionRunRequest(BaseModel):
    """LLM 교정 패스 실행 요청 (D-082)."""

    # 다시 볼 블록. None이면 기계적 선별(select_all이면 전량).
    block_ids: list[str] | None = None
    select_all: bool = False
    # "fast"(1단계, 사고 끔) | "precise"(2단계, 사고 켬·앞뒤 문맥 확대 — 행초용)
    mode: str = "fast"
    confidence_threshold: float | None = None
    force_provider: str | None = None
    force_model: str | None = None
    llm_thinking_budget: int | None = None


class CorrectionApplyRequest(BaseModel):
    """교정 초안 적용 요청. block_ids가 None이면 자동 수용된 블록만."""

    block_ids: list[str] | None = None


def _load_l2_page(doc_path: Path, part_id: str, page_number: int) -> dict | None:
    l2_path = doc_path / "L2_ocr" / f"{part_id}_page_{page_number:03d}.json"
    if not l2_path.exists():
        return None
    return json.loads(l2_path.read_text(encoding="utf-8"))


def _document_language(doc_path: Path) -> str | None:
    try:
        from core.document import get_bibliography

        return (get_bibliography(doc_path) or {}).get("language")
    except Exception:  # noqa: BLE001 — 서지가 없어도 선별은 돌아가야 한다
        return None


def _correction_dicts(doc_path: Path):
    """교정 패스의 정렬 사전과 프롬프트 자형 주의 목록 (D-080·D-081).

    기본 사전(strict) + 이 문헌의 승인 쌍. 승인 쌍은 프롬프트의 주의 목록으로도 쓴다.
    라우터 간 직접 import는 금지라(CLAUDE.md) alignment 라우터의 묶음 함수를 부르지
    않고 core에서 직접 조립한다.
    """
    from core.alignment import TieredVariantDicts, VariantCharDict, load_document_approvals

    approvals = load_document_approvals(doc_path)
    bundle = TieredVariantDicts([VariantCharDict(), approvals])
    pairs = []
    seen = set()
    for a, alts in approvals.to_dict().items():
        for b in alts:
            key = tuple(sorted((a, b)))
            if key not in seen:
                seen.add(key)
                pairs.append([a, b])
    return bundle, pairs


def _run_page_correction(
    doc_path: Path,
    doc_id: str,
    part_id: str,
    page_number: int,
    pipeline,
    registry,
    *,
    block_ids: list[str] | None = None,
    select_all: bool = False,
    mode: str = "fast",
    confidence_threshold: float | None = None,
    force_provider: str | None = None,
    force_model: str | None = None,
    thinking_budget: int | None = None,
) -> dict:
    """한 쪽의 교정 패스를 끝까지 돈다 — 선별 → LLM → 초안 저장. 동기 함수(executor용).

    출력: 초안 dict (candidates가 없으면 blocks가 빈 초안).
    """
    from core.document import get_page_layout, get_page_text
    from ocr.correction_pass import (
        DEFAULT_CONFIDENCE_THRESHOLD,
        llm_kwargs_for_mode,
        run_correction,
        select_candidates,
    )

    l2_page = _load_l2_page(doc_path, part_id, page_number)
    if l2_page is None:
        raise FileNotFoundError(f"{page_number}쪽에 L2 OCR 결과가 없습니다. 먼저 OCR을 실행하세요.")
    try:
        layout = get_page_layout(doc_path, part_id, page_number)
    except Exception:  # noqa: BLE001
        layout = None

    candidates = select_candidates(
        l2_page,
        layout,
        confidence_threshold=confidence_threshold or DEFAULT_CONFIDENCE_THRESHOLD,
        document_language=_document_language(doc_path),
        force_block_ids=block_ids,
        select_all=select_all,
    )
    if block_ids is not None:
        # 사람이 지정했으면 그 블록만 — 기계적 선별에 걸린 다른 블록은 이번에는 안 본다.
        wanted = set(block_ids)
        candidates = [c for c in candidates if c.block_id in wanted]

    engine = registry.get_engine("llm_vision")
    bundle, hint_pairs = _correction_dicts(doc_path)

    prev_text = next_text = None
    if mode == "precise":
        # 행초용 문맥: 앞뒤 쪽의 확정본 (없으면 None)
        for delta, setter in ((-1, "prev"), (1, "next")):
            try:
                info = get_page_text(doc_path, part_id, page_number + delta)
                text = info.get("text") or None
            except Exception:  # noqa: BLE001
                text = None
            if setter == "prev":
                prev_text = text
            else:
                next_text = text

    return run_correction(
        pipeline,
        engine,
        doc_path,
        doc_id,
        part_id,
        page_number,
        candidates,
        mode=mode,
        llm_kwargs=llm_kwargs_for_mode(
            mode,
            thinking_budget=thinking_budget,
            force_provider=force_provider,
            force_model=force_model,
        ),
        variant_dict=bundle,
        variant_hint_pairs=hint_pairs,
        prev_page_text=prev_text,
        next_page_text=next_text,
    )


@router.get("/api/documents/{doc_id}/parts/{part_id}/pages/{page_number}/ocr/correction-candidates")
async def api_correction_candidates(
    doc_id: str,
    part_id: str,
    page_number: int,
    select_all: bool = Query(False),
    confidence_threshold: float | None = Query(None),
):
    """LLM 교정 패스에 넘길 블록을 기계적으로 고른다 (D-082). LLM을 부르지 않는다.

    출력: {"candidates": [...], "engine": L2 엔진, "draft": 기존 초안 또는 null}
    """
    from core.document import get_page_layout
    from ocr.correction_pass import (
        DEFAULT_CONFIDENCE_THRESHOLD,
        load_draft,
        select_candidates,
    )

    doc_path = require_repo_path("documents", doc_id)
    l2_page = _load_l2_page(doc_path, part_id, page_number)
    if l2_page is None:
        return JSONResponse(
            {"error": f"{page_number}쪽에 L2 OCR 결과가 없습니다. 먼저 OCR을 실행하세요."},
            status_code=404,
        )
    try:
        layout = get_page_layout(doc_path, part_id, page_number)
    except Exception:  # noqa: BLE001
        layout = None
    candidates = select_candidates(
        l2_page,
        layout,
        confidence_threshold=confidence_threshold or DEFAULT_CONFIDENCE_THRESHOLD,
        document_language=_document_language(doc_path),
        select_all=select_all,
    )
    return {
        "engine": l2_page.get("ocr_engine"),
        "candidates": [c.to_dict() for c in candidates],
        "draft": load_draft(doc_path, part_id, page_number),
    }


@router.post("/api/documents/{doc_id}/parts/{part_id}/pages/{page_number}/ocr/correct")
async def api_run_correction(
    doc_id: str, part_id: str, page_number: int, body: CorrectionRunRequest
):
    """LLM 교정 패스를 실행하고 초안을 저장한다 (D-082 1·2단계). L2는 바뀌지 않는다.

    mode="fast"    — 앵커 있는 교정, 사고 끔.
    mode="precise" — 앞뒤 문맥 확대 + 사고 켬(예산 분리, D-083). 행초·흘림체용.
    출력: 초안 dict. 블록마다 anchor_text·corrected_text·agreement·accepted·pairs.
    """
    import asyncio

    if body.mode not in ("fast", "precise"):
        return JSONResponse({"error": "mode는 fast 또는 precise입니다."}, status_code=400)
    doc_path = require_repo_path("documents", doc_id)
    pipeline, registry = _get_ocr_pipeline()
    try:
        loop = asyncio.get_running_loop()
        draft = await loop.run_in_executor(
            None,
            lambda: _run_page_correction(
                doc_path,
                doc_id,
                part_id,
                page_number,
                pipeline,
                registry,
                block_ids=body.block_ids,
                select_all=body.select_all,
                mode=body.mode,
                confidence_threshold=body.confidence_threshold,
                force_provider=body.force_provider,
                force_model=body.force_model,
                thinking_budget=body.llm_thinking_budget,
            ),
        )
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"LLM 교정 실패: {e}"}, status_code=500)
    return draft


@router.post("/api/documents/{doc_id}/parts/{part_id}/pages/{page_number}/ocr/correct/apply")
async def api_apply_correction(
    doc_id: str, part_id: str, page_number: int, body: CorrectionApplyRequest
):
    """교정 초안을 L4에 쓴다. block_ids가 없으면 자동 수용된 블록만 (D-082)."""
    from ocr.correction_pass import apply_draft

    doc_path = require_repo_path("documents", doc_id)
    try:
        return apply_draft(doc_path, part_id, page_number, body.block_ids)
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"초안 적용 실패: {e}"}, status_code=500)


class OcrGuidanceRequest(BaseModel):
    """문헌 판독 지침 저장 요청 본문 (D-081)."""

    # 자유문. 빈 문자열·None이면 지침을 지운다.
    ocr_guidance: str | None = None


@router.put("/api/documents/{doc_id}/ocr-guidance")
async def api_put_ocr_guidance(doc_id: str, body: OcrGuidanceRequest):
    """문헌 판독 지침(manifest.ocr_guidance)을 저장한다 (D-081).

    목적: 시대·문서 종류·핵심 인명·지명·주의할 자형처럼 **이 문헌에만** 해당하는
          판독 지침을 연구자가 적어 두면, LLM OCR 프롬프트의 [문헌 정보] 조각에
          서지 자동 문장 뒤로 이어 붙는다. 자료마다 다른 어휘를 코드에 두지 않는다.
    입력: doc_id, body.ocr_guidance (None·빈 문자열 → 지움).
    출력: {"ocr_guidance": 저장된 값, "preview": 프롬프트에 실릴 [문헌 정보] 전문}
    """
    import jsonschema

    from core.document import get_bibliography, get_document_info, write_json_atomic
    from ocr.ocr_prompt import build_document_guidance

    doc_path = require_repo_path("documents", doc_id)
    try:
        manifest = get_document_info(doc_path)
    except FileNotFoundError:
        return JSONResponse({"error": f"문헌을 찾을 수 없습니다: {doc_id}"}, status_code=404)

    text = (body.ocr_guidance or "").strip()
    manifest["ocr_guidance"] = text or None

    # 스키마 검증 — 매니페스트는 문헌을 여는 열쇠라 깨진 채로 쓰면 되돌릴 수 없다.
    schema_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "schemas"
        / "source_repo"
        / "manifest.schema.json"
    )
    if schema_path.exists():
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        try:
            jsonschema.validate(instance=manifest, schema=schema)
        except jsonschema.ValidationError as e:
            return JSONResponse(
                {"error": f"매니페스트 검증 실패: {e.message}\n→ 지침은 문자열이어야 합니다."},
                status_code=400,
            )
    write_json_atomic(doc_path / "manifest.json", manifest)

    try:
        bibliography = get_bibliography(doc_path)
    except Exception:  # noqa: BLE001 — 서지가 없어도 지침 저장은 성공이다
        bibliography = None
    return {
        "ocr_guidance": manifest["ocr_guidance"],
        "preview": build_document_guidance(manifest, bibliography),
    }


@router.post("/api/documents/{doc_id}/parts/{part_id}/pages/{page_number}/ocr")
async def api_run_ocr(
    doc_id: str,
    part_id: str,
    page_number: int,
    body: OcrRunRequest,
):
    """페이지의 블록들을 OCR 실행한다.

    목적: 레이아웃 모드에서 OCR을 실행하고 결과를 L2_ocr/에 저장한다.
    입력:
        doc_id — 문헌 ID.
        part_id — 권 식별자.
        page_number — 페이지 번호 (1-indexed).
        body — {"engine_id": null, "block_ids": null}.
    출력: OcrPageResult.to_summary() 형식.
          일부 블록 실패 시에도 성공한 블록 결과를 반환한다 (부분 성공).

    처리 순서:
        1. L3 layout_page.json에서 블록 목록 로드
        2. L1_source에서 이미지 로드 (개별 파일 또는 PDF 페이지 추출)
        3. 각 블록: bbox 크롭 → 전처리 → OCR 엔진 인식
        4. 결과를 L2_ocr/{part_id}_page_{NNN}.json에 저장
    """
    library_path = get_library_path()
    if library_path is None:
        return JSONResponse({"error": "서고가 설정되지 않았습니다."}, status_code=500)

    doc_path = library_path / "documents" / doc_id
    if not doc_path.exists():
        return JSONResponse(
            {"error": f"문헌을 찾을 수 없습니다: {doc_id}"},
            status_code=404,
        )

    pipeline, _registry = _get_ocr_pipeline()

    # LLM 엔진용 추가 인자 (force_provider, force_model)
    engine_kwargs = {}
    if body.force_provider:
        engine_kwargs["force_provider"] = body.force_provider
    if body.force_model:
        engine_kwargs["force_model"] = body.force_model
    _add_llm_reasoning_kwargs(engine_kwargs, body)

    # PaddleOCR 엔진: 요청별 언어 지정 (공유 인스턴스 mutation 없음)
    # 왜 engine_kwargs로 전달하는가:
    #   이전에는 paddle_engine.lang을 직접 변경했는데, 동시 요청 시
    #   언어가 뒤바뀌는 레이스 컨디션이 발생했다 (공유 싱글톤 mutation).
    #   engine_kwargs로 전달하면 PaddleOcrEngine.recognize()에서
    #   언어별 캐시 인스턴스를 사용하여 안전하게 처리된다.
    if body.paddle_lang and body.engine_id == "paddleocr":
        engine_kwargs["paddle_lang"] = body.paddle_lang

    try:
        result = pipeline.run_page(
            doc_id=doc_id,
            part_id=part_id,
            page_number=page_number,
            engine_id=body.engine_id,
            block_ids=body.block_ids,
            **engine_kwargs,
        )
        return result.to_summary()
    except Exception as e:
        return JSONResponse(
            {"error": f"OCR 실행 실패: {e}"},
            status_code=500,
        )


@router.post("/api/documents/{doc_id}/parts/{part_id}/pages/{page_number}/ocr/stream")
async def api_run_ocr_stream(
    doc_id: str,
    part_id: str,
    page_number: int,
    body: OcrRunRequest,
):
    """OCR 실행 + SSE 스트리밍 진행률.

    목적: 블록별 진행률을 실시간으로 프론트엔드에 전달한다.
    출력: text/event-stream 형식.
        - progress 이벤트: {"type":"progress","current":2,"total":5,"block_id":"p01_b02"}
        - complete 이벤트: {"type":"complete", ...to_summary()}
        - error 이벤트: {"type":"error","error":"메시지"}
    """
    import asyncio
    import json as _json

    library_path = get_library_path()
    if library_path is None:
        return JSONResponse({"error": "서고가 설정되지 않았습니다."}, status_code=500)

    doc_path = library_path / "documents" / doc_id
    if not doc_path.exists():
        return JSONResponse(
            {"error": f"문헌을 찾을 수 없습니다: {doc_id}"},
            status_code=404,
        )

    pipeline, _registry = _get_ocr_pipeline()

    # 엔진 설정 (기존 api_run_ocr와 동일)
    engine_kwargs = {}
    if body.force_provider:
        engine_kwargs["force_provider"] = body.force_provider
    if body.force_model:
        engine_kwargs["force_model"] = body.force_model
    _add_llm_reasoning_kwargs(engine_kwargs, body)
    # PaddleOCR 요청별 언어 지정 (공유 인스턴스 mutation 없음)
    if body.paddle_lang and body.engine_id == "paddleocr":
        engine_kwargs["paddle_lang"] = body.paddle_lang

    # asyncio.Queue를 사용해 동기 콜백 → 비동기 제너레이터로 연결
    progress_queue: asyncio.Queue = asyncio.Queue()

    def _on_progress(data: dict):
        """OCR 파이프라인(동기)에서 호출되는 콜백.
        asyncio 이벤트 루프에 안전하게 큐에 넣는다."""
        progress_queue.put_nowait(data)

    async def _run_ocr_in_thread():
        """OCR를 별도 스레드에서 실행하고 결과를 큐에 넣는다."""
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: pipeline.run_page(
                    doc_id=doc_id,
                    part_id=part_id,
                    page_number=page_number,
                    engine_id=body.engine_id,
                    block_ids=body.block_ids,
                    progress_callback=_on_progress,
                    **engine_kwargs,
                ),
            )
            await progress_queue.put({"type": "complete", **result.to_summary()})
        except Exception as e:
            await progress_queue.put({"type": "error", "error": str(e)})

    async def _event_generator():
        """SSE 이벤트를 생성하는 비동기 제너레이터."""
        # OCR를 백그라운드 태스크로 시작
        task = asyncio.create_task(_run_ocr_in_thread())
        try:
            while True:
                data = await progress_queue.get()
                event_type = data.get("type", "progress")
                yield f"data: {_json.dumps(data, ensure_ascii=False)}\n\n"
                if event_type in ("complete", "error"):
                    break
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/documents/{doc_id}/parts/{part_id}/pages/{page_number}/ocr")
async def api_get_ocr_result(
    doc_id: str,
    part_id: str,
    page_number: int,
):
    """특정 페이지의 OCR 결과(L2)를 반환한다.

    목적: 교정 모드에서 기존 OCR 결과를 로드하기 위해 사용한다.
    입력:
        doc_id — 문헌 ID.
        part_id — 권 식별자.
        page_number — 페이지 번호 (1-indexed).
    출력: L2_ocr/{part_id}_page_{NNN}.json의 내용.
          파일이 없으면 404.
    """
    library_path = get_library_path()
    if library_path is None:
        return JSONResponse({"error": "서고가 설정되지 않았습니다."}, status_code=500)

    import json as _json

    filename = f"{part_id}_page_{page_number:03d}.json"
    legacy_filename = f"page_{page_number:03d}.json"
    ocr_path = library_path / "documents" / doc_id / "L2_ocr" / filename

    # 레거시 파일명 폴백 (part_id 없는 구형 파일 호환)
    if not ocr_path.exists():
        legacy_path = library_path / "documents" / doc_id / "L2_ocr" / legacy_filename
        if legacy_path.exists():
            ocr_path = legacy_path
            filename = legacy_filename
        else:
            return JSONResponse(
                {"error": f"OCR 결과가 없습니다: {doc_id}/{part_id}/page_{page_number:03d}"},
                status_code=404,
            )

    data = _json.loads(ocr_path.read_text(encoding="utf-8"))
    data["_meta"] = {
        "document_id": doc_id,
        "part_id": part_id,
        "page_number": page_number,
        "file_path": str(ocr_path.relative_to(library_path)),
    }
    return data


@router.delete("/api/documents/{doc_id}/parts/{part_id}/pages/{page_number}/ocr")
async def api_delete_ocr_result(
    doc_id: str,
    part_id: str,
    page_number: int,
):
    """특정 페이지의 OCR 결과(L2)를 휴지통으로 이동한다."""
    library_path = get_library_path()
    if library_path is None:
        return JSONResponse({"error": "서고가 설정되지 않았습니다."}, status_code=500)

    doc_path = library_path / "documents" / doc_id
    if not doc_path.exists():
        return JSONResponse(
            {"error": f"문헌을 찾을 수 없습니다: {doc_id}"},
            status_code=404,
        )

    filename = f"{part_id}_page_{page_number:03d}.json"
    legacy_filename = f"page_{page_number:03d}.json"
    ocr_path = doc_path / "L2_ocr" / filename

    if not ocr_path.exists():
        legacy_path = doc_path / "L2_ocr" / legacy_filename
        if legacy_path.exists():
            ocr_path = legacy_path
            filename = legacy_filename
        else:
            return JSONResponse(
                {"error": f"삭제할 OCR 결과가 없습니다: {doc_id}/{part_id}/page_{page_number:03d}"},
                status_code=404,
            )

    trash_dir = library_path / ".trash" / "ocr"
    trash_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    trash_name = f"{timestamp}_{doc_id}_{filename}"
    trash_path = trash_dir / trash_name

    try:
        shutil.move(str(ocr_path), str(trash_path))
    except Exception as e:
        return JSONResponse({"error": f"OCR 결과 삭제 실패: {e}"}, status_code=500)

    return {
        "status": "trashed",
        "document_id": doc_id,
        "part_id": part_id,
        "page_number": page_number,
        "trash_path": str(trash_path.relative_to(library_path)).replace("\\", "/"),
    }


@router.delete("/api/documents/{doc_id}/parts/{part_id}/pages/{page_number}/ocr/{block_id}")
async def api_delete_ocr_block_result(
    doc_id: str,
    part_id: str,
    page_number: int,
    block_id: str,
    index: int = Query(-1),
):
    """특정 OCR 결과 1건을 block_id + index로 강제 매칭하여 삭제한다.

    왜 이렇게 하는가:
      같은 페이지에서 layout_block_id가 겹치거나 중복 OCR 항목이 생길 수 있다.
      block_id만으로 삭제하면 여러 항목이 함께 지워질 위험이 있으므로,
      프론트가 보낸 index와 block_id를 동시에 검증해 단건만 삭제한다.
    """
    library_path = get_library_path()
    if library_path is None:
        return JSONResponse({"error": "서고가 설정되지 않았습니다."}, status_code=500)

    doc_path = library_path / "documents" / doc_id
    if not doc_path.exists():
        return JSONResponse(
            {"error": f"문헌을 찾을 수 없습니다: {doc_id}"},
            status_code=404,
        )

    import json as _json

    filename = f"{part_id}_page_{page_number:03d}.json"
    legacy_filename = f"page_{page_number:03d}.json"
    ocr_path = doc_path / "L2_ocr" / filename

    if not ocr_path.exists():
        legacy_path = doc_path / "L2_ocr" / legacy_filename
        if legacy_path.exists():
            ocr_path = legacy_path
        else:
            return JSONResponse(
                {"error": f"OCR 결과가 없습니다: {doc_id}/{part_id}/page_{page_number:03d}"},
                status_code=404,
            )

    try:
        data = _json.loads(ocr_path.read_text(encoding="utf-8"))
    except Exception as e:
        return JSONResponse({"error": f"OCR 파일 읽기 실패: {e}"}, status_code=500)

    ocr_results = data.get("ocr_results")
    if not isinstance(ocr_results, list):
        return JSONResponse({"error": "OCR 결과 형식이 올바르지 않습니다."}, status_code=500)

    if index < 0 or index >= len(ocr_results):
        return JSONResponse(
            {
                "error": "삭제할 OCR 항목 index가 유효하지 않습니다.",
                "index": index,
                "total": len(ocr_results),
            },
            status_code=400,
        )

    normalized_block_id = str(block_id or "").strip()
    target = ocr_results[index]
    target_block_id = str(target.get("layout_block_id") or "").strip()

    if target_block_id != normalized_block_id:
        return JSONResponse(
            {
                "error": "block_id와 OCR 항목 index가 일치하지 않습니다.",
                "expected_block_id": normalized_block_id,
                "actual_block_id": target_block_id,
                "index": index,
            },
            status_code=409,
        )

    deleted_item = ocr_results.pop(index)
    data["ocr_results"] = ocr_results

    try:
        ocr_path.write_text(
            _json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        return JSONResponse({"error": f"OCR 파일 저장 실패: {e}"}, status_code=500)

    return {
        "status": "deleted",
        "document_id": doc_id,
        "part_id": part_id,
        "page_number": page_number,
        "block_id": normalized_block_id,
        "index": index,
        "remaining": len(ocr_results),
        "deleted_text": "".join(
            [(line.get("text") or "") for line in (deleted_item.get("lines") or [])]
        ),
    }


@router.post("/api/documents/{doc_id}/parts/{part_id}/pages/{page_number}/ocr/{block_id}")
async def api_rerun_ocr_block(
    doc_id: str,
    part_id: str,
    page_number: int,
    block_id: str,
    body: OcrRunRequest,
):
    """특정 블록만 OCR을 재실행한다.

    목적: 하나의 블록만 다시 OCR 처리하고 기존 L2 결과에 반영한다.
          인식 결과가 좋지 않은 블록을 개별적으로 재시도할 때 사용한다.
    입력:
        doc_id — 문헌 ID.
        part_id — 권 식별자.
        page_number — 페이지 번호 (1-indexed).
        block_id — 재실행할 블록 ID (L3 layout의 block_id).
        body — {"engine_id": null} (다른 엔진으로 시도 가능).
    출력: OcrPageResult.to_summary() 형식 (해당 블록만 포함).
    """
    library_path = get_library_path()
    if library_path is None:
        return JSONResponse({"error": "서고가 설정되지 않았습니다."}, status_code=500)

    doc_path = library_path / "documents" / doc_id
    if not doc_path.exists():
        return JSONResponse(
            {"error": f"문헌을 찾을 수 없습니다: {doc_id}"},
            status_code=404,
        )

    pipeline, _registry = _get_ocr_pipeline()

    # 엔진별 추가 인자
    engine_kwargs = {}
    if body.force_provider:
        engine_kwargs["force_provider"] = body.force_provider
    if body.force_model:
        engine_kwargs["force_model"] = body.force_model
    _add_llm_reasoning_kwargs(engine_kwargs, body)
    # PaddleOCR 요청별 언어 지정 (공유 인스턴스 mutation 없음)
    if body.paddle_lang and body.engine_id == "paddleocr":
        engine_kwargs["paddle_lang"] = body.paddle_lang

    try:
        result = pipeline.run_block(
            doc_id=doc_id,
            part_id=part_id,
            page_number=page_number,
            block_id=block_id,
            engine_id=body.engine_id,
            **engine_kwargs,
        )
        return result.to_summary()
    except Exception as e:
        return JSONResponse(
            {"error": f"OCR 블록 재실행 실패: {e}"},
            status_code=500,
        )


# ===========================================================================
#  권(part) 단위 일괄 OCR
# ===========================================================================
#
# 왜 필요한가:
#   기존 OCR 라우트는 전부 페이지 단위다. 300쪽 문헌이면 사용자가
#   "페이지 선택 → 레이아웃 자동감지 → OCR 실행"을 300번 반복해야 한다.
#   근현대 논문처럼 페이지마다 판형이 같은 문헌에서는 이 반복에 의미가 없다.
#
# 왜 파이프라인을 고치지 않는가:
#   D-009의 계약(L3 → crop → 엔진 → L2, 파이프라인 경유)을 그대로 지킨다.
#   이 라우트는 쪽마다 기존 run_page()를 부르는 루프일 뿐이다.


@router.get("/api/documents/{doc_id}/parts/{part_id}/ocr/pending")
async def api_ocr_pending(doc_id: str, part_id: str):
    """돌리기 전에 «무엇이 몇 쪽 돌 것인지»를 알려 준다.

    입력: doc_id, part_id.
    출력: {
        "page_count": 15,
        "done": 14,        # OCR 결과가 있고 레이아웃과도 맞는 쪽
        "todo": 0,         # 아직 OCR 안 한 쪽
        "todo_pages": [],
        "stale": 1,        # 레이아웃을 고쳐 다시 돌아야 하는 쪽
        "stale_pages": [12],
        "will_run": 1      # 이대로 실행하면 실제로 도는 쪽 수
    }

    왜 필요한가:
        OCR 한 쪽마다 LLM 호출이 나간다. 버튼을 누르기 전에 «몇 쪽이 도는가»를
        알 수 있어야 한다. 특히 레이아웃을 고친 뒤 다시 돌릴 때, 전체가 다시
        도는 것인지 고친 쪽만 도는 것인지가 화면에 보여야 안심하고 누른다.
    """
    library_path = get_library_path()
    if library_path is None:
        return JSONResponse({"error": "서고가 설정되지 않았습니다."}, status_code=500)

    doc_path = library_path / "documents" / doc_id
    if not (doc_path / "manifest.json").exists():
        return JSONResponse({"error": f"문헌을 찾을 수 없습니다: {doc_id}"}, status_code=404)

    from core.document import get_document_info

    manifest = get_document_info(doc_path)
    part = next((p for p in manifest.get("parts", []) if p.get("part_id") == part_id), None)
    if part is None:
        return JSONResponse(
            {"error": f"권을 찾을 수 없습니다: part_id='{part_id}'"}, status_code=404
        )

    from ocr.layout_staleness import has_ocr_result, layout_changed_since_ocr

    page_count = _resolve_page_count(doc_path, part)
    todo_pages: list[int] = []
    stale_pages: list[int] = []
    done = 0
    for page_number in range(1, page_count + 1):
        if not has_ocr_result(doc_path, part_id, page_number):
            todo_pages.append(page_number)
            continue
        changed, _why = layout_changed_since_ocr(doc_path, part_id, page_number)
        if changed:
            stale_pages.append(page_number)
        else:
            done += 1

    return {
        "page_count": page_count,
        "done": done,
        "todo": len(todo_pages),
        "todo_pages": todo_pages,
        "stale": len(stale_pages),
        "stale_pages": stale_pages,
        "will_run": len(todo_pages) + len(stale_pages),
    }


@router.post("/api/documents/{doc_id}/parts/{part_id}/ocr/restore")
async def api_restore_ocr(
    doc_id: str,
    part_id: str,
    pages: str = Query(..., description="쉼표로 구분한 쪽 번호. 예: 3 또는 3,7"),
):
    """**바로 직전** 상태로 되돌린다. LLM을 부르지 않는다.

    입력: doc_id, part_id, pages — 되돌릴 쪽.
    출력: {"restored": [3], "no_backup": [7]}

    왜 필요한가:
        L2는 Git으로 추적되지 않아 «다시 돌렸는데 더 나빠졌다»에서 돌아갈
        길이 없었다. 배치가 덮어쓰기 직전에 한 벌 남기므로 그것을 되돌린다.

    어디까지 가는가:
        **방금 저장한 것 하나뿐이다.** OCR이든 교정이든 저장할 때마다
        직전 상태가 남으므로, 되돌리기는 언제나 그 하나를 취소한다.
        두 번은 안 된다.

    되돌린 뒤에도 백업은 남는다 — 지금 것과 자리를 바꾸므로 **두 상태를
    오갈 수 있다.** 어느 쪽이 나은지 비교하다 되돌아올 수 있어야 한다.
    """
    library_path = get_library_path()
    if library_path is None:
        return JSONResponse({"error": "서고가 설정되지 않았습니다."}, status_code=500)

    doc_path = library_path / "documents" / doc_id
    if not (doc_path / "manifest.json").exists():
        return JSONResponse({"error": f"문헌을 찾을 수 없습니다: {doc_id}"}, status_code=404)

    from ocr.page_backup import restore_backup

    wanted = [int(c) for c in pages.replace(" ", "").split(",") if c.isdigit()]
    if not wanted:
        return JSONResponse(
            {"error": "되돌릴 쪽을 지정하세요. 예: pages=3 또는 pages=3,7"},
            status_code=400,
        )

    restored, missing = [], []
    for page_number in wanted:
        if restore_backup(doc_path, part_id, page_number):
            restored.append(page_number)
        else:
            missing.append(page_number)

    return {"restored": restored, "no_backup": missing}


@router.post("/api/documents/{doc_id}/parts/{part_id}/ocr/fill-text")
async def api_fill_text_from_ocr(
    doc_id: str,
    part_id: str,
    overwrite: bool = False,
    pages: str | None = Query(None, description="쉼표로 구분한 쪽 번호. 비우면 전체"),
):
    """이미 있는 OCR 결과(L2)를 교정 텍스트(L4)로 옮긴다. LLM을 부르지 않는다.

    입력:
        doc_id, part_id.
        overwrite — 이미 있는 L4를 덮어쓸지(기본 False).
        pages — "3" 또는 "3,7,12". 비우면 이 권 전체.
    출력: {"filled": 12, "skipped": 3, "empty": 0, "total": 15}

    왜 필요한가:
        교정 탭은 L4를 읽는다. 그런데 배치 OCR이 L4를 채우기 전에 돌린 문헌은
        L2에 결과가 있어도 **교정 탭이 빈 화면**이다. 결과를 원본과 대조하려면
        교정 탭이 채워져 있어야 하는데, 그것 때문에 쪽마다 LLM을 다시 부르는
        것은 낭비다. 이 라우트는 이미 있는 결과를 옮기기만 한다.

    왜 기본이 덮어쓰지 않기인가:
        L4에는 사람이 손으로 고친 교정이 들어 있을 수 있다. OCR 원문으로
        덮으면 그 작업이 사라진다. 되돌릴 수 없는 쪽을 기본값으로 두지 않는다.
    """
    library_path = get_library_path()
    if library_path is None:
        return JSONResponse({"error": "서고가 설정되지 않았습니다."}, status_code=500)

    doc_path = library_path / "documents" / doc_id
    if not (doc_path / "manifest.json").exists():
        return JSONResponse({"error": f"문헌을 찾을 수 없습니다: {doc_id}"}, status_code=404)

    from core.document import get_document_info, get_page_text, save_page_text
    from ocr.layout_staleness import ocr_path, read_page_json

    manifest = get_document_info(doc_path)
    part = next((p for p in manifest.get("parts", []) if p.get("part_id") == part_id), None)
    if part is None:
        return JSONResponse(
            {"error": f"권을 찾을 수 없습니다: part_id='{part_id}'"}, status_code=404
        )

    page_count = _resolve_page_count(doc_path, part)

    # 쪽을 지정하면 그 쪽만. 「대조」 버튼이 한 쪽만 준비시킬 때 쓴다.
    targets = list(range(1, page_count + 1))
    if pages:
        wanted = {int(chunk) for chunk in pages.replace(" ", "").split(",") if chunk.isdigit()}
        targets = [p for p in targets if p in wanted]

    filled = skipped = empty = 0

    for page_number in targets:
        data = read_page_json(ocr_path(doc_path, part_id, page_number))
        results = (data or {}).get("ocr_results") or []
        text = "\n\n".join(
            "\n".join(ln.get("text") or "" for ln in (r.get("lines") or [])) for r in results
        ).strip()
        if not text:
            empty += 1
            continue

        if not overwrite:
            try:
                existing = (get_page_text(doc_path, part_id, page_number) or {}).get("text")
            except (FileNotFoundError, OSError):
                existing = None
            if existing and existing.strip():
                skipped += 1
                continue

        save_page_text(doc_path, part_id, page_number, text)
        filled += 1

    return {
        "filled": filled,
        "skipped": skipped,
        "empty": empty,
        "total": len(targets),
        "page_count": page_count,
    }


@router.get("/api/documents/{doc_id}/parts/{part_id}/ocr/overview")
async def api_ocr_overview(doc_id: str, part_id: str, preview_chars: int = 70):
    """쪽마다 OCR 결과가 어떤지 한눈에 보여 준다.

    입력: doc_id, part_id, preview_chars — 미리보기 글자 수.
    출력: {
        "page_count": 15,
        "median_chars": 950,          # 이상 판정의 기준선
        "pages": [{
            "page": 1, "lines": 32, "chars": 1098,
            "positioned": 30,          # 좌표를 가진 줄 수
            "blocks": 1,               # 읽은 LayoutBlock 수
            "preview": "본고는 18세기…",
            "flags": ["few_chars"]
        }, ...]
    }

    왜 필요한가:
        «12쪽만 다시 돌린다»를 하려면 **12쪽이 나쁘다는 것을 먼저 알아야 한다.**
        그런데 텍스트를 보는 경로가 쪽 단위뿐이라, 15쪽이면 15번 눌러 봐야
        어디가 나쁜지 알 수 있다. 300쪽이면 사실상 불가능하다.

    무엇을 «이상»이라 부르는가 (flags):
        empty      — 줄이 하나도 없다. 확실한 실패다.
        few_chars  — 글자 수가 이 권 중앙값의 40% 미만이다.
        no_position— 좌표를 가진 줄이 하나도 없다(형광 표시가 제자리에 안 뜬다).

    **few_chars는 «틀렸다»가 아니라 «봐 두라»는 표시다.** 표지·간지·참고문헌
    쪽은 원래 글자가 적다. 그래서 판정을 숨기지 않고 실제 글자 수를 함께
    돌려준다 — 최종 판단은 사람이 한다.
    """
    library_path = get_library_path()
    if library_path is None:
        return JSONResponse({"error": "서고가 설정되지 않았습니다."}, status_code=500)

    doc_path = library_path / "documents" / doc_id
    if not (doc_path / "manifest.json").exists():
        return JSONResponse({"error": f"문헌을 찾을 수 없습니다: {doc_id}"}, status_code=404)

    from core.document import get_document_info

    manifest = get_document_info(doc_path)
    part = next((p for p in manifest.get("parts", []) if p.get("part_id") == part_id), None)
    if part is None:
        return JSONResponse(
            {"error": f"권을 찾을 수 없습니다: part_id='{part_id}'"}, status_code=404
        )

    import statistics

    from ocr.layout_staleness import ocr_path, read_page_json
    from ocr.page_backup import has_backup

    page_count = _resolve_page_count(doc_path, part)
    pages = []
    for page_number in range(1, page_count + 1):
        data = read_page_json(ocr_path(doc_path, part_id, page_number))
        if data is None:
            # 아직 OCR 하지 않은 쪽. «결과가 나쁘다»와 구별해야 한다.
            pages.append(
                {
                    "page": page_number,
                    "lines": 0,
                    "chars": 0,
                    "positioned": 0,
                    "blocks": 0,
                    "preview": "",
                    "flags": ["not_run"],
                    "has_backup": has_backup(doc_path, part_id, page_number),
                }
            )
            continue

        results = data.get("ocr_results") or []
        texts, positioned, lines = [], 0, 0
        for result in results:
            for line in result.get("lines") or []:
                lines += 1
                text = line.get("text") or ""
                texts.append(text)
                if line.get("bbox"):
                    positioned += 1

        joined = " ".join(t.strip() for t in texts if t.strip())
        chars = sum(len(t.strip()) for t in texts)
        pages.append(
            {
                "page": page_number,
                "lines": lines,
                "chars": chars,
                "positioned": positioned,
                "blocks": len(results),
                "preview": joined[:preview_chars],
                "flags": [],
                # 되돌릴 수 있는 이전 결과가 있는가 (다시 돌린 쪽에만 생긴다).
                "has_backup": has_backup(doc_path, part_id, page_number),
            }
        )

    # 중앙값은 **글자가 나온 쪽만** 놓고 낸다. 안 돌린 쪽과 빈 쪽(0자)을
    # 섞으면 기준선이 끌려 내려가 진짜 부실한 쪽이 정상으로 보인다.
    # 실측 예: 15쪽 중 4쪽이 비어 있던 논문에서 중앙값이 840 → 939로 올라간다.
    scored = [p["chars"] for p in pages if p["lines"] > 0]
    median_chars = int(statistics.median(scored)) if scored else 0

    for entry in pages:
        if "not_run" in entry["flags"]:
            continue
        if entry["lines"] == 0:
            # 파일은 있는데 결과가 없다 = OCR이 돌았지만 아무것도 못 읽었다.
            # 아예 안 돌린 쪽(not_run)과 구별해야 원인을 좁힐 수 있다.
            entry["flags"].append("empty")
            continue
        if median_chars and entry["chars"] < median_chars * 0.4:
            entry["flags"].append("few_chars")
        if entry["positioned"] == 0:
            entry["flags"].append("no_position")

    return {
        "page_count": page_count,
        "median_chars": median_chars,
        "pages": pages,
    }


@router.post("/api/documents/{doc_id}/parts/{part_id}/ocr/batch")
async def api_run_ocr_batch(doc_id: str, part_id: str, body: OcrBatchRequest):
    """권 전체를 쪽 단위로 이어서 OCR 하고 SSE로 진행률을 보낸다.

    목적: 페이지마다 반복하던 "레이아웃 → OCR"을 한 번의 요청으로 끝낸다.
    입력: doc_id, part_id, body(OcrBatchRequest).
    출력: text/event-stream
        - start    : {"type":"start","total":N,"engine_id":...,"warnings":[...]}
        - page     : {"type":"page","page":3,"index":2,"total":10,"status":"ok",
                      "lines":12,"block_created":true}
        - skip     : {"type":"skip","page":3,...,"reason":"이미 OCR 결과가 있습니다."}
        - redo     : {"type":"redo","page":3,...,"reason":"레이아웃이 바뀌었습니다 ..."}
        - complete : {"type":"complete","processed":8,"skipped":2,"failed":0,
                      "redone":1,...}
        - error    : {"type":"error","error":"..."}

    중단과 재개:
        클라이언트가 연결을 끊으면 **쪽 경계에서** 멈춘다. 이미 끝난 쪽의
        결과는 L2에 남아 있으므로, 같은 요청을 다시 보내면
        skip_existing=True에 의해 남은 쪽부터 이어서 돈다.
        (별도 상태 파일이 필요 없다 — L2 자체가 체크포인트다.)

    부분 재-OCR:
        결과가 나쁜 몇 쪽만 레이아웃 탭에서 영역을 나눈 뒤 이 라우트를
        그대로 다시 부르면 된다. redo_changed_layout=True(기본)이면
        **레이아웃이 OCR 이후에 바뀐 쪽만** 골라 다시 돌고 나머지는
        건너뛴다. 쪽 번호를 기억해 입력할 필요가 없다.
        embed_after=True이면 그 결과가 반영된 텍스트 레이어 PDF가
        권 전체 기준으로 다시 만들어진다.
    """
    import asyncio
    import json as _json

    library_path = get_library_path()
    if library_path is None:
        return JSONResponse({"error": "서고가 설정되지 않았습니다."}, status_code=500)

    doc_path = library_path / "documents" / doc_id
    if not (doc_path / "manifest.json").exists():
        return JSONResponse({"error": f"문헌을 찾을 수 없습니다: {doc_id}"}, status_code=404)

    # 대상 쪽 목록을 정한다.
    from core.document import get_document_info

    manifest = get_document_info(doc_path)
    part = next((p for p in manifest.get("parts", []) if p.get("part_id") == part_id), None)
    if part is None:
        available = [p.get("part_id") for p in manifest.get("parts", [])]
        return JSONResponse(
            {
                "error": f"권을 찾을 수 없습니다: part_id='{part_id}'\n"
                f"→ 사용 가능한 part_id: {available}"
            },
            status_code=404,
        )

    page_count = _resolve_page_count(doc_path, part)
    if body.pages is None:
        targets = list(range(1, page_count + 1))
    elif page_count:
        # 쪽 수를 아는 경우에만 범위를 거른다.
        targets = [p for p in body.pages if 1 <= p <= page_count]
    else:
        targets = list(body.pages)

    if not targets:
        return JSONResponse(
            {
                "error": "OCR 할 쪽이 없습니다.\n"
                f"→ 이 권의 쪽 수는 {page_count}입니다. pages 값을 확인하세요."
            },
            status_code=400,
        )

    # LLM 교정 옵션 검증 — 오타가 조용히 «선별»로 동작하면 안 된다.
    if body.llm_correction not in ("off", "selected", "all"):
        return JSONResponse(
            {
                "error": f"llm_correction 값이 잘못되었습니다: {body.llm_correction!r} "
                "→ off | selected | all 중 하나"
            },
            status_code=400,
        )
    if body.llm_correction_mode not in ("fast", "precise"):
        return JSONResponse(
            {
                "error": f"llm_correction_mode 값이 잘못되었습니다: {body.llm_correction_mode!r} "
                "→ fast | precise"
            },
            status_code=400,
        )

    pipeline, registry = _get_ocr_pipeline()

    engine_kwargs = {}
    if body.force_provider:
        engine_kwargs["force_provider"] = body.force_provider
    if body.force_model:
        engine_kwargs["force_model"] = body.force_model
    _add_llm_reasoning_kwargs(engine_kwargs, body)
    if body.paddle_lang and body.engine_id == "paddleocr":
        engine_kwargs["paddle_lang"] = body.paddle_lang

    # 엔진 선택에 대한 사전 경고. 사용자가 300쪽을 다 돌린 뒤에
    # "한글이 하나도 안 나왔다"는 것을 알게 되면 안 된다.
    #
    # 기본 엔진은 "설치된 것 중 첫 번째"라(registry.py) 근현대 논문에도
    # 고전적 전용 엔진이 잡힌다. 그래서 이 경고가 특히 중요하다.
    warnings: list[str] = []
    effective_engine = body.engine_id or registry.default_engine_id
    if effective_engine in HANGUL_INCAPABLE_ENGINES:
        warnings.append(
            f"'{effective_engine}' 엔진은 한글을 인식하지 못합니다 "
            "(학습 데이터에 한글이 없습니다). "
            "→ 한글이 포함된 문헌이면 llm_vision 엔진을 사용하세요."
        )

    progress_queue: asyncio.Queue = asyncio.Queue()

    def _decide(page_number: int) -> tuple[bool, str]:
        """이 쪽을 돌릴지 판단한다.

        입력: page_number — 1-based 쪽 번호.
        출력: (돌릴 것인가, 사람이 읽을 사유)

        두 가지를 함께 본다:
          1) OCR 결과가 이미 있는가 (재개 — L2 자체가 체크포인트다)
          2) 그 결과가 지금 레이아웃과 맞는가 (부분 재-OCR)

        2)가 없으면 레이아웃 탭에서 손으로 나눈 쪽이 영원히 건너뛰어진다.
        """
        from ocr.layout_staleness import has_ocr_result, layout_changed_since_ocr

        if not body.skip_existing:
            return True, ""
        if not has_ocr_result(doc_path, part_id, page_number):
            return True, ""

        if body.redo_changed_layout:
            changed, why = layout_changed_since_ocr(doc_path, part_id, page_number)
            if changed:
                return True, why

        return False, "이미 OCR 결과가 있습니다."

    def _l4_is_hand_edited(dp, pid: str, page: int) -> bool:
        """확정본(L4)이 있고, 지금 L2를 그대로 옮긴 것과 다른가.

        L2가 없는데 L4가 있으면(텍스트 가져오기·손 입력) 역시 «OCR에서 온 것이 아니다»로 본다.
        비교는 일괄 OCR이 L4를 채울 때 쓰는 compose_page_text와 같은 모양으로 한다.
        """
        import json as _json

        from core.document import get_corrected_text
        from ocr.correction_pass import compose_page_text

        try:
            l4 = (get_corrected_text(dp, pid, page).get("corrected_text") or "").strip()
        except Exception:  # noqa: BLE001 — 확정본이 없으면 지킬 것도 없다
            return False
        if not l4:
            return False
        l2_path = dp / "L2_ocr" / f"{pid}_page_{page:03d}.json"
        if not l2_path.exists():
            return True
        try:
            l2 = _json.loads(l2_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True
        return compose_page_text(l2, None).strip() != l4

    async def _run_batch():
        """쪽을 하나씩 돌며 결과를 큐에 넣는다."""
        loop = asyncio.get_event_loop()
        processed = skipped = failed = redone = 0
        total_lines = 0

        # 이번 배치에서 쓴 LLM 사용량만 집계하려고 시작 지점을 기억한다.
        usage_start = _usage_snapshot()

        await progress_queue.put(
            {
                "type": "start",
                "total": len(targets),
                "engine_id": effective_engine,
                "warnings": warnings,
            }
        )

        try:
            for index, page_number in enumerate(targets):
                should_run, reason = _decide(page_number)
                if not should_run:
                    skipped += 1
                    await progress_queue.put(
                        {
                            "type": "skip",
                            "page": page_number,
                            "index": index,
                            "total": len(targets),
                            "reason": reason,
                        }
                    )
                    continue
                if reason:
                    # 레이아웃이 바뀌어 다시 도는 쪽이다. 왜 다시 도는지
                    # 그 자리에서 보이지 않으면 «건너뛴다더니 왜 도나»가 된다.
                    redone += 1
                    await progress_queue.put(
                        {
                            "type": "redo",
                            "page": page_number,
                            "index": index,
                            "total": len(targets),
                            "reason": reason,
                        }
                    )

                block_created = False
                # L4 보호(D-115 보강, Codex 지적 2026-09-07):
                # 이 쪽의 확정본이 «지금 L2를 그대로 옮긴 것»이
                # 아니면 사람이 고쳤거나 다른 데서 온 것이다 — OCR은 새로 하되 확정본은 두고 알린다.
                # (레이아웃이 바뀌어 다시 도는 쪽이 여기 걸린다.
                # 처음 도는 쪽은 L4가 비어 있어 걸리지 않는다.)
                keep_l4 = False
                if body.fill_text_layer:
                    keep_l4 = await loop.run_in_executor(
                        None, lambda p=page_number: _l4_is_hand_edited(doc_path, part_id, p)
                    )
                    if keep_l4:
                        warnings.append(
                            f"{page_number}쪽 확정본은 OCR 결과와 달라(사람이 고친 것으로 보여) "
                            "두었습니다. "
                            "새 OCR로 바꾸려면 교정 인덱스의 「OCR 채우기」를 누르세요."
                        )
                try:
                    # 0) 지금 결과를 한 벌 남긴다 (덮어쓰기 직전).
                    #
                    # 다시 돌렸는데 더 나빠졌을 때 돌아갈 곳이 필요하다.
                    # L2는 Git으로 추적되지 않으므로 이것이 유일한 안전망이다.
                    if body.backup_before_overwrite:
                        from ocr.page_backup import save_backup

                        await loop.run_in_executor(
                            None,
                            lambda p=page_number: save_backup(doc_path, part_id, p),
                        )

                    # 1) 레이아웃이 없으면 페이지 전면 블록을 만든다.
                    if body.auto_full_page_block:
                        from ocr.full_page_block import ensure_full_page_block

                        info = await loop.run_in_executor(
                            None,
                            lambda p=page_number: ensure_full_page_block(
                                doc_path,
                                part_id,
                                p,
                                writing_direction=body.writing_direction,
                            ),
                        )
                        block_created = bool(info.get("created"))

                    # 2) 기존 파이프라인으로 OCR (D-009 계약 그대로)
                    result = await loop.run_in_executor(
                        None,
                        lambda p=page_number: pipeline.run_page(
                            doc_id=doc_id,
                            part_id=part_id,
                            page_number=p,
                            engine_id=body.engine_id,
                            **engine_kwargs,
                        ),
                    )
                    summary = result.to_summary()
                    results = summary.get("ocr_results") or []
                    lines = sum(len(r.get("lines") or []) for r in results)
                    total_lines += lines
                    processed += 1

                    # 2.5) LLM 교정 패스 (D-082 1단계). 기본 off. "selected"면 신뢰도가
                    #      낮은 블록·협주·한글 미지원 엔진 결과만, "all"이면 전량을
                    #      LLM Vision으로 다시 읽어 초안을 만든다. L2는 그대로다.
                    correction_draft = None
                    corrected_blocks = 0
                    if body.llm_correction != "off" and results:
                        try:
                            correction_draft = await loop.run_in_executor(
                                None,
                                lambda p=page_number: _run_page_correction(
                                    doc_path,
                                    doc_id,
                                    part_id,
                                    p,
                                    pipeline,
                                    registry,
                                    select_all=(body.llm_correction == "all"),
                                    mode=body.llm_correction_mode,
                                    force_provider=body.force_provider,
                                    force_model=body.force_model,
                                    thinking_budget=body.llm_thinking_budget,
                                ),
                            )
                            corrected_blocks = sum(
                                1 for b in correction_draft.get("blocks", []) if b.get("accepted")
                            )
                        except Exception as e:  # noqa: BLE001 — 교정 실패로 OCR 결과를 버리지 않는다
                            warnings.append(f"{page_number}쪽 LLM 교정을 건너뜁니다: {e}")

                    # 3) OCR 텍스트를 교정 텍스트(L4)에도 넣는다.
                    #
                    # 확정본이 비어 있거나 «전 OCR을 그대로 옮긴 것»일 때만 — 사람이 고친 확정본은
                    # 위에서 keep_l4로 걸러 둔다(레이아웃이 바뀌어 다시 도는 쪽).
                    # 고서 흐름의 「OCR 채우기」 단추와 같은 일을 자동으로 한다.
                    # 교정 초안이 있으면 자동 수용된 블록은 교정본으로 바꿔 넣는다.
                    if body.fill_text_layer and lines and not keep_l4:
                        try:
                            from core.document import save_page_text
                            from ocr.correction_pass import compose_page_text

                            text = compose_page_text({"ocr_results": results}, correction_draft)
                            await loop.run_in_executor(
                                None,
                                lambda p=page_number, t=text: save_page_text(
                                    doc_path, part_id, p, t
                                ),
                            )
                        except Exception as e:  # noqa: BLE001
                            # L4 저장 실패로 OCR 결과까지 버리지 않는다.
                            # 다만 교정 탭이 비어 보일 것이므로 사유를 남긴다.
                            warnings.append(
                                f"{page_number}쪽의 교정 텍스트(L4)를 저장하지 "
                                f"못했습니다: {e}\n"
                                "→ 교정 탭이 비어 보이면 해당 쪽에서 "
                                "「OCR 채우기」를 눌러 주세요."
                            )
                    await progress_queue.put(
                        {
                            "type": "page",
                            "page": page_number,
                            "index": index,
                            "total": len(targets),
                            "status": summary.get("status"),
                            "lines": lines,
                            "block_created": block_created,
                            "corrected_blocks": corrected_blocks,
                            "errors": summary.get("errors") or [],
                        }
                    )
                except Exception as e:  # noqa: BLE001 — 한 쪽 실패로 전체를 멈추지 않는다
                    failed += 1
                    await progress_queue.put(
                        {
                            "type": "page",
                            "page": page_number,
                            "index": index,
                            "total": len(targets),
                            "status": "error",
                            "lines": 0,
                            "block_created": block_created,
                            "errors": [str(e)],
                        }
                    )

            # OCR이 끝났으면 텍스트 레이어 PDF까지 만든다.
            # 실패해도 OCR 결과는 유효하므로 배치 전체를 실패로 보지 않는다.
            embed_summary = None
            if body.embed_after and (processed or skipped):
                await progress_queue.put({"type": "baking", "total": len(targets)})
                try:
                    from export.text_layer_pdf import embed_text_layer

                    embed_result = await loop.run_in_executor(
                        None, lambda: embed_text_layer(doc_path, part_id)
                    )
                    embed_summary = embed_result.to_dict()
                except Exception as e:  # noqa: BLE001
                    warnings.append(
                        f"OCR은 끝났지만 텍스트 레이어 PDF를 입히지 못했습니다: {e}\n"
                        "→ 해결: 내보내기를 따로 실행해 보세요."
                    )

            await progress_queue.put(
                {
                    "type": "complete",
                    "processed": processed,
                    "skipped": skipped,
                    "failed": failed,
                    # processed 안에 포함된 값이다(따로 더하면 안 된다).
                    # 레이아웃을 고쳐 다시 돈 쪽이 몇 개인지 알려 준다.
                    "redone": redone,
                    "total": len(targets),
                    "total_lines": total_lines,
                    "engine_id": effective_engine,
                    "warnings": warnings,
                    "embedded": embed_summary,
                    "usage": _usage_since(usage_start),
                }
            )
        except asyncio.CancelledError:
            # 클라이언트가 끊었다. 여기까지의 결과는 L2에 남아 있으므로
            # 같은 요청을 다시 보내면 이어서 돈다.
            raise
        except Exception as e:  # noqa: BLE001
            await progress_queue.put({"type": "error", "error": str(e)})

    async def _event_generator():
        task = asyncio.create_task(_run_batch())
        try:
            while True:
                data = await progress_queue.get()
                yield f"data: {_json.dumps(data, ensure_ascii=False)}\n\n"
                if data.get("type") in ("complete", "error"):
                    break
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
