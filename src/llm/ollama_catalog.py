"""Ollama 비전 모델 후보 — «어느 모델을 받을까»를 사람이 고를 수 있게 목록을 준다 (D-114).

왜 목록이 필요한가:
    비전 모델이 없는 PC에서 앱이 «기본 모델 하나»만 받아 주면 두 가지가 어긋난다.
    ① 기본을 로컬 모델(gemma4:e4b, 9.6GB)로 두면 처음 켠 PC가 몇 분을 받기만 하고,
       사양이 낮은 PC에서는 받아도 느리다. ② 기본을 클라우드 모델로 두면 내려받는 파일은
       없지만(등록만 된다) ollama.com 로그인이 있어야 돈다 — 로그인하지 않을 사람은
       로컬 모델이 필요하다. 어느 쪽이 맞는지는 PC와 사람이 정한다. 그래서 기본은
       클라우드로 두고(2026-09-06 지시), 화면에서 다른 후보를 골라 받을 수 있게 한다.

무엇을 아는가:
    - 내장 후보(`BUILTIN`): 2026-09-06에 registry.ollama.ai 매니페스트로 확인한 이름과 크기.
      크기는 매니페스트 layers 합(GB, 소수 1자리). 클라우드 모델은 0 — 매니페스트 300B뿐.
    - 살아 있는 클라우드 목록: ollama.com 검색 페이지(비전·클라우드 필터)를 읽어 새 모델을
      더한다. 클라우드 모델은 은퇴한다(qwen3-vl:235b-cloud, 2026-06-16 — 목록에 있어도 410).
      검색 페이지에 없는 내장 클라우드 후보는 «은퇴했을 수 있음»으로 표시만 하고 지우지는 않는다
      (페이지 구조가 바뀌어 하나도 못 읽는 날, 목록이 텅 비면 안 된다).
    - 이 PC에 무엇이 깔렸는가는 호출자가 준다(`/api/tags`). 이 모듈은 네트워크를 그 페이지
      하나만 읽고, 그것도 1시간 캐시한다.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# 비전 모델이 없을 때 앱과 설치 스크립트가 받는 기본값. 클라우드 — 내려받는 파일이 없다.
# `llm.providers.ollama.OllamaProvider.DEFAULT_MODELS["vision"]`이 이 값을 쓴다.
DEFAULT_VISION_MODEL = "gemma4:cloud"

# 비전이 아닌 클라우드 후보 — OCR에는 못 쓰지만 편성·번역·주석 같은 텍스트 일에 고를 수 있다.
# ollama.com 검색 페이지(비전 필터)에 없으므로 «은퇴했을 수 있음» 판정에서 뺀다
# (glm-5.3:cloud가 화면에 안 보인다는 지적 2026-09-07 — 목록에 flash판만 있었다).
TEXT_ONLY: frozenset[str] = frozenset({"glm-5.3:cloud"})

# (이름, 종류, 크기 GB, 한 줄 설명). 크기는 registry.ollama.ai 매니페스트 실측(2026-09-06).
BUILTIN: list[tuple[str, str, float, str]] = [
    ("gemma4:cloud", "cloud", 0.0, "Google Gemma 4 — 기본. 로그인만 있으면 바로 씁니다"),
    ("qwen3.5:cloud", "cloud", 0.0, "Alibaba Qwen 3.5 397B"),
    ("kimi-k2.6:cloud", "cloud", 0.0, "Moonshot Kimi K2.6"),
    ("kimi-k3:cloud", "cloud", 0.0, "Moonshot Kimi K3"),
    ("minimax-m3:cloud", "cloud", 0.0, "MiniMax M3"),
    ("glm-5.3-flash:cloud", "cloud", 0.0, "Zhipu GLM 5.3 Flash"),
    ("glm-5.3:cloud", "cloud", 0.0, "Zhipu GLM 5.3 — 텍스트용(비전 아님: 편성·번역·주석에)"),
    ("gemma4:e4b", "local", 9.6, "Google Gemma 4 E4B — v1.3.0까지의 기본 모델"),
    ("gemma4:e2b", "local", 7.2, "Google Gemma 4 E2B — 조금 작은 판"),
    ("qwen3-vl:8b", "local", 6.1, "Alibaba Qwen3-VL 8B"),
    ("qwen3-vl:4b", "local", 3.3, "Alibaba Qwen3-VL 4B"),
    ("qwen3-vl:2b", "local", 1.9, "Alibaba Qwen3-VL 2B"),
    ("glm-ocr:latest", "local", 2.2, "GLM-OCR — 글자 읽기 전용(대화 불가)"),
    ("minicpm-v4.6:latest", "local", 1.6, "MiniCPM-V 4.6 — 가장 작습니다"),
]

_SEARCH_URL = "https://ollama.com/search?c=vision&c=cloud"
_LIBRARY_LINK = re.compile(r'href="/library/([a-z0-9][a-z0-9._-]*)"')
_TTL = 3600.0
_live: dict = {"names": None, "at": 0.0}
_REGISTRY = "https://registry.ollama.ai/v2/library/{repo}/manifests/cloud"
_tag_ok: dict[str, bool | None] = {}  # repo → :cloud 태그가 있는가(None = 확인 못 함)


def cloud_tag_exists(repo: str, timeout: float = 3.0) -> bool | None:
    """레지스트리에 `<repo>:cloud` 매니페스트가 있는가. 검색 페이지에 있어도 태그가 없는 것이 있다
    (실측 2026-09-06: mistral-large-3 — 404). 출력: True/False, 네트워크가 없으면 None. 프로세스 캐시."""
    if repo in _tag_ok:
        return _tag_ok[repo]
    req = urllib.request.Request(
        _REGISTRY.format(repo=repo), method="HEAD", headers={"User-Agent": "ctb-catalog"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            ok: bool | None = True
    except urllib.error.HTTPError as e:
        ok = False if e.code == 404 else None
    except Exception:  # noqa: BLE001 — 오프라인
        ok = None
    _tag_ok[repo] = ok
    return ok


def fetch_cloud_vision_names(timeout: float = 5.0, *, force: bool = False) -> list[str] | None:
    """ollama.com 검색(비전·클라우드)에서 모델 이름을 읽는다. 결과는 1시간 캐시.

    출력: ["gemma4", "qwen3.5", ...] (태그 없는 저장소 이름). 못 읽으면 None —
          빈 목록([])과 구별한다. 빈 목록은 «페이지는 읽었는데 하나도 없다»이고,
          None은 «모른다»라 은퇴 표시를 하면 안 된다.
    """
    now = time.time()
    if not force and _live["names"] is not None and now - _live["at"] < _TTL:
        return _live["names"]
    try:
        req = urllib.request.Request(_SEARCH_URL, headers={"User-Agent": "ctb-catalog"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 — 오프라인이면 내장 목록만 쓴다
        logger.info(f"Ollama 클라우드 비전 목록을 읽지 못했습니다(내장 목록만 씁니다): {e}")
        return None
    names: list[str] = []
    for n in _LIBRARY_LINK.findall(html):
        if n not in names:
            names.append(n)
    if not names:
        # 페이지는 왔는데 링크가 하나도 없다 — 구조가 바뀐 것이다. «모른다»로 다룬다.
        logger.warning(
            "ollama.com 검색 페이지에서 모델 링크를 찾지 못했습니다 — 구조가 바뀌었을 수 있습니다."
        )
        return None
    _live.update({"names": names, "at": now})
    return names


def catalog(installed: set[str] | None = None, *, live: bool = True) -> dict:
    """받을 수 있는 비전 모델 후보를 화면용으로 만든다.

    입력: installed — 이 PC의 `/api/tags` 이름들. live — ollama.com에서 새 클라우드 모델을 더할지.
    출력: {"default": 기본 이름, "models": [{name, kind, size_gb, note, installed, maybe_retired}]}.
          순서: 기본 → 나머지 클라우드 → 로컬(작은 것부터가 아니라 내장 순서 — 권장 순이다).
    """
    installed = {n for n in (installed or set()) if n}
    # ollama는 태그 없는 이름을 :latest로 저장한다 — «glm-ocr»를 받았으면 목록에는 glm-ocr:latest다.
    bare = {n.split(":")[0] for n in installed if n.endswith(":latest")}

    live_names = fetch_cloud_vision_names() if live else None
    rows: list[dict] = []
    seen: set[str] = set()
    for name, kind, size, note in BUILTIN:
        repo = name.split(":")[0]
        # 검색 페이지는 «비전» 필터라 텍스트 전용 클라우드 모델은 거기 없어도 은퇴가 아니다
        maybe_retired = (
            kind == "cloud"
            and name not in TEXT_ONLY
            and live_names is not None
            and repo not in live_names
        )
        rows.append(
            {
                "name": name,
                "kind": kind,
                "size_gb": size,
                "note": note,
                "installed": name in installed or (name.endswith(":latest") and repo in bare),
                "maybe_retired": maybe_retired,
            }
        )
        seen.add(name)
    # 검색 페이지에 있는데 내장 목록에 없는 클라우드 모델 — 새로 나온 것. 설명은 없다.
    for repo in live_names or []:
        name = f"{repo}:cloud"
        if name in seen:
            continue
        if cloud_tag_exists(repo) is False:
            continue  # 검색 페이지에는 있지만 :cloud 태그가 없다 — 받기가 반드시 실패한다
        rows.append(
            {
                "name": name,
                "kind": "cloud",
                "size_gb": 0.0,
                "note": "ollama.com 목록에서 새로 찾음",
                "installed": name in installed,
                "maybe_retired": False,
            }
        )
        seen.add(name)
    # 기본 → 클라우드 → 로컬. 각 묶음 안에서는 원래 순서(권장 순)를 지킨다.
    rows.sort(key=lambda r: (r["name"] != DEFAULT_VISION_MODEL, r["kind"] != "cloud"))
    return {"default": DEFAULT_VISION_MODEL, "models": rows}
