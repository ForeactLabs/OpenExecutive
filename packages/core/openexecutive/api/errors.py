"""What a caller sees when something fails unexpectedly.

An unexpected exception's own text can name internal paths, hosts, settings
or the content being processed, so it never goes back to the caller or into
a record the caller reads. The route logs the exception with
``logger.exception`` and sends :func:`public_error` instead.

Errors raised on purpose with plain, input-free text (an ``HTTPException``
detail, a validation message) are not "unexpected" and keep their own text.
"""

from __future__ import annotations

GENERIC_FAILURE = "Something went wrong. Try again."


def public_error(exc: BaseException) -> str:
    """The text to show a caller for ``exc``. Never the exception's own text."""
    del exc  # the details belong in the log, not the response
    return GENERIC_FAILURE
