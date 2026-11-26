"""Google and xAI on their own keys: Gemini and Grok without OpenRouter.

With ``GEMINI_API_KEY`` set, every ``google/…`` model goes to Google's
OpenAI-compatible endpoint; with ``XAI_API_KEY``, every ``x-ai/…`` model goes
to xAI's. The model keeps its OpenRouter-style name everywhere else (the
Council, capability checks, tracing, usage); only the request carries the
vendor's own id, the part after the slash.

Both run on ``OpenAICompatibleProvider`` with the plain tool-calling spec: no
prompt-cache markers, web search or PDFs. Deep reasoning becomes the
vendor's ``reasoning_effort`` where it takes one (Gemini). Gemini also signs
its tool calls (a ``thought_signature`` in each call's ``extra_content``) and
refuses the next turn without it; the translator carries it through the
reasoning block every tool loop already replays, and a call from history
that never had one (a turn written by another model) gets Google's
documented placeholder so the conversation can move to Gemini mid-way.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openexecutive.providers.feature_gate import NON_CLAUDE_MAX_OUTPUT_TOKENS, FeatureSpec
from openexecutive.providers.openai_compatible import OpenAICompatibleProvider

GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
XAI_BASE_URL = "https://api.x.ai/v1"

# Google's value for a function call that has no signature of its own.
# https://ai.google.dev/gemini-api/docs/thought-signatures
SKIP_SIGNATURE = "skip_thought_signature_validator"


@dataclass(frozen=True)
class Vendor:
    name: str  # "google" / "xai": the provider name tracing and usage see
    prefix: str  # the model-name prefix it serves
    key_setting: str
    models_setting: str


GOOGLE = Vendor("google", "google/", "gemini_api_key", "gemini_models")
XAI = Vendor("xai", "x-ai/", "xai_api_key", "xai_models")
VENDORS: tuple[Vendor, ...] = (GOOGLE, XAI)


def vendor_for(model: str, settings: Any) -> Vendor | None:
    """The vendor whose own key serves ``model``, or None."""
    for vendor in VENDORS:
        if model.startswith(vendor.prefix) and getattr(settings, vendor.key_setting, None):
            return vendor
    return None


def vendor_models(settings: Any) -> list[str]:
    """The models listed for each vendor whose key is set."""
    out: list[str] = []
    for vendor in VENDORS:
        if getattr(settings, vendor.key_setting, None):
            out.extend(getattr(settings, vendor.models_setting, None) or [])
    return out


GOOGLE_SPEC = FeatureSpec(
    supports_cache_control=False,
    supports_thinking=True,
    supports_web_search=False,
    supports_tool_use=True,
    supports_pdf_input=False,
    max_output_tokens=NON_CLAUDE_MAX_OUTPUT_TOKENS,
)
XAI_SPEC = FeatureSpec(
    supports_cache_control=False,
    supports_thinking=False,
    supports_web_search=False,
    supports_tool_use=True,
    supports_pdf_input=False,
    max_output_tokens=NON_CLAUDE_MAX_OUTPUT_TOKENS,
)


class _VendorProvider(OpenAICompatibleProvider):
    vendor: Vendor
    spec: FeatureSpec

    def __init__(self, *, base_url: str, api_key: str, timeout_s: float = 180.0) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            timeout_s=timeout_s,
            model_resolver=self._resolve_model,
        )

    def _resolve_model(self, model: str) -> tuple[str, FeatureSpec] | None:
        if not model.startswith(self.vendor.prefix):
            return None
        return model[len(self.vendor.prefix):], self.spec

    def _extend_body(self, slug: str, body: dict[str, Any]) -> None:
        # ``any`` is Anthropic/OpenRouter wording; these APIs say "required".
        if body.get("tool_choice") == "any":
            body["tool_choice"] = "required"
        for message in body.get("messages") or []:
            if isinstance(message, dict):
                message.pop("reasoning_details", None)


class GoogleProvider(_VendorProvider):
    """Gemini on Google's OpenAI-compatible endpoint."""

    vendor = GOOGLE
    spec = GOOGLE_SPEC

    def _extend_body(self, slug: str, body: dict[str, Any]) -> None:
        super()._extend_body(slug, body)
        reasoning = body.pop("reasoning", None)
        if isinstance(reasoning, dict):
            effort = reasoning.get("effort")
            if reasoning.get("enabled") is False:
                body["reasoning_effort"] = "none"
            elif isinstance(effort, str) and effort in ("low", "medium", "high"):
                body["reasoning_effort"] = effort
            elif "max_tokens" in reasoning or effort is not None:
                body["reasoning_effort"] = "medium"
        for message in body.get("messages") or []:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            calls = message.get("tool_calls")
            if not isinstance(calls, list) or not calls:
                continue
            # Gemini signs the first call of a turn; one with no signature
            # at all was written by another model.
            if not any(isinstance(c, dict) and c.get("extra_content") for c in calls):
                calls[0]["extra_content"] = {"google": {"thought_signature": SKIP_SIGNATURE}}


class XAIProvider(_VendorProvider):
    """Grok on xAI's OpenAI-compatible endpoint."""

    vendor = XAI
    spec = XAI_SPEC

    def _extend_body(self, slug: str, body: dict[str, Any]) -> None:
        super()._extend_body(slug, body)
        # Grok's reasoning models reason on their own and refuse a setting.
        body.pop("reasoning", None)
        for message in body.get("messages") or []:
            if isinstance(message, dict):
                for call in message.get("tool_calls") or []:
                    if isinstance(call, dict):
                        call.pop("extra_content", None)
