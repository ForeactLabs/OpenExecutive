"""Azure (Microsoft Foundry / Azure OpenAI): models a company runs in its own
Azure account, each mapped to its deployment (providers.azure)."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from openexecutive.config import Settings
from openexecutive.providers import azure
from openexecutive.providers import registry as registry_mod
from openexecutive.providers.registry import (
    allowed_models,
    get_provider,
    model_options_for,
    pdf_input_supported,
)

ENDPOINT = "https://contoso-ai.services.ai.azure.com"
DEPLOYMENTS = "claude-sonnet-5-5=claude-sonnet-prod,openai/gpt-6-astra=gpt6,meta-llama/llama-4-maverick=llama4"

_AZURE_VARS = (
    "AZURE_AI_ENDPOINT", "AZURE_AI_API_KEY", "AZURE_AI_TENANT_ID", "AZURE_AI_CLIENT_ID",
    "AZURE_AI_CLIENT_SECRET", "AZURE_AI_DEPLOYMENTS",
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("EXEC_EMAIL_ADDRESS", "exec@example.com")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("OPENROUTER_ENABLED", raising=False)
    monkeypatch.delenv("LOCAL_MODELS_ENABLED", raising=False)
    for key in _AZURE_VARS:
        monkeypatch.delenv(key, raising=False)
    registry_mod._reset_for_tests()
    yield
    registry_mod._reset_for_tests()


def _azure(monkeypatch: pytest.MonkeyPatch, *, key: bool = True) -> None:
    monkeypatch.setenv("AZURE_AI_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("AZURE_AI_DEPLOYMENTS", DEPLOYMENTS)
    if key:
        monkeypatch.setenv("AZURE_AI_API_KEY", "azure-resource-key")
    else:
        monkeypatch.setenv("AZURE_AI_TENANT_ID", "tenant-1")
        monkeypatch.setenv("AZURE_AI_CLIENT_ID", "app-1")
        monkeypatch.setenv("AZURE_AI_CLIENT_SECRET", "app-secret")
    registry_mod._reset_for_tests()


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_deployments_parse_as_pairs_or_json(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch, AZURE_AI_ENDPOINT=ENDPOINT, AZURE_AI_API_KEY="k", AZURE_AI_DEPLOYMENTS=DEPLOYMENTS)
    assert s.azure_ai_deployments == {
        "claude-sonnet-5-5": "claude-sonnet-prod",
        "openai/gpt-6-astra": "gpt6",
        "meta-llama/llama-4-maverick": "llama4",
    }
    s = _settings(monkeypatch, AZURE_AI_DEPLOYMENTS=json.dumps({"claude-opus-5-5": "opus"}))
    assert s.azure_ai_deployments == {"claude-opus-5-5": "opus"}


def test_unset_azure_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(_env_file=None).azure_ai_deployments == {}  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "env",
    [
        {"AZURE_AI_DEPLOYMENTS": "claude-sonnet-5-5"},  # no deployment
        {"AZURE_AI_DEPLOYMENTS": "claude-sonnet-5-5=x", "AZURE_AI_API_KEY": "k"},  # no endpoint
        {"AZURE_AI_DEPLOYMENTS": "claude-sonnet-5-5=x", "AZURE_AI_ENDPOINT": "http://plain.example", "AZURE_AI_API_KEY": "k"},
        {"AZURE_AI_DEPLOYMENTS": "claude-sonnet-5-5=x", "AZURE_AI_ENDPOINT": ENDPOINT},  # no sign-in
        {"AZURE_AI_DEPLOYMENTS": "claude-sonnet-5-5=x", "AZURE_AI_ENDPOINT": ENDPOINT, "AZURE_AI_TENANT_ID": "t"},
    ],
)
def test_incomplete_azure_settings_are_refused_at_start(env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        _settings(monkeypatch, **env)


def test_azure_alone_is_a_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    s = _settings(monkeypatch, AZURE_AI_ENDPOINT=ENDPOINT, AZURE_AI_API_KEY="k", AZURE_AI_DEPLOYMENTS=DEPLOYMENTS)
    assert s.azure_ai_deployments


@pytest.mark.parametrize(
    "pasted", [ENDPOINT, f"{ENDPOINT}/", f"{ENDPOINT}/openai/v1", f"{ENDPOINT}/anthropic/", f"{ENDPOINT}/anthropic/v1"]
)
def test_a_pasted_endpoint_url_is_cut_to_the_resource(pasted: str) -> None:
    assert azure.endpoint_base(pasted) == ENDPOINT


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


def test_mapped_models_go_to_azure_and_the_rest_do_not(monkeypatch: pytest.MonkeyPatch) -> None:
    _azure(monkeypatch)
    get_provider("claude-sonnet-5-5")
    assert registry_mod._azure_claude_provider is not None
    get_provider("meta-llama/llama-4-maverick")
    get_provider("openai/gpt-6-astra")
    assert registry_mod._azure_openai_provider is not None
    get_provider("claude-opus-5-5")  # not mapped: Anthropic as before
    assert registry_mod._anthropic_provider is not None


def test_the_council_offers_the_mapped_models_as_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    _azure(monkeypatch)
    models = allowed_models()
    assert "meta-llama/llama-4-maverick" in models and "openai/gpt-6-astra" in models
    assert models.count("claude-sonnet-5-5") == 1
    routes = {o["id"]: o["route"] for o in model_options_for(None)}
    assert routes["meta-llama/llama-4-maverick"] == "direct"
    assert routes["claude-sonnet-5-5"] == "direct"


def test_pdfs_only_reach_claude_on_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_ENABLED", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    _azure(monkeypatch)
    assert pdf_input_supported("claude-sonnet-5-5") is True
    assert pdf_input_supported("meta-llama/llama-4-maverick") is False
    assert pdf_input_supported("google/gemini-3.8-flash") is True  # OpenRouter, as before


# --------------------------------------------------------------------------- #
# Claude on Foundry
# --------------------------------------------------------------------------- #


async def test_claude_is_sent_as_its_deployment_with_caching_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    _azure(monkeypatch)
    provider = registry_mod._azure_claude()
    sent: list[dict[str, Any]] = []

    async def create(**kwargs: Any) -> str:
        sent.append(kwargs)
        return "ok"

    monkeypatch.setattr(provider._client.messages, "create", create)
    system = [{"type": "text", "text": "persona", "cache_control": {"type": "ephemeral"}}]
    tools = [
        {"name": "lookup", "description": "d", "input_schema": {"type": "object"}},
        {"type": "web_search_20250305", "name": "web_search"},
    ]
    await provider.messages_create(
        model="claude-sonnet-5-5", max_tokens=100, system=system, tools=tools, messages=[{"role": "user", "content": "hi"}]
    )
    [body] = sent
    assert body["model"] == "claude-sonnet-prod"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert [t["name"] for t in body["tools"]] == ["lookup"]  # no web search on Azure
    assert str(provider._client.base_url) == f"{ENDPOINT}/anthropic/"


def test_claude_signs_in_with_the_resource_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _azure(monkeypatch)
    provider = registry_mod._azure_claude()
    assert provider._client.api_key == "azure-resource-key"


# --------------------------------------------------------------------------- #
# Every other model on the OpenAI-compatible endpoint
# --------------------------------------------------------------------------- #


def _capture(provider: azure.AzureOpenAIProvider, monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "x",
                "model": "llama4",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )

    client = httpx.AsyncClient(
        base_url=provider._base_url, headers=provider._client.headers, transport=httpx.MockTransport(handler)
    )
    monkeypatch.setattr(provider, "_client", client)
    return seen


async def test_other_models_use_the_deployment_and_azures_key_header(monkeypatch: pytest.MonkeyPatch) -> None:
    _azure(monkeypatch)
    provider = registry_mod._azure_openai()
    seen = _capture(provider, monkeypatch)
    await provider.messages_create(
        model="meta-llama/llama-4-maverick", max_tokens=50, messages=[{"role": "user", "content": "hi"}]
    )
    [request] = seen
    assert str(request.url) == f"{ENDPOINT}/openai/v1/chat/completions"
    assert request.headers["api-key"] == "azure-resource-key"
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["model"] == "llama4" and body["max_tokens"] == 50


async def test_openai_models_get_openais_wording(monkeypatch: pytest.MonkeyPatch) -> None:
    _azure(monkeypatch)
    provider = registry_mod._azure_openai()
    seen = _capture(provider, monkeypatch)
    await provider.messages_create(
        model="openai/gpt-6-astra",
        max_tokens=50,
        temperature=0.2,
        tool_choice={"type": "any"},
        tools=[{"name": "lookup", "description": "d", "input_schema": {"type": "object"}}],
        messages=[{"role": "user", "content": "hi"}],
    )
    body = json.loads(seen[0].content)
    assert body["model"] == "gpt6"
    assert body["max_completion_tokens"] == 50 and "max_tokens" not in body
    assert "temperature" not in body
    assert body["tool_choice"] == "required"


# --------------------------------------------------------------------------- #
# Entra app sign-in
# --------------------------------------------------------------------------- #


def _login(calls: list[httpx.Request], *, status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": "invalid_client", "error_description": "secret app-secret"})
        return httpx.Response(200, json={"access_token": f"token-{len(calls)}", "expires_in": 3600})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_an_entra_token_is_fetched_once_and_reused() -> None:
    calls: list[httpx.Request] = []
    token = azure.EntraToken(
        tenant_id="tenant-1", client_id="app-1", client_secret="app-secret", scope="scope/.default", http=_login(calls)
    )
    assert await token.get() == "token-1"
    assert await token.get() == "token-1"
    [request] = calls
    assert str(request.url) == "https://login.microsoftonline.com/tenant-1/oauth2/v2.0/token"
    form = dict(httpx.QueryParams(request.content.decode()))
    assert form["grant_type"] == "client_credentials" and form["scope"] == "scope/.default"


async def test_a_refused_entra_sign_in_never_echoes_the_body() -> None:
    calls: list[httpx.Request] = []
    token = azure.EntraToken(tenant_id="t", client_id="c", client_secret="app-secret", scope="s", http=_login(calls, status=401))
    with pytest.raises(azure.EntraSignInError) as caught:
        await token.get()
    assert caught.value.status_code == 401
    assert "app-secret" not in str(caught.value) and "app-secret" not in repr(vars(caught.value))


def _claude_requests(provider: azure.AzureClaudeProvider, status: int = 200) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status != 200:
            return httpx.Response(status, headers={"location": "http://elsewhere.example/steal"})
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "dep",
            "content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    provider._client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return seen


@pytest.mark.parametrize("key", [True, False])
async def test_claude_on_azure_never_sends_the_anthropic_credentials(key: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-the-anthropic-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "the-anthropic-token")
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "x-anthropic-extra: meant-for-anthropic")
    calls: list[httpx.Request] = []
    token = None if key else azure.EntraToken(tenant_id="t", client_id="c", client_secret="s", scope="sc", http=_login(calls))
    provider = azure.AzureClaudeProvider(
        endpoint=ENDPOINT, deployments={"claude-sonnet-5-5": "dep"}, api_key="azure-resource-key" if key else None, token=token
    )
    seen = _claude_requests(provider)
    await provider.messages_create(model="claude-sonnet-5-5", max_tokens=5, messages=[{"role": "user", "content": "hi"}])
    [request] = seen
    sent = " ".join(f"{k}: {v}" for k, v in request.headers.items())
    assert "sk-ant-the-anthropic-key" not in sent and "the-anthropic-token" not in sent
    assert "meant-for-anthropic" not in sent and "x-api-key" not in request.headers
    if key:
        assert request.headers["api-key"] == "azure-resource-key" and "authorization" not in request.headers
    else:
        assert request.headers["authorization"] == "Bearer token-1" and "api-key" not in request.headers


def test_claude_on_azure_does_not_follow_redirects() -> None:
    provider = azure.AzureClaudeProvider(endpoint=ENDPOINT, deployments={}, api_key="azure-resource-key")
    assert provider._client._client.follow_redirects is False


async def test_entra_sign_in_sends_a_bearer_token_not_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _azure(monkeypatch, key=False)
    provider = registry_mod._azure_openai()
    calls: list[httpx.Request] = []
    assert registry_mod._azure_token is not None
    registry_mod._azure_token._http = _login(calls)
    seen = _capture(provider, monkeypatch)
    await provider.messages_create(model="meta-llama/llama-4-maverick", max_tokens=5, messages=[{"role": "user", "content": "hi"}])
    assert seen[0].headers["authorization"] == "Bearer token-1"
    assert "api-key" not in seen[0].headers
    # Claude shares the same sign-in.
    claude = registry_mod._azure_claude()
    assert claude._client._azure_ad_token_provider == registry_mod._azure_token.get  # type: ignore[attr-defined]
