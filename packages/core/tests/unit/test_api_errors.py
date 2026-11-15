from __future__ import annotations

from openexecutive.api.errors import GENERIC_FAILURE, public_error


def test_public_error_never_carries_the_exception_text() -> None:
    exc = RuntimeError("token=abc123 at /srv/data/secret.db")
    assert public_error(exc) == GENERIC_FAILURE
    assert "abc123" not in public_error(exc)
