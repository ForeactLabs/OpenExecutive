"""Google and xAI on their own keys (providers.vendors): routing, the request
each vendor gets, and Gemini's tool-call signatures carried across turns."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from openexecutive.config import Settings
from openexecutive.providers import registry as registry_mod
from openexecutive.providers import translator, vendors
from openexecutive.providers.registry import (
    allowed_models,
    get_provider,
    model_options_for,
    pdf_input_supported,
)

_VARS = ("GEMINI_API_KEY", "GEMINI_MODELS", "XAI_API_KEY", "XAI_MODELS", "OPENROUTER_ENABLED",
         "LOCAL_MODELS_ENABLED", "AZURE_AI_DEPLOYMENTS")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("EXEC_EMAIL_ADDRESS", "exec@example.com")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    for key in _VARS:
        monkeypatch.delenv(key, raising=False)
    registry_mod._reset_for_tests()
    yield
    registry_mod._reset_for_tests()


def _keys(monkeypatch: pytest.MonkeyPatch, *, google: bool = True, xai: bool = True) -> None:
    if google:
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    if xai:
        monkeypatch.setenv("XAI_API_KEY", "xai-key")
    registry_mod._reset_for_tests()


def _capture(provider: Any, monkeypatch: pytest.MonkeyPatch, reply: dict[str, Any] | None = None) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=reply or {
            "id": "x",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        })

    client = httpx.AsyncClient(
        base_url=provider._base_url, headers=provider._client.headers, transport=httpx.MockTransport(handler)
    )
    monkeypatch.setattr(provider, "_client", client)
    return seen


def _inner(model: str) -> Any:
    provider = get_provider(model)
    return getattr(provider, "_inner", provider)


# --------------------------------------------------------------------------- #
# Settings and routing
# --------------------------------------------------------------------------- #


def test_a_vendor_key_alone_is_a_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    settings = Settings(GEMINI_API_KEY="gemini-key")  # type: ignore[call-arg]
    assert settings.gemini_models == ["google/gemini-3.8-flash", "google/gemini-3.5-flash-lite"]


def test_a_listed_model_must_be_the_vendors(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="XAI_MODELS"):
        Settings(XAI_API_KEY="k", XAI_MODELS="x-ai/grok-4.6,openai/gpt-6-astra")  # type: ignore[call-arg]


def test_each_vendor_serves_its_own_models(monkeypatch: pytest.MonkeyPatch) -> None:
    _keys(monkeypatch)
    assert isinstance(_inner("google/gemini-3.8-flash"), vendors.GoogleProvider)
    assert isinstance(_inner("x-ai/grok-4.6"), vendors.XAIProvider)
    # Anything else keeps its route.
    assert type(_inner("claude-sonnet-5-5")).__name__ == "AnthropicProvider"


def test_without_its_key_a_vendors_model_still_needs_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    _keys(monkeypatch, google=False)
    with pytest.raises(Exception, match="OPENROUTER_ENABLED"):
        get_provider("google/gemini-3.8-flash")


def test_the_council_offers_the_listed_models_as_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_MODELS", "x-ai/grok-4.6")
    _keys(monkeypatch)
    models = allowed_models()
    assert "google/gemini-3.8-flash" in models and "x-ai/grok-4.6" in models and "x-ai/grok-4.5" not in models
    options = {o["id"]: o for o in model_options_for(None)}
    assert options["google/gemini-3.8-flash"]["route"] == "direct"
    assert options["google/gemini-3.8-flash"]["provider_label"] == "Google"
    assert options["x-ai/grok-4.6"]["provider_label"] == "xAI"


def test_pdfs_are_not_sent_to_a_vendor_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _keys(monkeypatch)
    assert pdf_input_supported("google/gemini-3.8-flash") is False
    assert pdf_input_supported("claude-sonnet-5-5") is True


# --------------------------------------------------------------------------- #
# The request each vendor gets
# --------------------------------------------------------------------------- #

_SEARCH = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
_TOOL = [{"name": "lookup", "description": "Look it up", "input_schema": {"type": "object", "properties": {}}}]


async def test_google_gets_its_own_id_key_and_reasoning_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    _keys(monkeypatch)
    provider = _inner("google/gemini-3.8-flash")
    seen = _capture(provider, monkeypatch)
    await provider.messages_create(
        model="google/gemini-3.8-flash", max_tokens=50, tools=[*_TOOL, *_SEARCH], tool_choice={"type": "any"},
        thinking={"type": "adaptive"}, output_config={"effort": "high"},
        system=[{"type": "text", "text": "persona", "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "hi"}],
    )
    [request] = seen
    body = json.loads(request.content)
    assert str(request.url) == f"{vendors.GOOGLE_BASE_URL}/chat/completions"
    assert request.headers["authorization"] == "Bearer gemini-key"
    assert body["model"] == "gemini-3.8-flash"
    assert body["reasoning_effort"] == "high" and "reasoning" not in body
    assert body["tool_choice"] == "required"
    assert [t["function"]["name"] for t in body["tools"]] == ["lookup"]
    assert "cache_control" not in json.dumps(body) and "usage" not in body


@pytest.mark.parametrize(
    ("reasoning", "effort"),
    [
        ({"effort": "max"}, "high"), ({"effort": "xhigh"}, "high"), ({"effort": "high"}, "high"),
        ({"effort": "minimal"}, "minimal"), ({"effort": "none"}, "none"), ({"enabled": False}, "none"),
        ({"max_tokens": 1024}, "low"), ({"max_tokens": 4096}, "medium"), ({"max_tokens": 32000}, "high"),
        ({}, None),
    ],
)
def test_each_reasoning_level_reaches_its_nearest_gemini_level(reasoning: dict[str, Any], effort: str | None) -> None:
    assert vendors.gemini_effort(reasoning) == effort


async def test_xai_gets_its_own_id_key_and_no_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    _keys(monkeypatch)
    provider = _inner("x-ai/grok-4.6")
    seen = _capture(provider, monkeypatch)
    await provider.messages_create(
        model="x-ai/grok-4.6", max_tokens=50, tools=_TOOL, thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": "hi"}],
    )
    [request] = seen
    body = json.loads(request.content)
    assert str(request.url) == f"{vendors.XAI_BASE_URL}/chat/completions"
    assert request.headers["authorization"] == "Bearer xai-key"
    assert body["model"] == "grok-4.6"
    assert "reasoning" not in body and "reasoning_effort" not in body


# --------------------------------------------------------------------------- #
# Gemini's thought signatures
# --------------------------------------------------------------------------- #

_SIGNED = {"google": {"thought_signature": "sig-abc"}}


def _signed_reply() -> dict[str, Any]:
    return {
        "id": "x",
        "choices": [{
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{}"},
                 "extra_content": _SIGNED},
                {"id": "call_2", "type": "function", "function": {"name": "lookup", "arguments": "{}"}},
            ]},
        }],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
    }


def _replayed(message: Any) -> list[dict[str, Any]]:
    """The assistant turn as the tool loops send it back (orchestrator.rounds)."""
    out: list[dict[str, Any]] = []
    for block in message.content:
        if block.type == "tool_use":
            out.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        elif (replay := translator.reasoning_replay_block(block)) is not None:
            out.append(replay)
    return out


async def test_a_signature_goes_back_on_the_call_that_carried_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _keys(monkeypatch)
    provider = _inner("google/gemini-3.8-flash")
    seen = _capture(provider, monkeypatch, _signed_reply())
    first = await provider.messages_create(
        model="google/gemini-3.8-flash", max_tokens=50, tools=_TOOL, messages=[{"role": "user", "content": "hi"}]
    )
    assert [b.type for b in first.content] == ["tool_use", "tool_use", translator.OPENROUTER_REASONING_BLOCK]
    await provider.messages_create(
        model="google/gemini-3.8-flash", max_tokens=50, tools=_TOOL,
        messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": _replayed(first)},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "a"},
                {"type": "tool_result", "tool_use_id": "call_2", "content": "b"},
            ]},
        ],
    )
    assistant = json.loads(seen[1].content)["messages"][1]
    assert assistant["tool_calls"][0]["extra_content"] == _SIGNED
    assert "extra_content" not in assistant["tool_calls"][1]
    assert "reasoning_details" not in assistant


def test_a_signature_never_reaches_openrouter_as_reasoning() -> None:
    block = {"type": translator.OPENROUTER_REASONING_BLOCK, "reasoning_details": [
        {"type": "reasoning.text", "text": "thought"},
        {"type": translator.TOOL_CALL_EXTRA, "id": "call_1", "extra_content": _SIGNED},
    ]}
    body = translator.to_openai_request("google/gemini-3.8-flash", {
        "max_tokens": 5,
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": "lookup", "input": {}}, block]},
        ],
    })
    assistant = body["messages"][1]
    assert assistant["reasoning_details"] == [{"type": "reasoning.text", "text": "thought"}]
    assert assistant["tool_calls"][0]["extra_content"] == _SIGNED


async def test_a_streamed_signature_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    acc = translator.StreamAccumulator()
    chunks = [
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": ""},
             "extra_content": _SIGNED},
        ]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]
    for chunk in chunks:
        acc.feed(chunk)
    final = acc.finalize()
    replay = translator.reasoning_replay_block(final.content[-1])
    assert replay is not None
    assert replay["reasoning_details"] == [{"type": translator.TOOL_CALL_EXTRA, "id": "call_1", "extra_content": _SIGNED}]


async def test_a_call_written_by_another_model_gets_googles_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    _keys(monkeypatch)
    provider = _inner("google/gemini-3.8-flash")
    seen = _capture(provider, monkeypatch)
    await provider.messages_create(
        model="google/gemini-3.8-flash", max_tokens=50, tools=_TOOL,
        messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "a"}]},
        ],
    )
    call = json.loads(seen[0].content)["messages"][1]["tool_calls"][0]
    assert call["extra_content"] == {"google": {"thought_signature": vendors.SKIP_SIGNATURE}}


async def test_xai_never_gets_a_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    _keys(monkeypatch)
    provider = _inner("x-ai/grok-4.6")
    seen = _capture(provider, monkeypatch)
    signed = {"type": translator.OPENROUTER_REASONING_BLOCK, "reasoning_details": [
        {"type": translator.TOOL_CALL_EXTRA, "id": "call_1", "extra_content": _SIGNED},
    ]}
    await provider.messages_create(
        model="x-ai/grok-4.6", max_tokens=50, tools=_TOOL,
        messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": "lookup", "input": {}}, signed]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "a"}]},
        ],
    )
    assistant = json.loads(seen[0].content)["messages"][1]
    assert "extra_content" not in assistant["tool_calls"][0] and "reasoning_details" not in assistant
