"""Claude on a company's own Amazon Bedrock or Google Vertex AI.

Only models listed in ``BEDROCK_MODELS`` / ``VERTEX_MODELS`` come here, each
under the name the rest of the code uses (``claude-sonnet-5-5``) and sent to
the cloud as the id it serves the model under
(``global.anthropic.claude-sonnet-5-5-v1:0`` on Bedrock,
``claude-sonnet-5-5@20260301`` on Vertex). Calls keep that name everywhere
else (capability checks, tracing, usage); only the request says the cloud's.

Both go through the Anthropic SDK's own Bedrock and Vertex clients, so
prompt caching and thinking work as on Anthropic's API. The web search tool
is left out: not every region or account offers it.

* Bedrock signs in with a Bedrock API key (``BEDROCK_API_KEY``), an IAM
  access key pair, or, with neither, the machine's own AWS role.
* Vertex signs in with a service account's JSON key
  (``VERTEX_CREDENTIALS_JSON``) or, without one, the machine's Google
  credentials.

Neither client follows redirects, which would carry the credential headers
to another host.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable
from contextlib import AbstractAsyncContextManager
from typing import Any

import anthropic

from openexecutive.providers.feature_gate import (
    FeatureSpec,
    apply_feature_gates,
    fit_claude_generation,
    relax_forced_tool_choice,
)

CLOUD_CLAUDE_SPEC = FeatureSpec(
    supports_cache_control=True,
    supports_thinking=True,
    supports_web_search=False,
    supports_tool_use=True,
)
VERTEX_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def bedrock_id_for(model: str, settings: Any) -> str | None:
    return (getattr(settings, "bedrock_models", None) or {}).get(model)


def vertex_id_for(model: str, settings: Any) -> str | None:
    return (getattr(settings, "vertex_models", None) or {}).get(model)


def cloud_models(settings: Any) -> list[str]:
    """Every model mapped to Bedrock or Vertex, in the order given."""
    out = list((getattr(settings, "bedrock_models", None) or {}).keys())
    out.extend(m for m in (getattr(settings, "vertex_models", None) or {}) if m not in out)
    return out


class _MappedClaude:
    """Claude on a cloud client, addressed by the cloud's model id."""

    _client: Any

    def __init__(self, ids: dict[str, str]) -> None:
        self._ids = ids

    def _prepare(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        model = str(kwargs.get("model", ""))
        fitted = fit_claude_generation(model, relax_forced_tool_choice(model, kwargs))
        gated = apply_feature_gates(CLOUD_CLAUDE_SPEC, fitted)
        return {**gated, "model": self._ids.get(model, model)}

    def messages_create(self, **kwargs: Any) -> Awaitable[Any]:
        return self._client.messages.create(**self._prepare(kwargs))

    def messages_stream(self, **kwargs: Any) -> AbstractAsyncContextManager[Any]:
        return self._client.messages.stream(**self._prepare(kwargs))


class BedrockClaudeProvider(_MappedClaude):
    def __init__(
        self,
        *,
        region: str,
        models: dict[str, str],
        api_key: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        timeout: float | None = None,
    ) -> None:
        super().__init__(models)
        kwargs: dict[str, Any] = {
            "aws_region": region,
            "http_client": anthropic.DefaultAsyncHttpxClient(follow_redirects=False),
        }
        if api_key:
            kwargs["api_key"] = api_key
        elif access_key_id and secret_access_key:
            kwargs["aws_access_key"] = access_key_id
            kwargs["aws_secret_key"] = secret_access_key
        if timeout is not None:
            kwargs["timeout"] = timeout
        self._client = anthropic.AsyncAnthropicBedrock(**kwargs)


def vertex_credentials(raw: str | None) -> Any:
    """A service account's JSON key → Google credentials, or None for the
    machine's own. Only the error's type is kept: the key is in the input."""
    if not raw:
        return None
    from google.oauth2 import service_account

    try:
        info = json.loads(raw)
        return service_account.Credentials.from_service_account_info(info, scopes=[VERTEX_SCOPE])
    except Exception as exc:  # noqa: BLE001 - the message can quote the key
        raise ValueError(f"VERTEX_CREDENTIALS_JSON isn't a service account key ({type(exc).__name__})") from None


class VertexClaudeProvider(_MappedClaude):
    def __init__(
        self,
        *,
        project_id: str,
        region: str,
        models: dict[str, str],
        credentials: Any = None,
        timeout: float | None = None,
    ) -> None:
        super().__init__(models)
        kwargs: dict[str, Any] = {
            "project_id": project_id,
            "region": region,
            "http_client": anthropic.DefaultAsyncHttpxClient(follow_redirects=False),
        }
        if credentials is not None:
            kwargs["credentials"] = credentials
        if timeout is not None:
            kwargs["timeout"] = timeout
        self._client = anthropic.AsyncAnthropicVertex(**kwargs)
