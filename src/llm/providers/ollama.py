"""Ollama Provider (1순위).

Ollama 로컬 서버(localhost:11434)를 통한 LLM 호출.
기본 비전 모델: gemma4:cloud — 클라우드(내려받는 파일 없음, ollama.com 로그인 필요, D-114)
기본 텍스트 모델: gemma4:e4b (로컬)

호출 흐름:
    Python → HTTP POST localhost:11434/api/generate
          → Ollama → 로컬 모델 실행
          → 결과 반환
"""

import base64
import json
import logging
import time

import httpx

from ..ollama_catalog import DEFAULT_VISION_MODEL
from .base import (
    TRUNCATED_MARK,
    BaseLlmProvider,
    LlmProviderError,
    LlmResponse,
    thinking_options,
)

logger = logging.getLogger(__name__)


def _looks_like_json(text: str) -> bool:
    """답이 JSON 객체(또는 배열)로 읽히는가.

    앞뒤 군말은 걷어 내고 본다(core.toc.lenient_json과 같은 눈).
    """
    t = (text or "").strip()
    if not t:
        return False
    for cand in (
        t,
        t[t.find("{") : t.rfind("}") + 1] if "{" in t else "",
        t[t.find("[") : t.rfind("]") + 1] if "[" in t else "",
    ):
        if not cand:
            continue
        try:
            json.loads(cand)
            return True
        except ValueError:
            continue
    return False


class OllamaProvider(BaseLlmProvider):
    """Ollama 로컬 서버를 통한 LLM 호출. 기본 비전 모델: gemma4:cloud, 텍스트: gemma4:e4b."""

    provider_id = "ollama"
    display_name = "Ollama"
    supports_image = True

    # 용도별 기본 모델
    # 비전: gemma4:cloud (D-114) — 비전 모델이 없는 PC가 처음 받는 것. 내려받는 파일이 없어
    #   몇 초에 끝나지만 ollama.com 로그인이 있어야 돈다. 로그인하지 않을 PC는 화면의
    #   「모델 받기」에서 로컬 모델을 고른다(llm/ollama_catalog.py). v1.3.0까지는 gemma4:e4b(9.6GB)였다.
    # 일반 텍스트: gemma4:e4b (로컬, 멀티모달)
    # JSON 구조화 출력(표점/주석): 소형 로컬 모델은 품질이 떨어지므로
    #   클라우드 프록시 모델을 우선 사용한다.
    #   클라우드 프록시가 없으면 gemma4:e4b로 폴백.
    #
    # 왜 표점/주석은 별도 모델인가:
    #   표점(구두점)은 고전 한문의 문맥을 이해해야 정확하고,
    #   JSON 배열 형식으로 출력해야 한다. gemma4:e4b(소형)로는
    #   구두점 위치 정확도와 JSON 구조 준수율이 크게 떨어진다.
    DEFAULT_MODELS = {
        "text": "gemma4:e4b",
        "vision": DEFAULT_VISION_MODEL,
        "translation": "gemma4:e4b",
        "json": "gemma4:e4b",
        "punctuation": "gemma4:e4b",
        "annotation": "gemma4:e4b",
    }

    # JSON 구조화 출력에 소형 모델이 부적합한 용도 목록.
    # 이 용도들은 LLM Router의 자동 폴백에서 Ollama를 건너뛰고
    # 다음 프로바이더(Gemini 등)로 넘어가도록 한다.
    SKIP_FOR_PURPOSES = {"punctuation", "annotation"}

    # 로컬 실행이 기본이지만 `:cloud` 모델은 구독 한도를 쓴다.
    billing_model = "free"

    # 로컬 모델만 쓸 거면 설치·기동으로 끝난다. `:cloud` 모델을 쓰려면
    # ollama.com 계정으로 로그인해야 하고, 그것은 터미널에서만 된다.
    setup_kind = "cli_signin"
    setup_steps = (
        "ollama serve   (서버가 이미 떠 있으면 생략)",
        "ollama signin  (클라우드 모델을 쓸 때만 — 브라우저가 열립니다)",
    )

    async def account_info(self) -> dict | None:
        """ollama.com 로그인 계정을 조회한다.

        출력: {"account": 이메일, "plan": 요금제} — 로그인 안 됐으면 None.

        왜 이 조회가 필요한가:
            Ollama 서버는 **로그인하지 않아도 뜬다.** 그래서 is_available()은
            True인데 `:cloud` 모델을 부르면 실패한다. 라우터는 조용히 다음
            프로바이더(유료 API)로 넘어가므로 사용자는 왜 Ollama가 아니라
            Gemini가 돌았는지 알 수 없다 — 실제로 그 사고가 있었다(D-056).

        남은 한도는 돌려주지 않는다. Ollama가 그 정보를 제공하지 않는다
            (실측 2026-07-26: /api/me 응답에 사용량·잔여 한도 없음,
             응답 헤더에도 X-RateLimit-* 없음).
        """
        try:
            async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
                # GET이 아니라 POST다 (GET은 405를 준다).
                resp = await client.post(f"{self._url}/api/me", json={})
            if resp.status_code != 200:
                return None
            data = resp.json()
        except (httpx.HTTPError, OSError, ValueError):
            return None

        email = data.get("email") or data.get("name")
        if not email:
            return None
        return {"account": email, "plan": data.get("plan")}

    def billing_for_model(self, model: str | None = None) -> str:
        """모델 이름으로 과금 방식을 가른다.

        Ollama는 로컬과 클라우드가 한 프로바이더에 섞여 있다.
        `qwen3.5:cloud` 처럼 이름에 `cloud`가 들어가면 ollama.com 계정의
        **구독 한도**를 소모한다 — 금액은 0이지만 공짜가 아니다.
        로컬 모델은 이 PC에서 도므로 소모하는 것이 없다.
        """
        if model and "cloud" in model:
            return "subscription"
        return "free"

    # 기본 주소로 안 닿을 때 찾아낸 주소. 프로세스 전체가 공유한다(라우터를 새로 만들어도 유지).
    _url_override: str | None = None

    @property
    def _url(self) -> str:
        default = "http://127.0.0.1:11434"
        url = self.config.get("ollama_url", default)
        # 사람이 .env에 localhost로 적어 두어도 127.0.0.1로 부른다 — Windows의 IPv6 우선 시도가
        # 호출마다 2초를 버리고, 느린 기기에서는 제한 시간을 넘겨 «Ollama 없음»으로 오판한다
        # (2026-09-05, 다른 PC에서 Ollama가 떠 있는데 안 잡히던 보고).
        url = url.replace("://localhost:", "://127.0.0.1:").replace(
            "://localhost/", "://127.0.0.1/"
        )
        # 찾아낸 주소([::1])는 사용자가 «다른» 주소를 정해 두지 않았을 때 쓴다. 기본 주소를 그대로
        # 적어 둔 것은 is_alive가 [::1]도 보는 조건과 같아야 한다 — 전에는 is_alive는 [::1]에서
        # 찾았다고 «떠 있음»으로 보고하면서 호출은 127.0.0.1로 나가 실패했다(Codex 지적 2026-09-06).
        explicit_other = self.config.is_set("ollama_url") and url.rstrip("/") != default
        if OllamaProvider._url_override and not explicit_other:
            return OllamaProvider._url_override
        return url

    async def _pick_vision_model(self) -> str:
        """실제로 쓸 수 있는 비전 모델을 고른다.

        출력: 모델 이름. 찾지 못하면 DEFAULT_MODELS["vision"]을 그대로 돌려준다
              (그 경우 호출이 실패하고 라우터가 다음 프로바이더로 넘어간다).

        고르는 순서:
            1. 설정에 `ollama_vision_model`이 있으면 그것 (사용자 지정 우선)
            2. DEFAULT_MODELS["vision"]이 설치돼 있으면 그것 — 단 클라우드 기본은 한 번 불러
               본다(로그인이 없거나 은퇴했으면 아래로 내려간다, D-114)
            3. 설치된 비전 모델 중 **클라우드 우선** — 로컬 소형 모델은
               이 PC 사양에서 성능이 떨어진다는 사용자 판단에 따른다.
            4. 그래도 없으면 로컬 비전 모델

        결과는 캐시한다. 매 쪽마다 /api/show를 부르면 OCR이 느려진다.
        """
        configured = self.config.get("ollama_vision_model")
        if configured:
            return configured

        # 공유 캐시만 본다 — 인스턴스 캐시를 먼저 보면 공유 캐시가 만료·무효화돼도 옛 값이 남는다.
        cached = self._shared_get("vision")
        if cached:
            return cached

        default = self.DEFAULT_MODELS["vision"]
        try:
            models = await self.list_models()
        except Exception:  # noqa: BLE001 — 목록을 못 받으면 기본값으로 시도한다
            return default

        names = {m.get("name") for m in models}
        # 기본이 클라우드 모델이면 목록에 있어도 한 번 불러 본다 — 로그인이 없거나 은퇴했으면
        # 아래에서 로컬 모델을 찾는다(D-114). 로컬 기본은 목록에 있으면 그대로 쓴다.
        if default in names and ("cloud" not in default or await self._is_model_alive(default)):
            self._vision_model_cache = default
            self._shared_set("vision", default)
            self._shared_set("vision_dead", None)
            return default

        vision = [m.get("name") for m in models if m.get("vision")]
        if not vision:
            logger.warning(
                "Ollama에 비전 모델이 없습니다. 이미지 호출은 다음 프로바이더로 넘어갑니다. "
                "→ 해결: ollama pull 로 비전 모델을 받거나 .env에 "
                "OLLAMA_VISION_MODEL 을 지정하세요."
            )
            return default

        # 클라우드 모델을 앞세운다 (로컬 소형 모델은 이 PC에서 성능이 낮다).
        cloud = [n for n in vision if n and "cloud" in n]
        for candidate in cloud + [n for n in vision if n not in cloud]:
            if candidate == default:
                continue  # 위에서 이미 불러 봤다
            if candidate and await self._is_model_alive(candidate):
                self._vision_model_cache = candidate
                self._shared_set("vision", candidate)
                logger.info(
                    f"Ollama 비전 모델 자동 선택: {candidate} "
                    f"(기본값 {default}이(가) 설치돼 있지 않음)"
                )
                self._shared_set("vision_dead", None)
                return candidate

        # 기본을 돌려주되 «응답하지 않았다»를 남긴다 — 설정 카드가 «연결됨»으로 보이면 안 된다
        # (Codex 리뷰 2026-09-06: 은퇴·로그인 실패를 확인하고도 정상처럼 보였다).
        self._shared_set("vision_dead", default)
        logger.warning(
            "Ollama의 비전 모델이 모두 응답하지 않습니다. "
            "이미지 호출은 다음 프로바이더로 넘어갑니다(유료일 수 있습니다). "
            "→ 확인: ollama list 로 남은 모델을 보고 .env의 "
            "OLLAMA_VISION_MODEL 을 지정하세요."
        )
        return default

    async def _is_model_alive(self, model: str) -> bool:
        """이 모델이 실제로 응답하는지 확인한다.

        입력: model — 확인할 모델 이름. 출력: 부를 수 있으면 True.

        왜 목록만 믿으면 안 되는가:
            `/api/tags`는 **은퇴한 클라우드 모델도 그대로 올려 둔다.**
            실측(2026-07-26): `qwen3-vl:235b-cloud`가 목록에 있는데 부르면
            HTTP 410 — "qwen3-vl:235b was retired at 2026-06-16".

            그 모델이 자동 선택되면 쪽마다 실패하고 라우터가 조용히 다음
            프로바이더(유료 API)로 넘어간다. 무료로 도는 줄 알았는데 요금이
            나가던 D-056의 사고가 그대로 재현된다. 그래서 고르기 전에
            **한 번 불러 본다.**

            비용은 토큰 1개짜리 호출 한 번뿐이고, 결과는 캐시되므로
            (`_vision_model_cache`) 배치 전체에서 한 번만 일어난다.
        """
        try:
            async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
                resp = await client.post(
                    f"{self._url}/api/generate",
                    json={
                        "model": model,
                        "prompt": "hi",
                        "stream": False,
                        "options": {"num_predict": 1},
                    },
                )
        except (httpx.HTTPError, OSError) as e:
            logger.warning(f"Ollama 모델 확인 실패: {model} — {e}")
            return False

        if resp.status_code == 200:
            return True

        # 은퇴 안내는 본문에 사유가 들어 있다. 그대로 남겨야 원인을 알 수 있다.
        detail = (resp.text or "")[:200]
        logger.warning(
            f"Ollama 비전 모델 {model} 을(를) 쓸 수 없습니다 (HTTP {resp.status_code}): {detail}"
        )
        return False

    async def is_available(self) -> bool:
        """Ollama 서버가 실행 중인지 확인.

        기본 주소(127.0.0.1)로 안 닿고 사용자가 주소를 정해 두지 않았으면 [::1]도 본다 —
        OLLAMA_HOST=localhost로 띄운 Windows에서 IPv6에만 붙는 경우가 있다(2026-09-05 보고).
        찾은 주소는 프로세스 전체가 기억한다.
        """
        default = "http://127.0.0.1:11434"
        configured = self.config.get("ollama_url", default).replace(
            "://localhost:", "://127.0.0.1:"
        )
        # 사용자가 «다른» 주소를 적어 둔 경우에만 그 주소 하나만 본다. .env.example대로
        # localhost:11434를 적어 둔 것은 기본과 같으므로 [::1] 폴백을 막지 않는다(리뷰 지적).
        explicit_other = self.config.is_set("ollama_url") and configured.rstrip("/") != default
        candidates = [self._url]
        if not explicit_other:
            for c in (default, "http://[::1]:11434"):
                if c not in candidates:
                    candidates.append(c)  # override가 [::1]이어도 127.0.0.1로 돌아온 서버를 본다
        for url in candidates:
            try:
                async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
                    resp = await client.get(f"{url}/api/tags")
                if resp.status_code == 200:
                    if url != self._url:
                        OllamaProvider._url_override = None if url == default else url
                        logger.info(f"Ollama를 {url}에서 찾았습니다")
                    return True
            except (httpx.ConnectError, httpx.TimeoutException, OSError):
                continue
        # 기억해 둔 주소로도 안 닿으면 잊는다 — 다음엔 기본 주소부터 다시 본다.
        OllamaProvider._url_override = None
        return False

    # 모델 목록 캐시. 세션 중에 모델이 바뀌는 일은 드물다.
    #
    # 왜 캐시하는가: 이 함수는 설치된 모델마다 /api/show 를 부른다.
    # 이 PC에서 11개 모델에 **4.2초**가 걸렸다(실측 2026-07-26).
    # /api/llm/status·/api/llm/models가 이것을 부르고, 그 둘은 패널을 열 때마다
    # 불린다. 캐시가 없으면 화면 전환마다 그 시간을 다시 낸다.
    _models_cache: list[dict] | None = None
    # 모델 목록·비전 모델 선택은 **프로세스 전체가 공유**한다(주소별). 인스턴스에만 두면
    # 키를 저장할 때마다 라우터가 새로 만들어져 캐시가 날아가고, 설정 화면이 열릴 때마다
    # 모델마다 /api/show + 살아 있는지 호출로 10초를 다시 낸다(2026-09-05 실측 9.9초).
    _SHARED: dict[str, dict] = {}
    _SHARED_TTL = 600.0

    def _shared(self) -> dict:
        return self._SHARED.setdefault(self._url, {})

    def _shared_get(self, key: str):
        import time as _t

        hit = self._shared().get(key)
        if hit and _t.time() - hit[0] < self._SHARED_TTL:
            return hit[1]
        return None

    def _shared_set(self, key: str, value) -> None:
        import time as _t

        self._shared()[key] = (_t.time(), value)

    async def list_models(self, *, force: bool = False) -> list[dict]:
        """설치된 모델 목록 조회. GUI 드롭다운에서 사용.

        입력: force — 캐시를 무시하고 다시 조회한다(모델을 새로 받은 뒤 등).

        비전 지원 판별:
            Ollama /api/show 의 capabilities 배열에 "vision"이 있으면 비전 모델.
            이전에는 모델 이름의 키워드("vl", "vision", "llava")로 판별했으나,
            gemma4 등 이름에 키워드가 없는 멀티모달 모델을 놓치는 문제가 있었다.
            /api/show는 GGUF 메타데이터에서 vision.block_count를 확인하므로
            모델 이름과 무관하게 정확히 판별한다.
        """
        if not force:
            shared = self._shared_get("models")
            if shared is not None:
                return shared

        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            resp = await client.get(f"{self._url}/api/tags")
            data = resp.json()

        import asyncio as _aio

        entries = data.get("models", [])
        # /api/show를 모델마다 차례로 부르면 모델 수 × 왕복이다 — 동시에 묻는다.
        flags = await _aio.gather(
            *(self._check_vision_capability(m.get("name", "")) for m in entries),
            return_exceptions=True,
        )
        models = [
            {
                "name": m.get("name", ""),
                "size": m.get("size", "N/A"),
                "vision": bool(flag) if not isinstance(flag, Exception) else False,
            }
            for m, flag in zip(entries, flags)
        ]
        self._models_cache = models
        self._shared_set("models", models)
        return models

    async def _check_vision_capability(self, model_name: str) -> bool:
        """개별 모델의 비전 지원 여부를 /api/show로 확인한다.

        왜 /api/show를 쓰는가:
            /api/tags는 모델 목록만 반환하고 capability 정보가 없다.
            /api/show는 capabilities 배열(["completion","vision"] 등)을 반환하며,
            이 배열에 "vision"이 있으면 비전 프로젝터가 로드된 모델이다.
            capabilities가 없는 구버전 Ollama에서는 모델 이름 키워드로 폴백한다.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
                resp = await client.post(
                    f"{self._url}/api/show",
                    json={"name": model_name},
                )
                if resp.status_code != 200:
                    # 실패 시 이름 기반 폴백
                    return self._name_based_vision_check(model_name)
                info = resp.json()

            # 1순위: capabilities 배열 (Ollama PR #10066 이후)
            caps = info.get("capabilities", [])
            if caps:
                return "vision" in caps

            # 2순위: details.families에 clip 계열이 있으면 비전
            families = info.get("details", {}).get("families", [])
            if any(f in ("clip", "mllama") for f in families):
                return True

            # 3순위: model_info에 vision.* 키가 있으면 비전
            model_info = info.get("model_info", {})
            if any(k.startswith("vision.") for k in model_info):
                return True

            # 최후 폴백: 이름 키워드 (구버전 Ollama 호환)
            return self._name_based_vision_check(model_name)

        except (httpx.ConnectError, httpx.TimeoutException, Exception):
            return self._name_based_vision_check(model_name)

    @staticmethod
    def _name_based_vision_check(model_name: str) -> bool:
        """모델 이름으로 비전 지원을 추정한다 (폴백용).

        /api/show를 사용할 수 없을 때만 호출된다.
        gemma4 등 이름에 키워드가 없는 모델은 놓칠 수 있으므로,
        가능한 한 /api/show 경로를 우선 사용해야 한다.
        """
        name_lower = model_name.lower()
        return any(kw in name_lower for kw in ["vl", "vision", "llava", "gemma4", "pixtral"])

    async def call(
        self,
        prompt,
        *,
        system=None,
        response_format="text",
        model=None,
        max_tokens=4096,
        purpose="text",
        **kwargs,
    ) -> LlmResponse:
        """Ollama API로 텍스트 생성.

        purpose: 용도 힌트 ("text", "translation", "json")
                 → 용도별 기본 모델 자동 선택

        reasoning 모델(qwen3.5:*, deepseek-v3.2:*, kimi-k2-thinking:* 등) 처리:
            Ollama는 reasoning 모델의 사고 흐름을 `thinking` 필드로 분리 반환한다.
            기본값으로 `think=False`를 보내 직접 답변을 유도한다.
            이렇게 해야 num_predict 토큰이 전부 thinking에 소모되어
            response가 빈 문자열로 돌아오는 사고를 막을 수 있다.
            호출자가 `think=True`를 넘기면 reasoning을 활성화하고,
            response가 비어 있으면 thinking을 폴백 텍스트로 사용한다.
        """
        selected_model = model or self.DEFAULT_MODELS.get(purpose, self.DEFAULT_MODELS["text"])

        payload = {
            "model": selected_model,
            "prompt": prompt,
            "stream": False,
            # num_predict: Ollama의 최대 출력 토큰 설정.
            # 이 값이 없으면 모델 기본값(128~256)이 적용되어
            # 표점·주석 등 긴 JSON 응답이 중간에 잘린다.
            "options": {"num_predict": max_tokens},
        }
        # think 파라미터는 호출자가 명시했을 때만 전달한다.
        # 왜 기본값을 보내지 않는가:
        #   일부 클라우드 프록시 reasoning 모델(qwen3.5:397b-cloud 등)은
        #   `think=False`를 받으면 thinking은 억제하지만 response도 비워
        #   반환하여 완전 무응답이 된다. 모델 기본 동작(think ON)을
        #   유지하고, response가 비면 thinking을 폴백으로 쓰는 편이 안전.
        if "think" in kwargs and kwargs["think"] is not None:
            payload["think"] = bool(kwargs["think"])
        if system:
            payload["system"] = system
        if response_format == "json":
            payload["format"] = "json"

        t0 = time.monotonic()
        data = await self._generate(payload)
        # D-118: JSON을 강제하고 사고를 껐는데 답이 JSON이 아니다 — 사고를 끌 수 없는 모델은
        # 추론을 본문에 써 내려간다(glm-5.3:cloud 실측 2026-09-07: think=False면 영문 추론이
        # response로, think=True면 추론은 thinking으로 빠지고 response에 JSON이 온다).
        # 모델 이름을 코드에 적지 않고
        # 행동으로 판단해 사고를 켜 **한 번만** 더 부른다. 답은 여전히 JSON만이다.
        if (
            response_format == "json"
            and payload.get("think") is False
            and not _looks_like_json(data.get("response", ""))
        ):
            logger.warning(
                "Ollama %s: 사고를 끈 JSON 호출의 답이 JSON이 아니라 사고를 켜 다시 부릅니다"
                " (D-118)",
                selected_model,
            )
            payload["think"] = True
            data = await self._generate(payload)
        elapsed = time.monotonic() - t0

        # reasoning 모델: response가 비어 있으면 thinking을 폴백으로 사용.
        # think=True 모드에서 num_predict가 사고에 모두 소모됐을 때의 방어책.
        text = data.get("response", "") or data.get("thinking", "")

        return LlmResponse(
            text=text,
            provider=self.provider_id,
            model=selected_model,
            tokens_in=data.get("prompt_eval_count"),
            tokens_out=data.get("eval_count"),
            cost_usd=0.0,
            elapsed_sec=round(elapsed, 2),
            raw=data,
        )

    async def _generate(self, payload: dict) -> dict:
        """/api/generate 한 번. HTTP·모델 오류는 LlmProviderError로.

        클라우드 프록시 모델(gemini-3-flash-preview:cloud 등)은 네트워크 지연이 추가되므로
        타임아웃을 넉넉히 300초로 둔다.
        """
        async with httpx.AsyncClient(timeout=300.0, trust_env=False) as client:
            resp = await client.post(f"{self._url}/api/generate", json=payload)
            if resp.status_code != 200:
                raise LlmProviderError(f"Ollama 응답 {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
        if data.get("error"):
            raise LlmProviderError(f"Ollama 에러: {data['error']}")
        return data

    async def call_stream(
        self,
        prompt,
        *,
        system=None,
        response_format="text",
        model=None,
        max_tokens=4096,
        purpose="text",
        progress_callback=None,
        **kwargs,
    ) -> LlmResponse:
        """Ollama 네이티브 스트리밍. NDJSON 청크를 읽으며 progress_callback 호출.

        왜 네이티브 스트리밍을 사용하는가:
            기본 heartbeat(2초 간격)보다 훨씬 세밀한 진행 표시가 가능하다.
            토큰이 생성될 때마다 경과 시간과 토큰 수를 실시간으로 전달한다.
            Ollama의 stream 모드는 NDJSON(줄 구분 JSON)으로 응답하며,
            각 줄은 {"response":"토큰","done":false} 형식이다.
        """
        import json as _json

        selected_model = model or self.DEFAULT_MODELS.get(purpose, self.DEFAULT_MODELS["text"])

        payload = {
            "model": selected_model,
            "prompt": prompt,
            "stream": True,
            "options": {"num_predict": max_tokens},
        }
        # think는 명시될 때만 전달 (call()과 동일한 정책).
        if "think" in kwargs and kwargs["think"] is not None:
            payload["think"] = bool(kwargs["think"])
        if system:
            payload["system"] = system
        if response_format == "json":
            payload["format"] = "json"

        t0 = time.monotonic()
        full_text = ""
        # reasoning 스트림이 활성화된 경우 chunk["thinking"]으로 들어오는
        # 사고 토큰을 별도로 모아둔다. 최종 response가 비어 있으면
        # thinking 전체를 폴백 텍스트로 사용한다.
        full_thinking = ""
        tokens_out = 0
        tokens_in = None
        last_report = t0

        async with httpx.AsyncClient(timeout=300.0, trust_env=False) as client:
            async with client.stream("POST", f"{self._url}/api/generate", json=payload) as resp:
                if resp.status_code != 200:
                    raise LlmProviderError(f"Ollama 스트리밍 응답 {resp.status_code}")

                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue

                    if chunk.get("error"):
                        raise LlmProviderError(f"Ollama 에러: {chunk['error']}")

                    token = chunk.get("response", "")
                    full_text += token
                    # reasoning 토큰은 response와 별도 필드로 도착한다.
                    thinking_token = chunk.get("thinking", "")
                    if thinking_token:
                        full_thinking += thinking_token
                    tokens_out += 1

                    # 1초마다 progress 콜백
                    now = time.monotonic()
                    if progress_callback and (now - last_report) >= 1.0:
                        last_report = now
                        progress_callback(
                            {
                                "type": "progress",
                                "elapsed_sec": round(now - t0, 1),
                                "tokens": tokens_out,
                                "provider": self.provider_id,
                            }
                        )

                    if chunk.get("done"):
                        tokens_in = chunk.get("prompt_eval_count")
                        tokens_out = chunk.get("eval_count", tokens_out)
                        break

        elapsed = time.monotonic() - t0

        # response가 비었는데 thinking만 차 있으면 thinking을 폴백으로.
        final_text = full_text or full_thinking

        return LlmResponse(
            text=final_text,
            provider=self.provider_id,
            model=selected_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=0.0,
            elapsed_sec=round(elapsed, 2),
            raw={"stream": True},
        )

    async def _post_generate(self, payload: dict, *, label: str = "Ollama") -> dict:
        """`/api/generate`에 페이로드를 보내고 JSON을 돌려준다.

        입력: payload — Ollama generate 요청 본문. label — 오류 메시지에 쓸 이름.
        출력: 응답 JSON(dict). HTTP 오류나 Ollama 오류 필드는 LlmProviderError로.

        왜 따로 뗐는가: 비전 경로의 페이로드 조립·잘림 판정을 서버 없이 시험하려면
        네트워크 한 줄이 바꿔 끼울 수 있는 자리에 있어야 한다. 이 환경에는
        Ollama 서버가 없고, 그 사정은 CI도 같다.
        """
        async with httpx.AsyncClient(timeout=300.0, trust_env=False) as client:
            resp = await client.post(f"{self._url}/api/generate", json=payload)
            if resp.status_code != 200:
                raise LlmProviderError(f"{label} 응답 {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
        if data.get("error"):
            raise LlmProviderError(f"{label} 에러: {data['error']}")
        return data

    async def call_with_image(
        self,
        prompt,
        image,
        *,
        image_mime="image/png",
        system=None,
        response_format="text",
        model=None,
        max_tokens=4096,
        **kwargs,
    ) -> LlmResponse:
        """Ollama 비전 모델로 이미지 분석.

        Ollama API는 images 필드에 base64 배열을 받는다.

        모델을 왜 자동으로 고르는가:
            DEFAULT_MODELS["vision"]에 적힌 모델이 **설치돼 있지 않으면**
            호출이 실패하고 라우터가 조용히 다음 프로바이더로 넘어간다.
            실제로 그 일이 있었다 — 기본값 `gemma4:e4b`가 없어서 Ollama가
            늘 탈락했고, 사용자는 무료 로컬로 도는 줄 알았는데 실제로는
            Gemini가 처리하고 있었다. 어느 모델이 쓰이는지 모르는 상태로
            유료 API가 소모되는 것이 가장 나쁘다.

            그래서 지정된 모델이 없으면 설치된 것 중에서 찾는다.
        """
        selected_model = model or await self._pick_vision_model()

        # 사고 예산은 답변 예산에 **더한다** (D-083).
        #
        # num_predict는 Ollama가 생성하는 토큰 전체의 상한이고, reasoning 모델은
        # 사고 토큰도 여기서 소모한다. 그래서 4096을 그대로 두고 think=True를
        # 켜면 사고가 상한을 다 쓰고 response가 빈다 — D-074가 본 현상이다.
        # 사고가 꺼져 있으면 budget은 0이라 예전과 같다.
        think, thinking_budget = thinking_options(kwargs)
        num_predict = max_tokens + thinking_budget

        payload = {
            "model": selected_model,
            "prompt": prompt,
            "images": [base64.b64encode(image).decode("ascii")],
            "stream": False,
            # max_tokens를 받아 놓고 쓰지 않고 있었다. 그래서 OCR 응답이 모델
            # 기본값에서 잘리고, 잘린 JSON은 파싱에 실패해 **원문 JSON 문자열이
            # 통째로** 텍스트 레이어에 박혔다. 그러면 줄 정보가 없어 한 덩어리로
            # 얹히므로 형광·드래그 위치도 전부 어긋난다
            # (실측 2026-08-12: y 위치가 73개 → 1개로 붕괴).
            "options": {"num_predict": num_predict},
        }
        # reasoning 비전 모델(qwen3-vl:235b-cloud 등)은 명시될 때만 think 전달.
        # gpt-oss 계열은 "low"/"medium"/"high" 문자열도 받으므로 str은 그대로 넘긴다.
        if think is not None:
            payload["think"] = think if isinstance(think, str) else bool(think)
        if system:
            payload["system"] = system
        # 답변을 구조로 제약한다 (D-083 원칙 2).
        #
        # 텍스트 경로 call()에는 있던 분기가 비전 경로에는 없었다. 사고를 켠
        # 모델이 추론 문장을 response에 흘리는 것을 막는 가장 확실한 방법은
        # format으로 답변을 스키마에 묶는 것이다. Ollama는 사고를 `thinking`
        # 필드에 따로 두므로 format은 response에만 걸린다.
        # 호출자가 json_schema(dict)를 주면 그것을, 없으면 "json"을 쓴다.
        if response_format == "json":
            payload["format"] = kwargs.get("json_schema") or "json"

        t0 = time.monotonic()
        data = await self._post_generate(payload, label="Ollama vision")
        elapsed = time.monotonic() - t0

        # 잘림 감지 (D-083 원칙 3).
        #
        # Gemini·OpenAI 프로바이더는 finish_reason으로 잘림을 예외로 올리는데
        # (D-033) Ollama만 done_reason을 읽지 않고 조용히 성공으로 돌려줬다.
        # 잘린 JSON은 파서가 «줄바꿈 분리»로 받아 한 덩어리 텍스트를 만든다 —
        # D-075가 본 «y 위치 42개 → 1개» 붕괴가 그 결과다. 실패로 드러내는
        # 편이 낫다. 메시지에 TRUNCATED_MARK를 넣어 호출자가 사다리를 탄다.
        if data.get("done_reason") == "length":
            raise LlmProviderError(
                f"Ollama vision 출력이 num_predict={num_predict}에서 잘렸습니다 "
                f"({TRUNCATED_MARK}; think={think!r}, thinking_budget={thinking_budget}). "
                "→ 사고를 끄거나 예산을 늘려 다시 시도합니다."
            )

        # reasoning 모델 폴백: response가 비면 thinking 사용.
        #
        # 단, 호출자가 allow_thinking_fallback=False를 주면 쓰지 않는다.
        # OCR이 그 경우다 — 사고문("The user wants me to extract text...")이
        # 그대로 PDF 텍스트 레이어로 구워지면, 문서는 멀쩡해 보이는데 검색은
        # 안 되고 복사하면 영어 사고문이 나온다. 무증상 오염이라 빈 결과보다
        # 나쁘다. 빈 결과는 «실패»로 드러나기라도 한다.
        # (실측 2026-08-12, qwen3.5:4b 1쪽: response 0자 / thinking 2,228자)
        text = data.get("response", "") or ""
        if not text.strip() and kwargs.get("allow_thinking_fallback", True):
            text = data.get("thinking", "") or ""
        # JSON을 요구했는데 비어 있으면 실패로 드러낸다 — 다른 세 프로바이더와 같다.
        # 조용히 빈 결과를 돌려주면 줄 0개짜리 L2가 «처리 완료»로 저장된다.
        if response_format == "json" and not text.strip():
            raise LlmProviderError(
                f"Ollama vision empty JSON output (done_reason={data.get('done_reason')!r}, "
                f"think={think!r}, num_predict={num_predict})"
            )

        return LlmResponse(
            text=text,
            provider=self.provider_id,
            model=selected_model,
            tokens_in=data.get("prompt_eval_count"),
            tokens_out=data.get("eval_count"),
            cost_usd=0.0,
            elapsed_sec=round(elapsed, 2),
            raw=data,
        )
