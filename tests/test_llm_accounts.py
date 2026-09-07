"""LLM 연결 상태 라우트 테스트 — 배포판에서 «무엇을 해야 하는가».

왜 이 테스트가 있는가:
    배포판 사용자는 각자 자기 계정으로 LLM을 연결해야 한다. API 키는
    `.env`에 넣으면 끝이지만, 구독형(Ollama 클라우드·OpenAI OAuth)은
    **터미널 로그인**이 필요하고 앱이 대신할 수 없다.

    더 나쁜 것은 그 중간 상태다. Ollama 서버는 로그인 없이도 뜬다.
    그래서 `is_available()`은 True인데 `:cloud` 모델을 부르면 실패하고,
    라우터가 조용히 다음 프로바이더(유료 API)로 넘어간다. 실제로 그 사고가
    있었다(D-056) — 무료로 도는 줄 알았는데 Gemini가 처리하고 있었다.

    그래서 이 라우트는 «닿는가»와 «인증됐는가»를 **따로** 판정해야 한다.
"""

import pytest
from fastapi.testclient import TestClient

from app.routers.llm_ocr import _account_status


@pytest.fixture(autouse=True)
def _isolate_ollama_shared_cache():
    """Ollama 모델 캐시는 프로세스 전체가 공유한다 — 테스트끼리 새지 않게 비운다."""
    from llm.providers.ollama import OllamaProvider

    OllamaProvider._SHARED.clear()
    yield
    OllamaProvider._SHARED.clear()


def _entry(**kw):
    """_account_status()에 넣을 항목을 만든다. 기본은 «준비된 종량제»."""
    base = {
        "display_name": "테스트 프로바이더",
        "billing_model": "metered",
        "setup_kind": "env_key",
        "reachable": True,
        "authenticated": True,
        "account": None,
        "plan": None,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
#  상태 판정
# ---------------------------------------------------------------------------


def test_reachable_but_not_signed_in_is_not_ready():
    """서버는 떴지만 로그인이 안 된 상태를 «사용 가능»으로 보이면 안 된다.

    이것이 D-056 사고의 핵심이다. 여기서 ready가 나오면 사용자는
    실행하고 나서야 안 되는 것을 알게 된다.
    """
    status, note = _account_status(
        _entry(setup_kind="cli_signin", billing_model="free", authenticated=False)
    )
    assert status == "needs_signin"
    assert "로그인" in note
    # 왜 위험한지까지 적어야 한다 — 조용히 유료 API로 넘어간다는 사실.
    assert "유료" in note


def test_signed_in_shows_account_and_plan():
    """로그인돼 있으면 어느 계정·어느 요금제인지 보여 준다."""
    status, note = _account_status(
        _entry(
            setup_kind="cli_signin",
            billing_model="free",
            account="user@example.com",
            plan="pro",
        )
    )
    assert status == "ready"
    assert "user@example.com" in note
    assert "pro" in note


def test_missing_api_key_says_what_to_do():
    """키가 없으면 «키를 넣으세요»라고 말한다 («실행 안 됨»이 아니라)."""
    status, note = _account_status(_entry(reachable=False, authenticated=False))
    assert status == "needs_key"
    assert ".env" in note


def test_offline_subscription_service_is_not_a_key_problem():
    """구독형이 안 뜬 것은 키 문제가 아니다. 안내가 달라야 한다."""
    status, note = _account_status(
        _entry(setup_kind="cli_signin", billing_model="subscription", reachable=False)
    )
    assert status == "offline"
    assert ".env" not in note


def test_metered_ready_warns_about_charges():
    """종량제가 연결됐으면 «쓴 만큼 청구된다»를 명시한다."""
    status, note = _account_status(_entry())
    assert status == "ready"
    assert "청구" in note


def test_subscription_ready_does_not_say_free():
    """구독형을 «무료»라고 하지 않는다. 금액은 0이지만 한도를 쓴다."""
    status, note = _account_status(_entry(setup_kind="cli_signin", billing_model="subscription"))
    assert status == "ready"
    assert "무료" not in note
    assert "한도" in note


# ---------------------------------------------------------------------------
#  라우트
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("HOME", str(fake_home))
    from app.server import app

    with TestClient(app) as c:
        yield c


def test_accounts_route_returns_every_provider(client):
    """등록된 프로바이더가 빠짐없이 나오고, 각 항목이 필요한 키를 갖는다.

    화면이 이 키들을 그대로 읽으므로 하나라도 빠지면 칸이 빈다.
    """
    r = client.get("/api/llm/accounts")
    assert r.status_code == 200, r.text
    providers = r.json()["providers"]
    assert providers, "프로바이더가 하나도 없다"

    ids = {p["provider_id"] for p in providers}
    assert {"ollama", "gemini", "openai", "anthropic"} <= ids

    for p in providers:
        for key in (
            "display_name",
            "billing_model",
            "setup_kind",
            "setup_steps",
            "reachable",
            "authenticated",
            "status",
            "note",
        ):
            assert key in p, f"{p['provider_id']}에 {key}가 없다"
        # «checking»·«unknown»은 Ollama 모델 고르기가 1.5초 안에 못 끝나 뒤로 미룬 상태(D-109)
        assert p["status"] in ("ready", "needs_signin", "needs_key", "offline", "checking")
        assert p["billing_model"] in ("metered", "subscription", "free", "unknown")


def test_accounts_route_never_leaks_api_keys(client):
    """응답에 API 키가 실려 나가면 안 된다.

    설정 화면은 «키가 있는가»만 알면 된다. 키 자체를 브라우저로 보내면
    화면 캡처·로그·확장 프로그램을 통해 새어 나갈 수 있다.
    """
    body = client.get("/api/llm/accounts").text
    for marker in ("sk-", "AIza", "sk-ant-"):
        assert marker not in body, f"응답에 키처럼 보이는 문자열({marker})이 있다"


def test_subscription_providers_explain_how_to_sign_in(client):
    """구독형 프로바이더는 로그인 방법을 함께 준다.

    앱이 대신 로그인할 수 없으므로, 사용자가 터미널에서 무엇을 쳐야 하는지
    화면에 있어야 한다.
    """
    providers = client.get("/api/llm/accounts").json()["providers"]
    signin = [p for p in providers if p["setup_kind"] == "cli_signin"]
    assert signin, "cli_signin 프로바이더가 하나도 없다"
    for p in signin:
        assert p["setup_steps"], f"{p['provider_id']}에 로그인 절차가 없다"


def test_signed_in_subscription_still_warns_about_quota():
    """로그인돼 있어도 구독 한도를 쓴다는 사실이 가려지면 안 된다.

    «로그인돼 있습니다»로 끝내면 공짜로 오해한다. Ollama는 클래스 기본값이
    "free"(로컬)인데 실제로 고르는 비전 모델은 클라우드일 수 있다 —
    D-056에서 문제 삼은 오해가 그대로 재발한다.
    """
    status, note = _account_status(
        _entry(
            setup_kind="cli_signin",
            billing_model="subscription",
            account="user@example.com",
            plan="pro",
            active_model="qwen3.5:397b-cloud",
        )
    )
    assert status == "ready"
    assert "한도" in note
    assert "qwen3.5:397b-cloud" in note, "어느 모델이 한도를 쓰는지 밝혀야 한다"


def test_local_model_is_not_called_subscription():
    """로컬 모델을 쓰면 한도 경고를 붙이지 않는다 (실제로 소모가 없다)."""
    status, note = _account_status(
        _entry(
            setup_kind="cli_signin",
            billing_model="free",
            account="user@example.com",
            active_model="qwen3.5:4b",
        )
    )
    assert status == "ready"
    assert "한도" not in note


def test_ollama_billing_reflects_the_model_it_would_actually_use(client):
    """Ollama의 과금 표시는 «실제로 고를 모델» 기준이어야 한다.

    클래스 기본값(free)을 그대로 쓰면, 클라우드 모델로 도는 환경에서
    «로컬 무료»라고 표시된다.
    """
    import time

    # 첫 호출은 모델 고르기가 1.5초 안에 못 끝나 «확인 중»(active_model_pending)으로
    # 돌아올 수 있다(D-109 — 화면을 기다리게 하지 않는다). 뒤에서 마저 고르므로 잠시 다시 묻는다.
    for _ in range(20):
        providers = client.get("/api/llm/accounts").json()["providers"]
        ollama = next(p for p in providers if p["provider_id"] == "ollama")
        if not ollama["reachable"]:
            pytest.skip("이 환경에는 Ollama가 떠 있지 않다")
        if not ollama.get("active_model_pending"):
            break
        time.sleep(1.0)
    else:
        pytest.skip("Ollama 모델 고르기가 20초 안에 끝나지 않았다(클라우드 왕복 지연)")
    model = ollama.get("active_model")
    assert model, "쓸 비전 모델을 알려 주지 않는다"
    expected = "subscription" if "cloud" in model else "free"
    assert ollama["billing_model"] == expected


# ===========================================================================
#  은퇴한 Ollama 모델 회피
# ===========================================================================
#
# 왜 필요한가:
#   Ollama의 /api/tags는 **은퇴한 클라우드 모델도 그대로 올려 둔다.**
#   실측(2026-07-26): qwen3-vl:235b-cloud가 목록에 있는데 부르면 HTTP 410
#   — "qwen3-vl:235b was retired at 2026-06-16".
#
#   그 모델이 자동 선택되면 쪽마다 실패하고 라우터가 조용히 다음
#   프로바이더(유료 API)로 넘어간다. 무료로 도는 줄 알았는데 요금이 나가던
#   D-056의 사고가 그대로 재현된다.


class _FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def _ollama(monkeypatch, *, models, alive):
    """list_models와 살아 있는지 확인을 가짜로 바꾼 OllamaProvider."""
    from llm.config import LlmConfig
    from llm.providers.ollama import OllamaProvider

    p = OllamaProvider(LlmConfig())
    p._vision_model_cache = None

    async def fake_list():
        return models

    async def fake_alive(model):
        return model in alive

    monkeypatch.setattr(p, "list_models", fake_list)
    monkeypatch.setattr(p, "_is_model_alive", fake_alive)
    return p


@pytest.mark.asyncio
async def test_retired_model_is_skipped(monkeypatch):
    """목록에 있어도 응답하지 않는 모델은 고르지 않는다."""
    p = _ollama(
        monkeypatch,
        models=[
            {"name": "qwen3-vl:235b-cloud", "vision": True},  # 은퇴
            {"name": "qwen3.5:cloud", "vision": True},
        ],
        alive={"qwen3.5:cloud"},
    )
    assert await p._pick_vision_model() == "qwen3.5:cloud"


@pytest.mark.asyncio
async def test_local_vision_model_is_last_resort(monkeypatch):
    """클라우드를 먼저 보되, 클라우드가 전부 죽었으면 로컬이라도 쓴다."""
    p = _ollama(
        monkeypatch,
        models=[
            {"name": "qwen3-vl:235b-cloud", "vision": True},  # 은퇴
            {"name": "gemma4:4b", "vision": True},
        ],
        alive={"gemma4:4b"},
    )
    assert await p._pick_vision_model() == "gemma4:4b"


@pytest.mark.asyncio
async def test_all_dead_falls_back_to_default(monkeypatch):
    """전부 죽었으면 기본값을 돌려주고 라우터가 다음 프로바이더로 넘어간다.

    이때 **경고를 남겨야 한다** — 조용히 유료 API로 넘어가는 것이 문제였다.
    """
    p = _ollama(
        monkeypatch,
        models=[{"name": "qwen3-vl:235b-cloud", "vision": True}],
        alive=set(),
    )
    picked = await p._pick_vision_model()
    assert picked == p.DEFAULT_MODELS["vision"]


@pytest.mark.asyncio
async def test_alive_check_reads_status_code(monkeypatch):
    """410 같은 오류 상태는 «못 쓴다»로 판정한다."""
    import httpx

    from llm.config import LlmConfig
    from llm.providers.ollama import OllamaProvider

    p = OllamaProvider(LlmConfig())

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            name = (json or {}).get("model")
            if name == "dead":
                return _FakeResponse(410, '{"error":"retired"}')
            return _FakeResponse(200, "{}")

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    assert await p._is_model_alive("dead") is False
    assert await p._is_model_alive("live") is True


class TestDotenvEncoding:
    """.env가 BOM이 붙었거나 UTF-8이 아니어도 LlmConfig가 죽지 않는다.

    왜: 여기서 UnicodeDecodeError가 나면 LLM 라우터 초기화가 실패하고, 그 라우터를
    주입받는 OCR 엔진 목록 API까지 500이 된다. Windows 메모장이 BOM을 남긴다.
    """

    def test_bom_is_stripped(self, tmp_path):
        from llm.config import LlmConfig

        (tmp_path / ".env").write_bytes("GEMINI_API_KEY=abc123\n".encode("utf-8-sig"))
        cfg = LlmConfig(library_root=tmp_path)
        assert cfg._env_cache.get("GEMINI_API_KEY") == "abc123"

    def test_cp949_does_not_raise(self, tmp_path):
        from llm.config import LlmConfig

        (tmp_path / ".env").write_bytes("# 메모\nGEMINI_API_KEY=abc123\n".encode("cp949"))
        cfg = LlmConfig(library_root=tmp_path)
        assert cfg._env_cache.get("GEMINI_API_KEY") == "abc123"


def test_ollama_local_model_without_signin_is_ready():
    """로그인 안 된 Ollama라도 고른 비전 모델이 로컬이면 «사용 가능»이다(2026-09-05 보고)."""
    e = _entry(
        provider_id="ollama",
        display_name="Ollama",
        setup_kind="cli_signin",
        reachable=True,
        authenticated=False,
        account=None,
        billing_model="free",
        active_model="gemma4:e4b",
    )
    status, note = _account_status(e)
    assert status == "ready" and "gemma4:e4b" in note


def test_ollama_cloud_model_without_signin_needs_signin():
    """클라우드 모델을 골랐는데 로그인이 없으면 여전히 «로그인 필요» — 유료 폴백 사고를 막는다."""
    e = _entry(
        provider_id="ollama",
        display_name="Ollama",
        setup_kind="cli_signin",
        reachable=True,
        authenticated=False,
        account=None,
        billing_model="subscription",
        active_model="qwen3.5:cloud",
    )
    assert _account_status(e)[0] == "needs_signin"


def test_ollama_without_installed_vision_model_says_so():
    """비전 모델이 하나도 없으면 «모델 없음»과 받을 명령을 말한다 — 로그인 여부와 무관하게."""
    e = _entry(
        provider_id="ollama",
        display_name="Ollama",
        setup_kind="cli_signin",
        reachable=True,
        authenticated=True,
        account="me@x",
        billing_model="free",
        active_model="gemma4:e4b",
        active_model_installed=False,
    )
    status, note = _account_status(e)
    assert status == "no_model" and "gemma4:e4b" in note and "받기 시작" in note


def test_ollama_url_uses_ipv6_override_even_if_default_is_written(monkeypatch):
    """기본 주소를 .env에 «명시»해 둔 경우에도 [::1]에서 찾은 주소로 부른다.

    is_alive는 그 경우 [::1]도 보고 override를 남기는데 _url이 무시하면 «떠 있음»으로
    보고하고 호출은 127.0.0.1로 나가 실패한다 (Codex 지적 2026-09-06).
    """
    from llm.config import LlmConfig
    from llm.providers.ollama import OllamaProvider

    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    p = OllamaProvider(LlmConfig())
    monkeypatch.setattr(OllamaProvider, "_url_override", "http://[::1]:11434")
    try:
        assert p._url == "http://[::1]:11434"
        monkeypatch.setenv("OLLAMA_URL", "http://10.0.0.5:11434")
        assert p._url == "http://10.0.0.5:11434"  # 다른 주소를 정해 두면 그것이 우선
    finally:
        OllamaProvider._url_override = None


# ---------------------------------------------------------------------------
#  D-114: 기본 비전 모델은 클라우드, 어느 모델을 받을지는 사람이 고른다
# ---------------------------------------------------------------------------


def test_default_vision_model_is_cloud():
    """기본 비전 모델은 내려받는 파일이 없는 클라우드 모델이다 — 처음 켠 PC가 9.6GB를 받지 않는다."""
    from llm.ollama_catalog import DEFAULT_VISION_MODEL
    from llm.providers.ollama import OllamaProvider

    assert OllamaProvider.DEFAULT_MODELS["vision"] == DEFAULT_VISION_MODEL
    assert "cloud" in DEFAULT_VISION_MODEL


@pytest.mark.asyncio
async def test_cloud_default_is_probed_and_local_used_when_it_fails(monkeypatch):
    """클라우드 기본은 목록에 있어도 한 번 불러 본다. 로그인이 없어 실패하면 로컬로 내려간다."""
    from llm.providers.ollama import OllamaProvider

    OllamaProvider._SHARED.clear()
    p = _ollama(
        monkeypatch,
        models=[{"name": "gemma4:cloud", "vision": True}, {"name": "gemma4:e4b", "vision": True}],
        alive={"gemma4:e4b"},
    )
    assert await p._pick_vision_model() == "gemma4:e4b"


@pytest.mark.asyncio
async def test_cloud_default_is_used_when_it_answers(monkeypatch):
    """로그인이 있어 클라우드 기본이 답하면 로컬이 같이 있어도 기본을 쓴다."""
    from llm.providers.ollama import OllamaProvider

    OllamaProvider._SHARED.clear()
    p = _ollama(
        monkeypatch,
        models=[{"name": "gemma4:e4b", "vision": True}, {"name": "gemma4:cloud", "vision": True}],
        alive={"gemma4:cloud", "gemma4:e4b"},
    )
    assert await p._pick_vision_model() == "gemma4:cloud"


def test_catalog_marks_installed_and_puts_default_first(monkeypatch):
    """후보 목록: 기본이 맨 앞, 클라우드가 로컬 앞, 깔린 것·은퇴 의심·새로 찾은 것이 표시된다."""
    from llm import ollama_catalog as oc

    monkeypatch.setattr(
        oc, "fetch_cloud_vision_names", lambda *a, **k: ["gemma4", "qwen3.5", "brand-new", "no-tag"]
    )
    # 레지스트리 확인도 가짜로 — «no-tag»는 검색 페이지에 있지만 :cloud 태그가 없는 경우(mistral-large-3 실측)
    monkeypatch.setattr(oc, "cloud_tag_exists", lambda repo, timeout=3.0: repo != "no-tag")
    out = oc.catalog({"gemma4:e4b", "glm-ocr:latest", "kimi-k3:cloud"})
    assert "no-tag:cloud" not in {m["name"] for m in out["models"]}, (
        "태그 없는 이름은 목록에서 뺀다"
    )
    names = [m["name"] for m in out["models"]]
    assert names[0] == out["default"] == "gemma4:cloud"
    kinds = [m["kind"] for m in out["models"]]
    assert kinds == sorted(kinds, key=lambda k: k != "cloud"), "클라우드가 전부 로컬 앞이어야 한다"
    by = {m["name"]: m for m in out["models"]}
    assert by["gemma4:e4b"]["installed"] and by["glm-ocr:latest"]["installed"]
    assert not by["gemma4:cloud"]["installed"]
    assert by["kimi-k3:cloud"]["maybe_retired"] is True  # 검색 페이지에 없다
    assert by["gemma4:cloud"]["maybe_retired"] is False
    assert by["brand-new:cloud"]["kind"] == "cloud"  # 검색 페이지에서 새로 찾은 것이 더해진다
    assert all(m["size_gb"] == 0 for m in out["models"] if m["kind"] == "cloud")
    assert all(m["size_gb"] > 0 for m in out["models"] if m["kind"] == "local")


def test_catalog_offline_keeps_builtin_without_retired_marks(monkeypatch):
    """ollama.com을 못 읽으면(None) 내장 목록만 주고, 아무것도 «은퇴»로 찍지 않는다."""
    from llm import ollama_catalog as oc

    monkeypatch.setattr(oc, "fetch_cloud_vision_names", lambda *a, **k: None)
    out = oc.catalog(set())
    assert len(out["models"]) == len(oc.BUILTIN)
    assert not any(m["maybe_retired"] for m in out["models"])


def test_catalog_route(client, monkeypatch):
    """GET /api/settings/ollama/catalog — 화면이 그대로 읽는 꼴(default·models·reachable)."""
    import core.env_settings as es
    from llm import ollama_catalog as oc

    monkeypatch.setattr(oc, "fetch_cloud_vision_names", lambda *a, **k: None)
    monkeypatch.setattr(
        es,
        "detect_ollama",
        lambda base_url=None: {
            "reachable": True,
            "base_url": "x",
            "models": ["gemma4:e4b"],
            "error": None,
        },
    )
    r = client.get("/api/settings/ollama/catalog")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["default"] == "gemma4:cloud" and d["reachable"] is True
    installed = {m["name"]: m["installed"] for m in d["models"]}
    assert installed["gemma4:e4b"] is True and installed["gemma4:cloud"] is False


def test_no_model_note_for_cloud_default_points_to_login_and_chooser():
    """기본이 클라우드일 때 «모델 없음»은 크기(GB)가 아니라 로그인과 「모델 받기」를 말한다."""
    e = _entry(
        provider_id="ollama",
        display_name="Ollama",
        setup_kind="cli_signin",
        reachable=True,
        authenticated=False,
        account=None,
        billing_model="subscription",
        active_model="gemma4:cloud",
        active_model_installed=False,
    )
    status, note = _account_status(e)
    assert status == "no_model"
    assert "받기 시작" in note and "로그인" in note and "모델 받기" in note and "GB" not in note


def test_needs_signin_note_points_to_buttons_not_terminal():
    """«로그인 필요» 안내는 터미널 명령이 아니라 카드의 두 단추(로그인·모델 받기)를 가리킨다."""
    e = _entry(
        provider_id="ollama",
        display_name="Ollama",
        setup_kind="cli_signin",
        reachable=True,
        authenticated=False,
        account=None,
        billing_model="subscription",
        active_model="gemma4:cloud",
    )
    status, note = _account_status(e)
    assert status == "needs_signin" and "ollama pull" not in note and "모델 받기" in note


def test_pull_log_strips_terminal_control_sequences():
    """ollama CLI가 찍는 커서·지우기 제어열은 진행 문구에서 지운다 — 화면에 «[K[?25h»가 남았다."""
    from core.ollama_signin import _ANSI

    raw = "\x1b[?2026h\x1b[?25l\x1b[1G⠙ pulling manifest \x1b[K\x1b[?25h\x1b[?2026l"
    assert _ANSI.sub("", raw).strip() == "⠙ pulling manifest"
    assert _ANSI.sub("", "\x1b[32mModel files already exist\x1b[0m") == "Model files already exist"


def test_dead_vision_model_is_not_ready():
    """고른 모델이 응답하지 않았으면(은퇴·로그인 실패) 깔려 있고 로그인돼 있어도 «연결됨»이 아니다."""
    e = _entry(
        provider_id="ollama",
        display_name="Ollama",
        setup_kind="cli_signin",
        reachable=True,
        authenticated=True,
        account="me@x",
        billing_model="subscription",
        active_model="gemma4:cloud",
        active_model_installed=True,
        active_model_dead=True,
    )
    status, note = _account_status(e)
    assert status == "no_model" and "응답하지 않습니다" in note and "모델 받기" in note


@pytest.mark.asyncio
async def test_all_dead_leaves_a_mark_for_the_card(monkeypatch):
    """전부 죽어 기본을 돌려줄 때 vision_dead를 남기고, 하나라도 살면 지운다."""
    from llm.providers.ollama import OllamaProvider

    OllamaProvider._SHARED.clear()
    p = _ollama(monkeypatch, models=[{"name": "gemma4:cloud", "vision": True}], alive=set())
    assert await p._pick_vision_model() == "gemma4:cloud"
    assert p._shared_get("vision_dead") == "gemma4:cloud"
    OllamaProvider._SHARED.clear()
    p = _ollama(
        monkeypatch, models=[{"name": "gemma4:cloud", "vision": True}], alive={"gemma4:cloud"}
    )
    assert await p._pick_vision_model() == "gemma4:cloud"
    assert not p._shared_get("vision_dead")


def test_app_version_comes_from_pyproject_not_dist_info(monkeypatch):
    """화면 아래 버전은 pyproject.toml이 정본이다 — 실행 환경(.venv-gpu)의 dist-info가 낡아도 옛 판이 보이면 안 된다.

    2026-09-06 보고: .venv-gpu의 편집 가능 설치 메타데이터가 1.2.1에 멈춰 화면에 v1.2.1이 남았다.
    """
    import importlib.metadata as md
    from pathlib import Path

    import tomllib

    from app.server import _app_version

    monkeypatch.setattr(md, "version", lambda name: "0.0.1")  # 낡은 dist-info를 흉내 낸다
    root = Path(__file__).resolve().parents[1]
    with open(root / "pyproject.toml", "rb") as f:
        expected = tomllib.load(f)["project"]["version"]
    assert _app_version() == expected != "0.0.1"


# ── D-118: 사고를 끌 수 없는 모델의 JSON 호출 ─────────────────────────────


def test_looks_like_json_strips_prose_around_object():
    from llm.providers.ollama import _looks_like_json

    assert _looks_like_json('{"a": 1}')
    assert _looks_like_json('여기 답입니다: {"a": [1, 2]} 끝.')
    assert not _looks_like_json("The user wants me to find the word that ends the title.")
    assert not _looks_like_json("")


@pytest.mark.asyncio
async def test_json_call_retries_with_thinking_when_model_writes_prose(monkeypatch):
    """glm-5.3:cloud 실측(2026-09-07): think=False면 추론이 본문에, think=True면 JSON이 본문에 온다.

    모델 이름을 코드에 적지 않고 «답이 JSON이 아니다»로 판단해 사고를 켜 한 번만 더 부른다.
    """
    import json as _json

    import httpx

    from llm.config import LlmConfig
    from llm.providers.ollama import OllamaProvider

    p = OllamaProvider(LlmConfig())
    posts = []

    class _Resp(_FakeResponse):
        def json(self):
            return _json.loads(self.text)

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            posts.append(dict(json or {}))
            if json.get("think") is False:
                body = '{"response": "The user wants me to find the title word...", "done": true}'
            else:
                body = '{"response": "{\\"title_words\\": [\\"談草\\"]}", "thinking": "…", "done": true}'
            return _Resp(200, body)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    r = await p.call("행들", response_format="json", model="glm-5.3:cloud", think=False)
    assert len(posts) == 2 and posts[0]["think"] is False and posts[1]["think"] is True
    assert "談草" in r.text

    # 답이 JSON이면 다시 부르지 않는다
    posts.clear()

    class _ClientOk(_Client):
        async def post(self, url, json=None):
            posts.append(dict(json or {}))
            return _Resp(200, '{"response": "{\\"ok\\": 1}", "done": true}')

    monkeypatch.setattr(httpx, "AsyncClient", _ClientOk)
    r = await p.call("행들", response_format="json", model="gemma4:cloud", think=False)
    assert len(posts) == 1 and '"ok"' in r.text
