"""Azure backend: models a company runs in its own Azure account (Microsoft
Foundry, Azure OpenAI).

Only models listed in ``AZURE_AI_DEPLOYMENTS`` come here, each under the
name the rest of the code uses (``claude-sonnet-5-5``, ``openai/gpt-6-astra``,
``meta-llama/llama-4-maverick``) and sent to Azure as its deployment name.
Calls keep that name everywhere else (capability checks, tracing, usage), so
only the request itself says the deployment.

* Claude goes to Foundry's Anthropic endpoint (``<endpoint>/anthropic/``)
  through the Anthropic SDK's Foundry client, so prompt caching and thinking
  work as on Anthropic's own API. The web search tool is left out: it isn't
  offered on every Azure deployment.
* Every other model goes to the resource's OpenAI-compatible v1 endpoint
  (``<endpoint>/openai/v1``) through ``OpenAICompatibleProvider``.

Sign-in is the resource's key (the ``api-key`` header) or an Entra app
(``AZURE_AI_TENANT_ID`` / ``_CLIENT_ID`` / ``_CLIENT_SECRET``): a client
credentials token for each endpoint's audience (``AZURE_AI_TOKEN_SCOPE``
overrides both), cached until shortly before it expires.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import anthropic
import httpx

from openexecutive.providers.feature_gate import (
    NON_CLAUDE_MAX_OUTPUT_TOKENS,
    FeatureSpec,
    apply_feature_gates,
    fit_claude_generation,
    relax_forced_tool_choice,
)
from openexecutive.providers.openai_compatible import OpenAICompatibleProvider

LOGIN_BASE = "https://login.microsoftonline.com"
# The audience each endpoint takes an Entra token for: Foundry's Anthropic
# endpoint is an AI Foundry resource API, the OpenAI v1 one a Cognitive
# Services API. ``AZURE_AI_TOKEN_SCOPE`` sets one scope for both.
CLAUDE_TOKEN_SCOPE = "https://ai.azure.com/.default"
OPENAI_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"
# A token is fetched again this long before Azure says it expires.
_TOKEN_MARGIN_S = 300.0

AZURE_CLAUDE_SPEC = FeatureSpec(
    supports_cache_control=True,
    supports_thinking=True,
    supports_web_search=False,
    supports_tool_use=True,
)
AZURE_OPENAI_SPEC = FeatureSpec(
    supports_cache_control=False,
    supports_thinking=False,
    supports_web_search=False,
    supports_tool_use=True,
    supports_pdf_input=False,
    max_output_tokens=NON_CLAUDE_MAX_OUTPUT_TOKENS,
)


def endpoint_base(endpoint: str) -> str:
    """``https://contoso.services.ai.azure.com/`` (or a pasted
    ``…/openai/v1`` / ``…/anthropic`` URL) → ``https://contoso.services.ai.azure.com``."""
    base = endpoint.strip().rstrip("/")
    for suffix in ("/openai/v1", "/anthropic/v1", "/anthropic", "/openai"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base


def deployment_for(model: str, settings: Any) -> str | None:
    """The Azure deployment serving ``model``, or None when it isn't mapped."""
    deployments = getattr(settings, "azure_ai_deployments", None) or {}
    return deployments.get(model)


def azure_models(settings: Any) -> list[str]:
    """Every model mapped to an Azure deployment, in the order given."""
    return list((getattr(settings, "azure_ai_deployments", None) or {}).keys())


class EntraSignInError(RuntimeError):
    """Entra refused the app's sign-in. Carries only the status code."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"Entra sign-in failed with HTTP {status_code}")
        self.status_code = status_code


class EntraToken:
    """A client credentials token for one Entra app, fetched when first
    needed and again shortly before it expires."""

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        scope: str,
        http: httpx.AsyncClient | None = None,
        login_base: str = LOGIN_BASE,
    ) -> None:
        self._url = f"{login_base}/{tenant_id}/oauth2/v2.0/token"
        self._form = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        }
        self._http = http
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def cached(self) -> str | None:
        if self._token is not None and time.monotonic() < self._expires_at:
            return self._token
        return None

    async def get(self) -> str:
        if (token := self.cached()) is not None:
            return token
        async with self._lock:
            if (token := self.cached()) is not None:
                return token
            if self._http is not None:
                response = await self._http.post(self._url, data=self._form)
            else:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(self._url, data=self._form)
            if response.status_code >= 400:
                # The body can echo the request; only the status is kept.
                # The request is left off: its form holds the client secret.
                raise EntraSignInError(response.status_code)
            data = response.json()
            token = str(data["access_token"])
            lifetime = float(data.get("expires_in", 3600))
            self._token = token
            self._expires_at = time.monotonic() + max(lifetime - _TOKEN_MARGIN_S, 60.0)
            return token


def token_scope(settings: Any, default: str) -> str:
    """The scope to ask Entra for: ``AZURE_AI_TOKEN_SCOPE`` when set, else the
    endpoint's own (``CLAUDE_TOKEN_SCOPE`` / ``OPENAI_TOKEN_SCOPE``)."""
    return getattr(settings, "azure_ai_token_scope", None) or default


def entra_token(settings: Any, scope: str) -> EntraToken | None:
    """The Entra app sign-in for ``scope``, or None when the resource key is used."""
    if getattr(settings, "azure_ai_api_key", None):
        return None
    return EntraToken(
        tenant_id=str(settings.azure_ai_tenant_id),
        client_id=str(settings.azure_ai_client_id),
        client_secret=str(settings.azure_ai_client_secret),
        scope=scope,
    )


class _FoundryClient(anthropic.AsyncAnthropicFoundry):
    """The Foundry client, sending only Azure's own credential.

    The Anthropic client it is built on reads ``ANTHROPIC_API_KEY`` and
    ``ANTHROPIC_AUTH_TOKEN`` from the environment when it isn't given one and
    sends them on every request (``X-Api-Key`` / ``Authorization``). With an
    Entra app that would hand the Anthropic key to the Azure endpoint. Foundry
    adds its own ``api-key`` or bearer header per request, so the inherited
    ones are dropped."""

    @property
    def auth_headers(self) -> dict[str, str]:
        return {}

    def _validate_headers(self, headers: Any, custom_headers: Any) -> None:
        # Foundry's ``_prepare_options`` sets ``api-key`` or the bearer
        # token on every request, or refuses it.
        return None


class AzureClaudeProvider:
    """Claude on Foundry's Anthropic endpoint, addressed by deployment."""

    def __init__(
        self,
        *,
        endpoint: str,
        deployments: dict[str, str],
        api_key: str | None = None,
        token: EntraToken | None = None,
        timeout: float | None = None,
    ) -> None:
        if not api_key and token is None:
            raise ValueError("Azure needs a key or an Entra app")
        kwargs: dict[str, Any] = {"base_url": f"{endpoint_base(endpoint)}/anthropic/"}
        if api_key:
            kwargs["api_key"] = api_key
        else:
            assert token is not None
            kwargs["azure_ad_token_provider"] = token.get
        if timeout is not None:
            kwargs["timeout"] = timeout
        # A redirect must not carry the key headers to another host, or to
        # plain http: httpx strips only ``Authorization`` on one.
        kwargs["http_client"] = anthropic.DefaultAsyncHttpxClient(follow_redirects=False)
        client = _FoundryClient(**kwargs)
        if not api_key:
            client.api_key = None  # what the environment's ANTHROPIC_API_KEY put there
        client.auth_token = None
        # Headers set in ANTHROPIC_CUSTOM_HEADERS are meant for Anthropic.
        client._custom_headers = {}
        self._client = client
        self._deployments = deployments

    def _prepare(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        model = str(kwargs.get("model", ""))
        fitted = fit_claude_generation(model, relax_forced_tool_choice(model, kwargs))
        gated = apply_feature_gates(AZURE_CLAUDE_SPEC, fitted)
        return {**gated, "model": self._deployments.get(model, model)}

    def messages_create(self, **kwargs: Any) -> Awaitable[Any]:
        return self._client.messages.create(**self._prepare(kwargs))

    def messages_stream(self, **kwargs: Any) -> AbstractAsyncContextManager[Any]:
        return self._client.messages.stream(**self._prepare(kwargs))


class AzureOpenAIProvider(OpenAICompatibleProvider):
    """Every other mapped model, on the resource's OpenAI-compatible v1
    endpoint. OpenAI's own models there take the same request wording as on
    OpenAI's API; other makers' models take the plain one."""

    def __init__(
        self,
        *,
        endpoint: str,
        deployments: dict[str, str],
        api_key: str | None = None,
        token: EntraToken | None = None,
        timeout_s: float = 180.0,
    ) -> None:
        if not api_key and token is None:
            raise ValueError("Azure needs a key or an Entra app")
        self._deployments = deployments
        self._token = token
        # OpenAI's own models (reasoning ones) refuse ``max_tokens`` and a
        # non-default temperature, as on OpenAI's API.
        self._openai_deployments = {d for m, d in deployments.items() if m.startswith("openai/")}
        super().__init__(
            base_url=f"{endpoint_base(endpoint)}/openai/v1",
            # Never a bearer key: the resource key goes in Azure's own header.
            api_key=None,
            default_headers={"api-key": api_key} if api_key else None,
            timeout_s=timeout_s,
            model_resolver=self._resolve_deployment,
        )

    def _resolve_deployment(self, model: str) -> tuple[str, FeatureSpec] | None:
        deployment = self._deployments.get(model)
        if deployment is None:
            return None
        return deployment, AZURE_OPENAI_SPEC

    def _auth_headers(self) -> dict[str, str]:
        if self._token is None:
            return {}
        token = self._token.cached()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _extend_body(self, slug: str, body: dict[str, Any]) -> None:
        if slug in self._openai_deployments:
            max_tokens = body.pop("max_tokens", None)
            if max_tokens is not None:
                body["max_completion_tokens"] = max_tokens
            body.pop("temperature", None)
        # ``any`` is Anthropic/OpenRouter wording; the v1 API says "required".
        if body.get("tool_choice") == "any":
            body["tool_choice"] = "required"
        for message in body.get("messages") or []:
            if isinstance(message, dict):
                message.pop("reasoning_details", None)
        body.pop("reasoning", None)

    async def _messages_create(self, kwargs: dict[str, Any]) -> Any:
        if self._token is not None:
            await self._token.get()
        return await super()._messages_create(kwargs)

    def messages_stream(self, **kwargs: Any) -> AbstractAsyncContextManager[Any]:
        if self._token is None:
            return super().messages_stream(**kwargs)
        return self._stream_signed_in(kwargs)

    @asynccontextmanager
    async def _stream_signed_in(self, kwargs: dict[str, Any]) -> AsyncIterator[Any]:
        assert self._token is not None
        await self._token.get()  # so _auth_headers has a fresh token
        async with super().messages_stream(**kwargs) as stream:
            yield stream
