"""Claude on a company's own Amazon Bedrock or Google Vertex AI
(providers.cloud): settings, routing and the request each cloud gets."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from openexecutive.config import Settings
from openexecutive.providers import cloud
from openexecutive.providers import registry as registry_mod
from openexecutive.providers.registry import (
    allowed_models,
    get_provider,
    model_options_for,
    pdf_input_supported,
)

BEDROCK_ID = "global.anthropic.claude-sonnet-5-5-v1:0"
VERTEX_ID = "claude-opus-5-5@20260301"
_VARS = (
    "BEDROCK_REGION", "BEDROCK_API_KEY", "BEDROCK_ACCESS_KEY_ID", "BEDROCK_SECRET_ACCESS_KEY", "BEDROCK_MODELS",
    "VERTEX_PROJECT_ID", "VERTEX_REGION", "VERTEX_CREDENTIALS_JSON", "VERTEX_MODELS",
    "OPENROUTER_ENABLED", "LOCAL_MODELS_ENABLED", "AZURE_AI_DEPLOYMENTS", "AWS_BEARER_TOKEN_BEDROCK",
)
_REPLY = {
    "id": "msg_1", "type": "message", "role": "assistant", "model": "x",
    "content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn",
    "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
}
_SEARCH = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
_CACHED_SYSTEM = [{"type": "text", "text": "persona", "cache_control": {"type": "ephemeral"}}]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("EXEC_EMAIL_ADDRESS", "exec@example.com")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-the-anthropic-key")
    for key in _VARS:
        monkeypatch.delenv(key, raising=False)
    registry_mod._reset_for_tests()
    yield
    registry_mod._reset_for_tests()


def _bedrock(monkeypatch: pytest.MonkeyPatch, *, api_key: bool = True) -> None:
    monkeypatch.setenv("BEDROCK_REGION", "us-east-1")
    monkeypatch.setenv("BEDROCK_MODELS", f"claude-sonnet-5-5={BEDROCK_ID}")
    if api_key:
        monkeypatch.setenv("BEDROCK_API_KEY", "bedrock-api-key")
    else:
        monkeypatch.setenv("BEDROCK_ACCESS_KEY_ID", "AKIAEXAMPLE")
        monkeypatch.setenv("BEDROCK_SECRET_ACCESS_KEY", "aws-secret")
    registry_mod._reset_for_tests()


def _vertex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERTEX_PROJECT_ID", "acme-ai")
    monkeypatch.setenv("VERTEX_REGION", "us-east5")
    monkeypatch.setenv("VERTEX_MODELS", f"claude-opus-5-5={VERTEX_ID}")
    registry_mod._reset_for_tests()


def _capture(provider: Any) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_REPLY)

    provider._client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return seen


def _inner(model: str) -> Any:
    provider = get_provider(model)
    return getattr(provider, "_inner", provider)


# --------------------------------------------------------------------------- #
# Settings and routing
# --------------------------------------------------------------------------- #


def test_only_claude_can_be_mapped_to_a_cloud() -> None:
    with pytest.raises(ValueError, match="Claude models only"):
        Settings(BEDROCK_REGION="us-east-1", BEDROCK_MODELS="openai/gpt-6-astra=x")  # type: ignore[call-arg]


def test_bedrock_needs_a_region_and_a_whole_key_pair() -> None:
    with pytest.raises(ValueError, match="BEDROCK_REGION"):
        Settings(BEDROCK_MODELS=f"claude-sonnet-5-5={BEDROCK_ID}")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="go together"):
        Settings(  # type: ignore[call-arg]
            BEDROCK_REGION="us-east-1", BEDROCK_MODELS=f"claude-sonnet-5-5={BEDROCK_ID}", BEDROCK_ACCESS_KEY_ID="AKIA"
        )


def test_vertex_needs_a_project_and_region() -> None:
    with pytest.raises(ValueError, match="VERTEX_PROJECT_ID"):
        Settings(VERTEX_MODELS=f"claude-opus-5-5={VERTEX_ID}")  # type: ignore[call-arg]


def test_a_cloud_alone_is_a_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    settings = Settings(BEDROCK_REGION="us-east-1", BEDROCK_MODELS=f"claude-sonnet-5-5={BEDROCK_ID}")  # type: ignore[call-arg]
    assert settings.bedrock_models == {"claude-sonnet-5-5": BEDROCK_ID}


def test_each_mapped_model_goes_to_its_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    _bedrock(monkeypatch)
    _vertex(monkeypatch)
    assert isinstance(_inner("claude-sonnet-5-5"), cloud.BedrockClaudeProvider)
    assert isinstance(_inner("claude-opus-5-5"), cloud.VertexClaudeProvider)
    assert type(_inner("claude-haiku-5-5")).__name__ == "AnthropicProvider"
    assert pdf_input_supported("claude-sonnet-5-5") is True


def test_the_council_shows_cloud_models_as_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    _bedrock(monkeypatch)
    assert allowed_models().count("claude-sonnet-5-5") == 1
    options = {o["id"]: o for o in model_options_for(None)}
    assert options["claude-sonnet-5-5"]["route"] == "direct"


# --------------------------------------------------------------------------- #
# The request each cloud gets
# --------------------------------------------------------------------------- #


async def test_bedrock_gets_its_model_id_with_caching_and_no_web_search(monkeypatch: pytest.MonkeyPatch) -> None:
    _bedrock(monkeypatch)
    provider = _inner("claude-sonnet-5-5")
    seen = _capture(provider)
    await provider.messages_create(
        model="claude-sonnet-5-5", max_tokens=50, system=_CACHED_SYSTEM, tools=_SEARCH,
        messages=[{"role": "user", "content": "hi"}],
    )
    [request] = seen
    body = json.loads(request.content)
    assert request.url.host == "bedrock-runtime.us-east-1.amazonaws.com"
    assert request.url.path == f"/model/{BEDROCK_ID}/invoke"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "tools" not in body or not body["tools"]
    assert request.headers["authorization"] == "Bearer bedrock-api-key"
    assert "sk-ant-the-anthropic-key" not in " ".join(request.headers.values())
    assert "x-api-key" not in request.headers


async def test_bedrock_signs_with_an_access_key_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("botocore")
    _bedrock(monkeypatch, api_key=False)
    provider = _inner("claude-sonnet-5-5")
    seen = _capture(provider)
    await provider.messages_create(model="claude-sonnet-5-5", max_tokens=5, messages=[{"role": "user", "content": "hi"}])
    [request] = seen
    assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE/")
    assert "aws-secret" not in " ".join(request.headers.values())


async def test_vertex_gets_its_model_id_and_the_service_accounts_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from google.oauth2.credentials import Credentials

    _vertex(monkeypatch)
    provider = cloud.VertexClaudeProvider(
        project_id="acme-ai", region="us-east5", models={"claude-opus-5-5": VERTEX_ID},
        credentials=Credentials(token="ya29.vertex-token"),
    )
    seen = _capture(provider)
    await provider.messages_create(
        model="claude-opus-5-5", max_tokens=5, tools=_SEARCH, messages=[{"role": "user", "content": "hi"}]
    )
    [request] = seen
    body = json.loads(request.content)
    assert request.url.host == "us-east5-aiplatform.googleapis.com"
    assert request.url.path == (
        f"/v1/projects/acme-ai/locations/us-east5/publishers/anthropic/models/{VERTEX_ID}:rawPredict"
    )
    assert request.headers["authorization"] == "Bearer ya29.vertex-token"
    assert "tools" not in body or not body["tools"]
    assert "sk-ant-the-anthropic-key" not in " ".join(request.headers.values())


def test_a_bad_service_account_key_never_echoes_it() -> None:
    with pytest.raises(ValueError) as caught:
        cloud.vertex_credentials('{"private_key": "-----BEGIN secret-key-material"}')
    assert "secret-key-material" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_neither_cloud_follows_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    from google.oauth2.credentials import Credentials

    bedrock = cloud.BedrockClaudeProvider(region="us-east-1", models={}, api_key="k")
    vertex = cloud.VertexClaudeProvider(project_id="p", region="global", models={}, credentials=Credentials(token="t"))
    assert bedrock._client._client.follow_redirects is False
    assert vertex._client._client.follow_redirects is False


# --------------------------------------------------------------------------- #
# Hosts, ids and keys come from these settings only
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "env",
    [
        {"BEDROCK_REGION": "evil.example/x"},
        {"BEDROCK_REGION": "us-east-1.evil.example#"},
        {"VERTEX_REGION": "evil.example/"},
        {"VERTEX_PROJECT_ID": "acme/../other"},
    ],
)
def test_a_region_or_project_cant_change_the_host(env: dict[str, str]) -> None:
    base: dict[str, str] = {
        "BEDROCK_REGION": "us-east-1", "BEDROCK_MODELS": f"claude-sonnet-5-5={BEDROCK_ID}",
        "VERTEX_PROJECT_ID": "acme-ai", "VERTEX_REGION": "us-east5", "VERTEX_MODELS": f"claude-opus-5-5={VERTEX_ID}",
    }
    with pytest.raises(ValueError, match="isn't a"):
        Settings(**{**base, **env})  # type: ignore[arg-type]


def test_a_region_is_used_without_its_spaces() -> None:
    settings = Settings(BEDROCK_REGION=" us-east-1 ", BEDROCK_MODELS=f"claude-sonnet-5-5={BEDROCK_ID}")  # type: ignore[call-arg]
    assert settings.bedrock_region == "us-east-1"


@pytest.mark.parametrize("pair", ["claude-sonnet-5-5=a/b", "claude-sonnet-5-5=x?y", "claude-sonnet-5-5/x=ok"])
def test_a_model_id_cant_reach_another_path(pair: str) -> None:
    with pytest.raises(ValueError, match="Claude models only|characters"):
        Settings(BEDROCK_REGION="us-east-1", BEDROCK_MODELS=pair)  # type: ignore[call-arg]


async def test_an_unmapped_model_is_refused_not_sent_under_its_name() -> None:
    provider = cloud.BedrockClaudeProvider(region="us-east-1", models={}, api_key="k")
    seen = _capture(provider)
    with pytest.raises(ValueError, match="no id"):
        await provider.messages_create(model="claude-haiku-5-5", max_tokens=5, messages=[{"role": "user", "content": "hi"}])
    assert seen == []


async def test_the_sdks_environment_overrides_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("botocore")
    monkeypatch.setenv("ANTHROPIC_BEDROCK_BASE_URL", "https://evil.example")
    monkeypatch.setenv("ANTHROPIC_VERTEX_BASE_URL", "https://evil.example")
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "someone-elses-token")
    _bedrock(monkeypatch, api_key=False)
    bedrock = _inner("claude-sonnet-5-5")
    seen = _capture(bedrock)
    await bedrock.messages_create(model="claude-sonnet-5-5", max_tokens=5, messages=[{"role": "user", "content": "hi"}])
    [request] = seen
    assert request.url.host == "bedrock-runtime.us-east-1.amazonaws.com"
    assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE/")
    assert "someone-elses-token" not in " ".join(request.headers.values())

    from google.oauth2.credentials import Credentials

    vertex = cloud.VertexClaudeProvider(
        project_id="acme-ai", region="global", models={"claude-opus-5-5": VERTEX_ID}, credentials=Credentials(token="t")
    )
    seen = _capture(vertex)
    await vertex.messages_create(model="claude-opus-5-5", max_tokens=5, messages=[{"role": "user", "content": "hi"}])
    assert seen[0].url.host == "aiplatform.googleapis.com"


@pytest.mark.parametrize(
    ("region", "host"),
    [
        ("global", "aiplatform.googleapis.com"),
        ("us", "aiplatform.us.rep.googleapis.com"),
        ("eu", "aiplatform.eu.rep.googleapis.com"),
        ("us-east5", "us-east5-aiplatform.googleapis.com"),
    ],
)
def test_each_vertex_region_has_googles_host(region: str, host: str) -> None:
    assert httpx.URL(cloud.vertex_base_url(region)).host == host


def _service_account(**extra: str) -> str:
    return json.dumps({
        "type": "service_account", "project_id": "acme-ai", "client_email": "oe@acme-ai.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----secret-key-material", "private_key_id": "1", **extra,
    })


def test_a_service_account_key_gets_tokens_from_google_only() -> None:
    raw = _service_account(token_uri="https://evil.example/token")
    with pytest.raises(ValueError, match="token_uri") as caught:
        Settings(VERTEX_PROJECT_ID="acme-ai", VERTEX_REGION="global", VERTEX_CREDENTIALS_JSON=raw)  # type: ignore[call-arg]
    assert "secret-key-material" not in str(caught.value)
    with pytest.raises(ValueError) as again:
        cloud.vertex_credentials(raw)
    assert "secret-key-material" not in str(again.value)


def test_a_settings_error_never_prints_the_key() -> None:
    with pytest.raises(ValueError) as caught:
        Settings(VERTEX_PROJECT_ID="acme/x", VERTEX_REGION="global", VERTEX_CREDENTIALS_JSON=_service_account())  # type: ignore[call-arg]
    assert "secret-key-material" not in str(caught.value)


async def test_signing_runs_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("botocore")
    import threading

    from anthropic.lib.bedrock import _auth

    loop_thread = threading.get_ident()
    signed_on: list[int] = []
    real = _auth.get_auth_headers

    def spy(**kwargs: Any) -> Any:
        signed_on.append(threading.get_ident())
        return real(**kwargs)

    monkeypatch.setattr(_auth, "get_auth_headers", spy)
    _bedrock(monkeypatch, api_key=False)
    provider = _inner("claude-sonnet-5-5")
    seen = _capture(provider)
    await provider.messages_create(model="claude-sonnet-5-5", max_tokens=5, messages=[{"role": "user", "content": "hi"}])
    assert seen and signed_on and signed_on[0] != loop_thread
    assert seen[0].headers["authorization"].startswith("AWS4-HMAC-SHA256")
