"""웹 앱 서버 — 라우터 조립 및 정적 파일 서빙.

FastAPI 기반. 서고 데이터를 API로 제공하고 정적 파일(HTML/CSS/JS)을 서빙한다.
D-001: 이 플랫폼의 주 인터페이스는 GUI이며, CLI는 보조 도구다.

아키텍처:
    이 파일은 FastAPI 앱 생성과 라우터 마운트, 미들웨어만 담당한다.
    실제 API 엔드포인트 216개가 app/routers/ 패키지의 9개 모듈에 분산된다
    (2026-09-06 실측):

    routers/documents.py     — 문헌 CRUD/페이지/교정/서지/파서·권 추가·경계·찍기 (43 라우트)
    routers/annotation.py    — L7 주석·사전형·인용마크 + AI보조 (34 라우트)
    routers/reading.py       — L5 표점·현토 + L6 번역 + 비고 + AI보조 (24 라우트)
    routers/composition.py   — 편성: 내용 트리·경계·제안·목차·자동 트리·신호 도출·쪼개기·리셋 (13 라우트)
    routers/interpretations.py — 해석 CRUD·레이어·의존·엔티티·관계·태그 (22 라우트)
    routers/llm_ocr.py       — LLM 상태·분석 + OCR 실행·일괄·되돌리기·교정 패스 (24 라우트)
    routers/alignment.py     — 이체자 사전/정렬/일괄교정/문헌별 승인 (20 라우트)
    routers/library.py       — 서고·설정·백업·휴지통·검증·연결·업데이트·엔진·로그인·모델 후보 (29 라우트)
    routers/version.py       — Git 그래프/되돌리기/스냅샷/가져오기 (7 라우트)

    공유 상태 및 헬퍼는 app/_state.py에 집약.

    이 수치는 scripts/check_doc_drift.py가 검사한다(D-079). 라우트를 늘리거나 줄이면
    여기와 CLAUDE.md·AGENTS.md를 함께 고쳐야 pytest가 통과한다.
"""

import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# src/ 디렉토리를 Python 경로에 추가
_src_dir = str(Path(__file__).resolve().parent.parent)
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from app._state import (  # noqa: E402,F401
    RepoPathError,
    configure_library,
    set_library_path,
)
from app.routers import (  # noqa: E402,F401
    alignment,
    annotation,
    composition,
    documents,
    interpretations,
    library,
    llm_ocr,
    reading,
    version,
)


# 앱 버전.
#
# 왜 pyproject.toml을 직접 읽는가: **버전을 적는 곳은 하나여야 한다.**
# 여러 곳에 적으면 릴리스 때 일부만 고쳐져 화면이 옛 버전을 말하게 된다.
# 설치된 배포판의 메타데이터(dist-info)는 «실행 중인 인터프리터»의 것이라, GPU PC(.venv-gpu로 뜸)에서는
# uv sync가 갱신하는 .venv와 어긋나 화면 아래에 옛 판(1.2.1)이 남았다(2026-09-06 보고).
# core.updater.current_version()이 pyproject → dist-info 순으로 읽는 정본이다.
def _app_version() -> str:
    """pyproject.toml의 판 번호. 실패하면 «unknown»."""
    try:
        from core.updater import current_version

        return current_version()
    except Exception:  # noqa: BLE001 — 저장소 밖에서 임포트될 때 등
        return "unknown"


APP_VERSION = _app_version()

app = FastAPI(
    title="고전서지 통합 브라우저",
    description="사람과 LLM이 함께 고전 텍스트를 읽고 번역하고 연구하는 통합 작업 환경",
    version=APP_VERSION,
)


@app.get("/api/app/version")
async def api_app_version():
    """앱 버전을 알려준다.

    목적: 화면 아래 상태바가 버전을 **하드코딩하지 않도록** 한다.
    출력: {"version": "1.2.0"}
    """
    return {"version": APP_VERSION}


# ── 저장소 경로 오류 → 표준 에러 응답 변환 ─────────────
# require_repo_path()(_state.py)가 던지는 RepoPathError를 이 프로젝트의
# 에러 규약({"error": ...} + 상태코드)으로 바꾼다. 라우터 각 지점에
# 오류 분기를 복제하지 않기 위한 단일 변환 지점이다.
@app.exception_handler(RepoPathError)
async def _repo_path_error_handler(request, exc: RepoPathError):
    from fastapi.responses import JSONResponse

    return JSONResponse({"error": str(exc)}, status_code=exc.status_code)


# ── 라우터 마운트 ─────────────────────────────────
app.include_router(library.router)
app.include_router(documents.router)
app.include_router(composition.router)
app.include_router(interpretations.router)
app.include_router(llm_ocr.router)
app.include_router(alignment.router)
app.include_router(reading.router)
app.include_router(annotation.router)
app.include_router(version.router)

# ── 정적 파일 서빙 ───────────────────────────────
# 서고 유무와 관계없이 항상 마운트한다.


class _NoCacheStaticFiles(StaticFiles):
    """정적 파일에 재검증을 강제하는 StaticFiles.

    왜 필요한가:
        기본 StaticFiles는 Cache-Control을 붙이지 않는다. 그러면 브라우저가
        Last-Modified를 근거로 스스로 캐시 기간을 추정해(heuristic caching)
        **고친 JS·CSS를 다시 받지 않는다.** `?v=` 쿼리를 손으로 올려도
        올리는 것을 잊으면 같은 일이 벌어진다. 실제로 그 사고가 있었다:
        코드는 고쳐졌는데 화면은 옛 동작 그대로였다.

    왜 no-store가 아니라 no-cache인가:
        no-cache는 «캐시하되 쓸 때마다 서버에 물어보라»는 뜻이다.
        ETag가 함께 나가므로 내용이 그대로면 서버가 304를 돌려주어
        본문 전송이 없다. 로컬에서 도는 앱이라 이 왕복은 무시할 수준이고,
        대신 «고쳤는데 반영이 안 된다»가 원천적으로 사라진다.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


_static_dir = Path(__file__).parent / "static"
app.mount("/static", _NoCacheStaticFiles(directory=str(_static_dir)), name="static")


@app.middleware("http")
async def _no_store_api(request, call_next):
    """API 응답에 캐시 금지를 붙인다.

    왜 필요한가:
        API가 돌려주는 것은 **작업 중에 바뀌는 데이터**다 — OCR 결과, 레이아웃,
        교정, 진행 상황. 그런데 응답에 Cache-Control이 없으면 브라우저가
        스스로 캐시 기간을 추정한다(heuristic caching).

        실제로 그 사고가 있었다: 배치 OCR이 L3 전면 블록을 새로 만들었는데
        레이아웃 탭에는 «블록이 없던 시절»이 남아 있었다. 교정 탭은 요청마다
        `cache: "no-store"`를 붙여 두어 바로 반영됐고, 레이아웃만 빠져 있었다.

    왜 호출부가 아니라 여기인가:
        점검해 보니 프론트에서 **변하는 데이터를 캐시 지정 없이 읽는 곳이
        47군데**였다. 한 곳씩 고치면 새로 쓰는 코드에서 또 빠진다.
        서버가 한 번 붙이면 그 부류가 통째로 사라진다.

    정적 파일과 달리 no-store인 이유:
        정적 파일은 내용이 그대로면 304로 끝나 이득이 있다(위 참조).
        API 응답은 매번 달라지는 것이 정상이라 재검증할 값이 없다.

    빼는 것 하나 — 원본 PDF:
        `/pdf/{part}`는 «작업 중에 바뀌는 데이터»가 아니라 **원본 파일**이다. 78.9MB짜리
        문헌에 no-store를 붙이면 쪽을 넘길 때마다 조각을 다시 받는다. ETag가 함께 나가므로
        no-cache(«쓸 때마다 물어보라»)면 바뀌지 않은 동안 304로 끝난다 —
        «고쳤는데 반영이 안 된다»는 그대로 막으면서 재전송만 없앤다.
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        if "/pdf/" in path:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        else:
            response.headers["Cache-Control"] = "no-store"
    return response


# CTB_LIBRARY 환경변수가 있으면 그 서고로 시작한다(스크립트·컨테이너에서 서고를 넘길 때). 없으면
# serve --library 또는 브라우저에서 고른다.
_env_library = os.environ.get("CTB_LIBRARY")
if _env_library and Path(_env_library).exists():
    configure_library(_env_library)
    print(f"서고(자동 재적재): {_env_library}")


def configure(library_path: str | Path) -> FastAPI:
    """서고 경로를 설정하고 정적 파일 마운트를 수행한다.

    목적: 서버 시작 전(또는 런타임에 서고 전환 시) 서고 경로를 지정한다.
    입력: library_path — 서고 디렉토리 경로.
    출력: 설정된 FastAPI 앱 인스턴스.

    서고 전환 시 주의:
        - LLM 라우터 캐시를 초기화한다 (서고별 .env가 다를 수 있음).
        - 최근 서고 목록에 추가한다.
    """
    configure_library(library_path)
    return app


# ── 하위 호환: parsers/generic_llm.py 등에서 사용 ──
# 기존에 `from app.server import _get_llm_router` 형태로 접근하는 코드 지원
from app._state import _get_llm_router  # noqa: E402,F401
