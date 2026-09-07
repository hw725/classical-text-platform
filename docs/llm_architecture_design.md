# LLM 호출 아키텍처 상세 설계

> Phase 10-2의 핵심 — 전체 프로젝트 공용 LLM 연동 기반
> 작성: 2026-02-15 · **2026-07-26 확인: 5단 폴백 순서는 `router.py`와 여전히 일치**
> (Ollama → OpenAI OAuth → Gemini → OpenAI → Anthropic).
> v1.2.0에서 더해진 것: 사용량을 화면에 표시(D-056), LLM Vision을 OCR 엔진으로 사용.
> v1.3.0에서 더해진 것: 목차 항목 구조화(D-089)와 표제 어휘 뽑기(D-095), 시작 표지 묻기(D-117 4단 —
> 통계가 못 찾은 책만, 표본 행 ≤ 80줄, 답은 정해진 종류로만 받고 코드가 세어 확인) —
> 셋 다 텍스트만 넘기고 JSON을 강제하며 사고는 끈다(D-083).

---

## 1. 호출 우선순위 (5단 폴백)

```
┌─────────────────────────────────────────────────────────────┐
│  classical-text-browser (Python/FastAPI)                    │
│                                                             │
│  src/llm/router.py  ← LLM 호출의 단일 진입점               │
│      │                                                      │
│      ├─ 1순위: Ollama (로컬 서버)                            │
│      │   localhost:11434/api/generate                       │
│      │   모델: 텍스트 gemma4:e4b · 비전 gemma4:cloud(D-114)  │
│      │   조건: Ollama 서버가 실행 중                         │
│      │   장점: 무료, 로컬 실행, 비전 포함, 오프라인 가능     │
│      │                                                      │
│      ├─ 2순위: OpenAI OAuth                                  │
│      │   조건: openai-oauth 프록시 실행 또는 자동 기동       │
│      │   장점: API 키 없이 무료, 비전 포함                   │
│      │                                                      │
│      ├─ 3순위: Gemini (Google AI)                            │
│      │   조건: GOOGLE_API_KEY 설정됨                        │
│      │   장점: 저렴, 비전 포함                              │
│      │                                                      │
│      ├─ 4순위: OpenAI                                        │
│      │   조건: OPENAI_API_KEY 설정됨                        │
│      │   장점: 중간 비용, 비전 포함                         │
│      │                                                      │
│      └─ 5순위: Anthropic (Claude API)                        │
│          조건: ANTHROPIC_API_KEY 설정됨                      │
│          장점: 최후 폴백                                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 왜 이 순서인가

| 순위 | 방식 | 비용 | 이미지 분석 | 의존성 | 오프라인 |
|------|------|------|-------------|--------|----------|
| 1 | Ollama (텍스트 gemma4:e4b · 비전 gemma4:cloud) | 무료 / 클라우드는 구독 한도 | ✅ | Ollama 실행(+클라우드는 로그인) | ✅ |
| 2 | OpenAI OAuth | 무료 | ✅ | openai-oauth 프록시 | ✗ |
| 3 | Gemini | 저렴 | ✅ | API 키 | ✗ |
| 4 | OpenAI | 중간 | ✅ | API 키 | ✗ |
| 5 | Anthropic | 유료 | ✅ | API 키 | ✗ |

- 1순위는 Ollama — 텍스트는 로컬 gemma4:e4b(무료, 오프라인), 비전 기본은 gemma4:cloud(D-114, 2026-09-06 — 로그인 필요, 화면 「모델 받기」에서 로컬 후보로 바꿀 수 있다)
- 2순위는 ChatGPT 계정 OAuth 프록시 (API 키 없이 무료, 온라인)
- 3~5순위는 유료 API (비용 순: Gemini < OpenAI < Anthropic)

---

## 2. 디렉토리 구조

```
src/
  llm/
    __init__.py
    router.py           ← 단일 진입점: call_llm(), call_llm_with_image()
    providers/
      __init__.py
      base.py               ← BaseLlmProvider 추상 클래스
      ollama.py             ← 1순위: Ollama REST API (gemma4:e4b)
      openai_oauth_provider.py ← 2순위: OpenAI OAuth 프록시
      gemini_provider.py    ← 3순위: Gemini API
      openai_provider.py    ← 4순위: OpenAI API
      anthropic_provider.py ← 5순위: Claude API
    prompts/
      layout_analysis.yaml       ← L3 레이아웃 분석
      punctuation.yaml           ← L5 표점
      translation.yaml           ← L6 번역
      annotation.yaml            ← L7 주석
      annotation_dict_stage1.yaml ← L7 사전형 1단계
      annotation_dict_stage2.yaml ← L7 사전형 2단계
      annotation_dict_stage3.yaml ← L7 사전형 3단계
    config.py           ← 설정: API 키, 모델 선택, 우선순위
    usage_tracker.py    ← 비용 추적
    draft.py            ← LlmDraft 모델 (Draft → Review → Commit)
```

---

## 3. 핵심 인터페이스

### 3.1 BaseLlmProvider

```python
# src/llm/providers/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class LlmResponse:
    """LLM 응답 통합 모델.
    
    모든 provider가 이 형식으로 반환한다.
    어떤 provider를 썼든 호출자는 동일한 형식을 받는다.
    """
    text: str                    # 응답 텍스트
    provider: str                # "ollama", "openai_oauth", "gemini", "openai", "anthropic" 등
    model: str                   # 실제 사용된 모델명
    tokens_in: int | None        # 입력 토큰 (추정 가능할 때)
    tokens_out: int | None       # 출력 토큰
    cost_usd: float | None       # 추정 비용 (무료면 0.0)
    raw: dict | None             # provider별 원본 응답 (디버깅용)


class BaseLlmProvider(ABC):
    """LLM provider 추상 클래스.
    
    각 provider는 이것을 구현한다.
    router.py가 우선순위에 따라 순서대로 시도.
    """
    
    provider_id: str        # "ollama", "openai_oauth", "gemini", "openai", "anthropic"
    display_name: str       # "Ollama", "Gemini", ...
    supports_image: bool    # 이미지 입력 가능 여부
    
    @abstractmethod
    async def is_available(self) -> bool:
        """이 provider가 현재 사용 가능한지 확인.
        
        - ollama: localhost:11434 헬스체크
        - gemini/openai/anthropic: API 키 존재 확인
        """
        ...
    
    @abstractmethod
    async def call(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_format: str = "text",  # "text" | "json"
        model: str | None = None,       # 모델 오버라이드
        max_tokens: int = 4096,
    ) -> LlmResponse:
        """텍스트 프롬프트로 LLM 호출."""
        ...
    
    @abstractmethod
    async def call_with_image(
        self,
        prompt: str,
        image: bytes,
        *,
        image_mime: str = "image/png",
        system: str | None = None,
        response_format: str = "text",
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> LlmResponse:
        """이미지 + 텍스트 프롬프트로 LLM 호출.
        
        레이아웃 분석(10-2), OCR 보조 등에서 사용.
        supports_image=False인 provider에서 호출하면 NotImplementedError.
        """
        ...
```

### 3.2 Router (단일 진입점)

```python
# src/llm/router.py

class LlmRouter:
    """LLM 호출의 단일 진입점.
    
    우선순위에 따라 provider를 순서대로 시도한다.
    모든 코드에서 LLM이 필요하면 이것만 호출하면 된다.
    
    사용 예시:
        router = LlmRouter(config)
        
        # 텍스트 호출
        response = await router.call("이 문장을 번역해줘")
        
        # 이미지 호출 (레이아웃 분석)
        response = await router.call_with_image(
            "이 페이지의 영역을 분석해줘",
            image_bytes
        )
    """
    
    def __init__(self, config: LlmConfig):
        # 우선순위 순서대로 provider 목록 생성
        self.providers = [
            OllamaProvider(config),         # 1순위 (gemma4:e4b)
            OpenAiOAuthProvider(config),    # 2순위
            GeminiProvider(config),         # 3순위
            OpenAiProvider(config),         # 4순위
            AnthropicProvider(config),      # 5순위
        ]
        self.usage_tracker = UsageTracker(config)
    
    async def call(self, prompt, *, system=None, response_format="text",
                   require_image=False,
                   force_provider=None, force_model=None,
                   purpose="text", **kwargs) -> LlmResponse:
        """LLM 호출 — 단일 진입점.
        
        기본 동작: 우선순위에 따라 provider를 순서대로 시도.
        
        모델 선택 옵션 (품질 테스트·비교용):
          force_provider: 특정 provider만 사용
            예: "ollama", "openai_oauth", "gemini", "openai", "anthropic"
          force_model: 특정 모델 지정 (force_provider와 함께 사용)
            예: "gemma4:e4b", "claude-sonnet-4-20250514"
        
        사용 예시:
          # 기본: 폴백 순서대로
          await router.call("번역해줘")
          
          # Ollama의 특정 모델로 강제 지정
          await router.call("번역해줘",
              force_provider="ollama",
              force_model="glm-5:cloud")
          
          # Claude로 강제 지정 (품질 비교용)
          await router.call("번역해줘",
              force_provider="anthropic")
        """
        # ── 명시적 모델 선택 모드 ──
        if force_provider:
            provider = self._get_provider(force_provider)
            if not provider:
                raise LlmProviderError(
                    f"provider '{force_provider}'를 찾을 수 없습니다.\n"
                    f"사용 가능: {[p.provider_id for p in self.providers]}"
                )
            if not await provider.is_available():
                raise LlmProviderError(
                    f"provider '{force_provider}'가 현재 사용 불가합니다."
                )
            if require_image and not provider.supports_image:
                raise LlmProviderError(
                    f"provider '{force_provider}'는 이미지를 지원하지 않습니다."
                )
            
            response = await provider.call(
                prompt, system=system,
                response_format=response_format,
                model=force_model, **kwargs
            )
            self.usage_tracker.log(response, purpose=purpose)
            return response
        
        # ── 자동 폴백 모드 ──
        errors = []
        
        for provider in self.providers:
            if require_image and not provider.supports_image:
                continue
            
            try:
                if not await provider.is_available():
                    continue
                
                response = await provider.call(
                    prompt, system=system,
                    response_format=response_format,
                    purpose=purpose, **kwargs
                )
                
                self.usage_tracker.log(response, purpose=purpose)
                return response
                
            except Exception as e:
                errors.append(f"{provider.provider_id}: {e}")
                continue
        
        raise LlmUnavailableError(
            "사용 가능한 LLM provider가 없습니다.\n"
            "다음 중 하나를 확인하세요:\n"
            "1. Ollama 실행 (ollama serve)\n"
            "2. OpenAI OAuth 프록시 실행 (start_server.bat 또는 npx.cmd -y openai-oauth)\n"
            "3. API 키 설정 (.env에 GOOGLE_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY)\n\n"
            f"시도한 provider별 오류:\n" + "\n".join(errors)
        )
    
    async def call_with_image(self, prompt, image, **kwargs) -> LlmResponse:
        """이미지 분석 호출. supports_image인 provider만 시도.
        
        force_provider, force_model도 지원 — kwargs로 전달.
        """
        return await self.call(
            prompt, require_image=True, _image=image, **kwargs
        )
    
    async def compare(
        self,
        prompt: str,
        *,
        targets: list[str | tuple[str, str]] | None = None,
        image: bytes | None = None,
        system: str | None = None,
        purpose: str = "comparison",
        **kwargs,
    ) -> list[LlmResponse | Exception]:
        """여러 모델에 같은 입력을 보내서 결과를 비교.
        
        품질 테스트용. 결과를 나란히 보여줘서 어떤 모델이 나은지 판단.
        
        targets: 비교할 provider(+모델) 목록
          - 문자열: provider_id → 기본 모델 사용
          - 튜플: (provider_id, model) → 특정 모델 지정
          - None → 현재 사용 가능한 모든 provider
        
        사용 예시:
          # 가용한 전체 모델 비교
          results = await router.compare("이 페이지 분석해줘", image=img)
          
          # 특정 모델만 비교
          results = await router.compare(
              "이 문장 번역해줘",
              targets=[
                  "ollama",
                  ("gemini", "gemini-2.5-flash"),
                  "anthropic",
              ]
          )
          
        반환: LlmResponse 리스트 (실패한 것은 Exception 객체)
        """
        import asyncio
        
        # 비교 대상 결정
        if targets is None:
            # 사용 가능한 모든 provider
            pairs = []
            for p in self.providers:
                if image and not p.supports_image:
                    continue
                if await p.is_available():
                    pairs.append((p.provider_id, None))
        else:
            pairs = []
            for t in targets:
                if isinstance(t, str):
                    pairs.append((t, None))
                else:
                    pairs.append((t[0], t[1]))
        
        # 병렬 호출
        async def _call_one(provider_id, model):
            try:
                return await self.call(
                    prompt,
                    system=system,
                    force_provider=provider_id,
                    force_model=model,
                    purpose=purpose,
                    **kwargs,
                )
            except Exception as e:
                return e
        
        tasks = [_call_one(pid, model) for pid, model in pairs]
        results = await asyncio.gather(*tasks)
        
        # 비교 기록 (usage_tracker에 comparison 로그)
        self.usage_tracker.log_comparison(
            purpose=purpose,
            targets=pairs,
            results=results,
        )
        
        return results
    
    async def get_available_models(self) -> list[dict]:
        """현재 사용 가능한 모든 provider와 모델 목록.
        
        GUI의 모델 선택 드롭다운에서 사용.
        
        반환 예시:
        [
            {"provider": "ollama", "model": "gemma4:e4b", "available": True,
             "display": "Ollama — gemma4:e4b (멀티모달)", "cost": "무료", "vision": True},
            {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "available": False,
             "display": "Claude Sonnet 4", "cost": "유료", "reason": "API 키 미설정"},
        ]
        """
        models = []
        
        for provider in self.providers:
            available = await provider.is_available()
            
            if provider.provider_id == "ollama" and available:
                # Ollama: 실제 설치된 모델 목록 조회
                ollama_models = await provider.list_models()
                for m in ollama_models:
                    models.append({
                        "provider": "ollama",
                        "model": m["name"],
                        "available": True,
                        "display": f"Ollama — {m['name']}",
                        "cost": "무료",
                        "vision": "vl" in m["name"].lower(),
                    })
            else:
                models.append({
                    "provider": provider.provider_id,
                    "model": getattr(provider, "DEFAULT_MODEL", "(자동)"),
                    "available": available,
                    "display": provider.display_name,
                    "cost": "무료" if provider.provider_id == "ollama" else "유료",
                    "vision": provider.supports_image,
                })
        
        return models
    
    def _get_provider(self, provider_id: str) -> BaseLlmProvider | None:
        """provider_id로 provider 객체를 찾는다."""
        for p in self.providers:
            if p.provider_id == provider_id:
                return p
        return None
```

---

## 4. Provider별 구현 상세

### 4.1 Ollama (1순위)

```python
# src/llm/providers/ollama.py

class OllamaProvider(BaseLlmProvider):
    """Ollama 로컬 서버(localhost:11434)를 통한 LLM 호출.
    
    기본 모델: gemma4:e4b (Google Gemma 4, 멀티모달)
    
    호출 흐름:
      Python → HTTP POST localhost:11434/api/generate
            → Ollama → 로컬 모�� 실행
            → 결과 반환
    """
    
    provider_id = "ollama"
    display_name = "Ollama"
    supports_image = True  # gemma4:e4b 멀티모달
    
    OLLAMA_URL = "http://localhost:11434"
    
    # 용도별 기본 모델 — 텍스트 gemma4:e4b, 비전은 클라우드 gemma4:cloud (D-114, 2026-09-06)
    DEFAULT_MODELS = {
        "text": "gemma4:e4b",
        "vision": "gemma4:cloud",  # llm/ollama_catalog.DEFAULT_VISION_MODEL
        "translation": "gemma4:e4b",
        "json": "gemma4:e4b",
        "punctuation": "gemma4:e4b",
        "annotation": "gemma4:e4b",
    }
    
    async def is_available(self) -> bool:
        """Ollama 서버가 실행 중인지 확인."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{self.OLLAMA_URL}/api/tags")
                return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException):
            return False
    
    async def call(self, prompt, *, system=None, response_format="text",
                   model=None, purpose="text", **kwargs) -> LlmResponse:
        """Ollama API로 텍스트 생성.
        
        purpose: 용도 힌트 ("text", "translation", "json")
                 → 용도별 기본 모델 자동 선택
        """
        selected_model = model or self.DEFAULT_MODELS.get(purpose, "kimi-k2.5:cloud")
        
        payload = {
            "model": selected_model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system
        if response_format == "json":
            payload["format"] = "json"
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.OLLAMA_URL}/api/generate",
                json=payload,
            )
            data = resp.json()
        
        return LlmResponse(
            text=data.get("response", ""),
            provider="ollama",
            model=selected_model,
            tokens_in=data.get("prompt_eval_count"),
            tokens_out=data.get("eval_count"),
            cost_usd=0.0,  # 클라우드 모델도 Ollama 프록시 비용은 별도 추적
            raw=data,
        )
    
    async def call_with_image(self, prompt, image, *,
                              image_mime="image/png", model=None,
                              **kwargs) -> LlmResponse:
        """Ollama 비전 모델로 이미지 분석.
        
        gemma4:cloud가 기본 비전 모델(D-114). qwen3-vl:235b-cloud는 2026-06-16 은퇴.
        Ollama API는 images 필드에 base64 배열을 받는다.
        """
        import base64
        
        selected_model = model or self.DEFAULT_MODELS["vision"]
        
        payload = {
            "model": selected_model,
            "prompt": prompt,
            "images": [base64.b64encode(image).decode("ascii")],
            "stream": False,
        }
        
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{self.OLLAMA_URL}/api/generate",
                json=payload,
            )
            data = resp.json()
        
        return LlmResponse(
            text=data.get("response", ""),
            provider="ollama",
            model=selected_model,
            tokens_in=data.get("prompt_eval_count"),
            tokens_out=data.get("eval_count"),
            cost_usd=0.0,
            raw=data,
        )
```

### 4.2 OpenAI OAuth (2순위)

`OpenAiOAuthProvider`는 `OpenAiProvider`를 상속하고 OpenAI 호환 `base_url`만
openai-oauth 프록시로 바꾼다. `start_server.bat`는 `npx.cmd -y openai-oauth`를
별도 창에서 자동 실행하고, 프록시가 올라온 `10531~10540` 포트를 감지해
`OPENAI_OAUTH_BASE_URL`을 본체 서버에 전달한다.

프록시가 실행 중이 아니면 이 provider는 즉시 스킵되고 Gemini로 폴백한다.

### 4.5 직접 API (5순위) — Anthropic 예시

```python
# src/llm/providers/anthropic.py

class AnthropicProvider(BaseLlmProvider):
    """Anthropic Claude API 직접 호출.
    
    최후 수단. 유료지만 가장 안정적.
    고전 한문 분석에는 Claude가 가장 정확할 수 있다.
    """
    
    provider_id = "anthropic"
    display_name = "Claude (Anthropic)"
    supports_image = True
    
    DEFAULT_MODEL = "claude-sonnet-4-20250514"
    
    async def is_available(self) -> bool:
        """ANTHROPIC_API_KEY가 설정되어 있는지 확인."""
        return bool(self.config.get_api_key("anthropic"))
    
    async def call(self, prompt, *, system=None, model=None,
                   max_tokens=4096, **kwargs) -> LlmResponse:
        import anthropic
        
        client = anthropic.AsyncAnthropic(
            api_key=self.config.get_api_key("anthropic")
        )
        
        messages = [{"role": "user", "content": prompt}]
        
        response = await client.messages.create(
            model=model or self.DEFAULT_MODEL,
            max_tokens=max_tokens,
            system=system or "",
            messages=messages,
        )
        
        text = response.content[0].text
        
        return LlmResponse(
            text=text,
            provider="anthropic",
            model=response.model,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            cost_usd=self._estimate_cost(response),
            raw={"id": response.id},
        )
    
    # call_with_image도 유사하게 구현 (content block에 image 추가)
```

---

## 5. 설정 관리

### 5.1 config.py

```python
# src/llm/config.py

class LlmConfig:
    """LLM 설정 관리.
    
    설정 우선순위:
      1. 환경변수 (.env)
      2. 서고 설정 파일
      3. 기본값
    """
    
    def get_api_key(self, provider: str) -> str | None:
        """API 키 조회. 환경변수 → 설정 파일 → None."""
        env_keys = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "gemini": "GOOGLE_API_KEY",
        }
        env_name = env_keys.get(provider)
        if env_name:
            return os.environ.get(env_name) or self._env.get(env_name)
        return None
    
    DEFAULTS = {
        "ollama_url": "http://localhost:11434",
        "monthly_budget_usd": 10.0,
    }
```

### 5.2 .env 예시

```env
# LLM 설정 — classical-text-browser/.env
# 이 파일은 .gitignore에 포함되어야 한다!

# Ollama (1순위 — 무료, 로컬 gemma4:e4b)
# Ollama가 localhost:11434에서 실행 중이면 자동 감지
# 모델 설치: ollama pull gemma4:cloud  (또는 앱 설정 ▸ LLM 연결 ▸ Ollama 「모델 받기」에서 고른다)
# OLLAMA_URL=http://localhost:11434

# OpenAI OAuth (2순위 — start_server.bat 자동 기동, 직접 고정할 때만 설정)
# OPENAI_OAUTH_BASE_URL=http://127.0.0.1:10532/v1

# 유료 API (3~5순위)
# GOOGLE_API_KEY=AIza...
# OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-ant-...

# 예산
# MONTHLY_BUDGET_USD=10.0
```

---

## 6. 용도별 모델 선택 전략

```
┌────────────────────────┬──────────────────────────────────┐
│ 용도                    │ 모델 선택                        │
├────────────────────────┼──────────────────────────────────┤
│ 레이아웃 분석 (10-2)    │ 비전 필수:                        │
│  이미지 → LayoutBlock   │  1. Ollama gemma4:e4b (멀티모달) │
│                        │  2. OpenAI OAuth                 │
│                        │  3. Gemini gemini-2.5-flash      │
│                        │  4. OpenAI API                   │
│                        │  5. Claude claude-sonnet-4       │
├────────────────────────┼──────────────────────────────────┤
│ 번역 (11-2)            │ 텍스트:                           │
│  한문 → 현대한국어       │  1. Ollama gemma4:e4b            │
│                        │  2. OpenAI OAuth                 │
│                        │  3. Gemini                       │
│                        │  4. OpenAI API                   │
│                        │  5. Claude (한문에 강함)           │
├────────────────────────┼──────────────────────────────────┤
│ 주석 자동 생성 (11-3)   │ 텍스트:                           │
│  인물/지명/전거 식별     │  1. Ollama gemma4:e4b            │
│                        │  2. OpenAI OAuth                 │
│                        │  3. Gemini                       │
│                        │  4. Claude                       │
├────────────────────────┼──────────────────────────────────┤
│ JSON 구조화 출력        │ JSON 모드 지원 모델:              │
│  프롬프트 → JSON        │  1. Ollama gemma4:e4b (json)     │
│                        │  2. OpenAI OAuth                 │
│                        │  3. Gemini (JSON mode)           │
│                        │  4. Claude (JSON mode)           │
├────────────────────────┼──────────────────────────────────┤
│ OCR 보조 (10-1)        │ 비전 필수:                        │
│  저품질 이미지 판독     │  1. Ollama gemma4:e4b (멀티모달) │
│                        │  2. OpenAI OAuth                 │
│                        │  3. Gemini Vision                │
│                        │  4. Claude Vision                │
└────────────────────────┴──────────────────────────────────┘
```

---

## 8. 비용 추적

```python
# src/llm/usage_tracker.py

class UsageTracker:
    """LLM 사용량 추적.
    
    서고별 llm_usage_log.jsonl에 매 호출 기록.
    무료 provider(Ollama)도 기록하여 사용 패턴 분석.
    """
    
    def log(self, response: LlmResponse, purpose: str = ""):
        """호출 기록 추가."""
        entry = {
            "ts": datetime.now().isoformat(),
            "provider": response.provider,
            "model": response.model,
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
            "cost_usd": response.cost_usd or 0.0,
            "purpose": purpose,  # "layout_analysis", "translation", ...
        }
        # jsonl 파일에 append
    
    def log_comparison(self, purpose, targets, results):
        """비교 모드 호출 기록.
        
        어떤 모델들을 비교했는지, 각 결과의 길이·토큰 등을 기록.
        나중에 "어떤 모델이 번역에 가장 좋았나" 분석에 사용.
        """
        entry = {
            "ts": datetime.now().isoformat(),
            "type": "comparison",
            "purpose": purpose,
            "targets": [
                {"provider": pid, "model": model}
                for pid, model in targets
            ],
            "results": [
                {
                    "provider": r.provider if isinstance(r, LlmResponse) else None,
                    "model": r.model if isinstance(r, LlmResponse) else None,
                    "text_length": len(r.text) if isinstance(r, LlmResponse) else 0,
                    "error": str(r) if isinstance(r, Exception) else None,
                }
                for r in results
            ],
        }
        # jsonl 파일에 append
    
    def get_monthly_summary(self) -> dict:
        """이번 달 사용량 요약."""
        return {
            "total_calls": 42,
            "total_cost_usd": 1.23,
            "by_provider": {
                "ollama": {"calls": 38, "cost": 0.0},
                "anthropic": {"calls": 4, "cost": 1.23},
            },
            "by_purpose": {
                "layout_analysis": 15,
                "translation": 20,
                "annotation": 7,
            },
            "budget_remaining_usd": 8.77,
        }
```

---

## 9. 모델 선택 및 비교 GUI

### 9.1 모델 선택 드롭다운

모든 LLM 기능(레이아웃 분석, 번역, 주석)의 UI에 모델 선택 옵션을 둔다.

```
┌─────────────────────────────────────────────┐
│  [AI 분석]                                   │
│                                              │
│  모델: [🔄 자동 (폴백순서)            ▼]     │
│        ┌──────────────────────────────┐      │
│        │ 🔄 자동 (폴백순서)            │ ← 기본값     │
│        │ ─────────────────────────── │      │
│        │ 🟢 Ollama: gemma4:e4b       │ ← 무료     │
│        │ ⚫ OpenAI OAuth: gpt-5.4-mini │ ← 무료, 프록시 미실행 │
│        │ ⚫ Gemini: gemini-2.5-flash │ ← 유료, 키 미설정     │
│        │ ⚫ OpenAI: gpt-5-mini       │ ← 유료, 키 미설정     │
│        │ ⚫ Claude sonnet-4          │ ← 유료, 키 미설정     │
│        │ ─────────────────────────── │      │
│        │ 🔬 비교 모드                  │ ← 전체 비교     │
│        └──────────────────────────────┘      │
│                                              │
│  🟢 = 사용 가능   ⚫ = 사용 불가             │
└─────────────────────────────────────────────┘
```

- `GET /api/llm/models` → `router.get_available_models()` 호출
- 드롭다운은 가용 모델만 선택 가능, 불가한 것은 회색 + 사유 표시
- 선택한 모델은 API 호출 시 `force_provider` + `force_model`로 전달

### 9.2 비교 모드 UI

"🔬 비교 모드"를 선택하면, 같은 입력을 여러 모델에 동시에 보내고 결과를 나란히 표시.

```
┌─ 비교 결과 ──────────────────────────────────────────────┐
│                                                          │
│  입력: [페이지 3 이미지 — 레이아웃 분석]                   │
│                                                          │
│  ┌──────────────┬──────────────┬──────────────┐          │
│  │ Ollama       │ Gemini       │ Claude       │          │
│  │ gemma4:e4b   │ 2.5-flash    │ sonnet-4     │          │
│  ├──────────────┼──────────────┼──────────────┤          │
│  │ 블록 5개      │ 블록 6개      │ 블록 5개      │          │
│  │ 본문 2       │ 본문 2       │ 본문 2       │          │
│  │ 주석 2       │ 주석 3       │ 주석 2       │          │
│  │ 판심제 1     │ 판심제 1     │ 판심제 1     │          │
│  │              │ 장차 추가 ★  │              │          │
│  │              │              │              │          │
│  │ ⏱ 2.1초      │ ⏱ 3.4초      │ ⏱ 1.8초      │          │
│  │ 💰 무료       │ 💰 무료       │ 💰 $0.003    │          │
│  ├──────────────┼──────────────┼──────────────┤          │
│  │ [이 결과 채택]│ [이 결과 채택]│ [이 결과 채택]│          │
│  └──────────────┴──────────────┴──────────────┘          │
│                                                          │
│  [전체 취소]                                              │
└──────────────────────────────────────────────────────────┘
```

- "이 결과 채택" → 해당 모델의 결과를 Draft로 전환 → 기존 Review 워크플로우로
- 비교 결과는 usage_tracker에 기록 → "어떤 모델을 자주 채택했나" 통계 가능

### 9.3 품질 평가 기록

Draft를 review할 때 간단한 품질 평가를 기록할 수 있다.

```python
@dataclass
class LlmDraft:
    # ... 기존 필드들 ...
    
    # 품질 평가 (review 시 기록)
    quality_rating: int | None = None    # 1-5점
    quality_notes: str | None = None     # "주석 영역을 빠뜨렸음"
    
    # 비교 모드에서 채택된 경우
    compared_with: list[str] | None = None  # ["ollama", "anthropic"]
    chosen_reason: str | None = None        # "블록 구분이 가장 정확"
```

이 데이터가 쌓이면 나중에 용도별 최적 모델을 데이터 기반으로 판단할 수 있다:

```
"레이아웃 분석 30회 중:
  - Ollama gemma4:e4b 채택 22회 (평균 4.2점)
  - Gemini 채택 4회 (평균 4.0점)
  - Claude 채택 4회 (평균 4.5점, 비용 대비 효율은 낮음)
→ 기본 모델 gemma4:e4b 유지 권장"
```

### 9.4 Ollama 모델 목록 동적 조회

Ollama provider에 모델 목록 조회 기능 추가:

```python
class OllamaProvider(BaseLlmProvider):
    
    async def list_models(self) -> list[dict]:
        """Ollama에 설치된 모델 목록 조회.
        
        GET localhost:11434/api/tags → 설치된 모델 목록
        클라우드 모델(:cloud 접미사)도 포함.
        
        반환 예시:
        [
            {"name": "qwen3-vl:235b-cloud", "size": "N/A", "vision": True},
            {"name": "kimi-k2.5:cloud", "size": "N/A", "vision": False},
            {"name": "llama3.2:3b", "size": "2.0 GB", "vision": False},
        ]
        """
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{self.OLLAMA_URL}/api/tags")
            data = resp.json()
        
        models = []
        for m in data.get("models", []):
            name = m.get("name", "")
            models.append({
                "name": name,
                "size": m.get("size", "N/A"),
                "vision": any(kw in name.lower() for kw in ["vl", "vision", "llava"]),
            })
        return models
```

---

## 9. Phase 10-2 세션 지시문 (업데이트)

```
# 컨텍스트

프로젝트: 고전서지 통합 브라우저
Phase 10-1 OCR 완료. 이번은 LLM 아키텍처.

CLAUDE.md를 먼저 읽어.
docs/phase10_12_design.md — Phase 10-2 섹션 읽어.
docs/llm_architecture_design.md — 이 문서를 읽어. LLM 호출 아키텍처 상세 설계.

## 이번 목표: Phase 10-2 — LLM 호출 아키텍처 + 레이아웃 분석

### 핵심: 5단 폴백 LLM Router

우선순위:
1. Ollama 로컬 모델 gemma4:e4b (localhost:11434)
2. OpenAI OAuth 프록시 (start_server.bat 자동 기동, API 키 없음)
3. Gemini API (저렴)
4. OpenAI API (중간)
5. Anthropic API (최후 폴백)

모든 LLM 호출은 src/llm/router.py를 통해야 한다.
provider를 직접 호출하지 않는다.

## 작업 순서

### 작업 1: Provider 추상 클래스 + LlmResponse

src/llm/providers/base.py:
- BaseLlmProvider: is_available(), call(), call_with_image()
- LlmResponse: text, provider, model, tokens, cost, raw
- LlmProviderError, LlmUnavailableError

### 작업 2: Ollama Provider (1순위)

src/llm/providers/ollama.py:
- localhost:11434/api/generate POST
- 기본 모델: gemma4:e4b (멀티모달 — 텍스트+비전 통합)
- call_with_image: images 필드에 base64 배열

### 작업 3: OpenAI OAuth Provider (2순위)

src/llm/providers/openai_oauth_provider.py:
- OpenAI 호환 프록시(base_url) 사용
- 포트 10531~10540 자동 감지
- 기본 모델: gpt-5.4-mini

### 작업 4: Gemini Provider (3순위)

src/llm/providers/gemini_provider.py:
- google-genai SDK 사용
- 기본 모델: gemini-2.5-flash

### 작업 5: OpenAI Provider (4순위)

src/llm/providers/openai_provider.py:
- openai SDK 사용
- 기본 모델: gpt-5-mini

### 작업 6: Anthropic Provider (5순위)

src/llm/providers/anthropic.py:
- anthropic Python SDK (uv add anthropic)
- 기본 모델: claude-sonnet-4-20250514

### 작업 7: Router + Config

src/llm/router.py:
- LlmRouter: providers 리스트를 순서대로 시도
- call(), call_with_image() — 첫 성공 provider 결과 반환
- 전부 실패 시 LlmUnavailableError (에러 메시지에 각 provider 실패 이유)

src/llm/config.py:
- .env에서 API 키, backend-44 경로, 월간 예산 읽기
- 기본 설정값 (DEFAULT_MODELS 등)

### 작업 8: 비용 추적

src/llm/usage_tracker.py:
- 서고별 llm_usage_log.jsonl
- log(response, purpose) — 매 호출 기록
- get_monthly_summary() — 월간 요약

### 작업 9: Draft 모델

src/llm/draft.py:
- LlmDraft: draft_id, purpose, status (pending→accepted/modified/rejected)
- Draft → Review → Commit 패턴의 기반

### 작업 10: 레이아웃 분석 (Draft 패턴 첫 적용)

src/llm/prompts/layout_analysis.yaml:
- 이미지 → LayoutBlock 제안 JSON
- block_type 목록 포함

src/core/layout_analyzer.py:
- analyze_page_layout() → LlmDraft (status: pending)
- commit_layout_draft() → layout_page.json 저장 + git commit

### 작업 11: API 엔드포인트

POST /api/documents/{doc_id}/pages/{page}/layout/analyze
POST /api/llm/drafts/{draft_id}/commit
GET /api/llm/usage
POST /api/llm/config

GET /api/llm/status — 각 provider 가용 상태 조회
  응답: {
    "ollama": {"available": true, "models": ["gemma4:e4b"]},
    "openai_oauth": {"available": false, "reason": "프록시 미실행"},
    "gemini": {"available": false, "reason": "API 키 미설정"},
    "openai": {"available": false, "reason": "API 키 미설정"},
    "anthropic": {"available": false, "reason": "API 키 미설정"}
  }

### 작업 12: GUI — AI 분석 + Provider 상태

layout-editor.js:
- "AI 분석" 버튼 → POST /analyze
- 제안 블록을 점선으로 표시
- 블록별 [✅ 승인] [✏️ 수정] [❌ 삭제]
- "전체 확정" → POST /commit

설정 또는 사이드바:
- LLM 상태 표시: 🟢 Ollama | ⚫ OpenAI OAuth | ⚫ Gemini | ⚫ Claude
- 이번 달 비용: $X.XX / $10.00

### 작업 13: 통합 테스트

1. router가 provider를 순서대로 시도하는지 (mock)
2. OpenAI OAuth 프록시가 꺼졌을 때 다음 provider로 폴백하는지
3. 레이아웃 분석 → Draft → Review → Commit 전체 흐름

커밋: "feat: Phase 10-2 — LLM 5단 폴백 아키텍처 + 레이아웃 분석"
```
