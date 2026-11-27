"""Sign-in codes: texts that are never kept or read.

A text like "Your code is 482913", "Enter 4 8 2 9 1 3 to confirm" or a
sign-in link lets whoever reads it into someone's account. The person's
texts (``delegation.texts``) drop them before they are kept, and the
browser extension (``packages/extension/texts.js``, ``T.isSignInCode``)
doesn't send them at all; the two use the same patterns.
"""
from __future__ import annotations

import re

# A code word with the code right after it: "code is 482913", "PIN: 1234",
# "passcode #5521". Only right after, so "dress code for the 2026 gala" isn't one.
_CODE_RIGHT_AFTER = re.compile(
    r"\b(?:codes?|passcodes?|passwords?|passwd|otp|pins?|tokens?)\b[\s:#-]*(?:(?:is|was)\b[\s:#-]*)?"
    r"(\d{4,10}|\d{3}[- ]\d{3}|(?:\d ){3,7}\d)(?!\d)",
    re.IGNORECASE,
)
# A sign-in word anywhere with a code-length number (5 or more digits, or
# 123-456, or 1 2 3 4 5 6): never a 4-digit year or time like 2026 or 1030.
_SIGN_IN_WORDS = re.compile(
    r"\b(one[- ]time|verification|verify|sign(?:ing)?[- ]?in|log(?:ging)?[- ]?in|2fa|mfa|authenticat\w*)\b",
    re.IGNORECASE,
)
_CODE_DIGITS = re.compile(r"(?<!\d)(\d{5,10}|\d{3}[- ]\d{3}|(?:\d ){3,7}\d)(?!\d)")
# A link or prompt that signs someone in, with no code at all.
_SIGN_IN_LINK = re.compile(
    r"\b(magic link|sign[- ]?in link|log[- ]?in link|is this you signing in|approve (?:this |the )?sign[- ]?in|"
    r"tap to (?:sign|log) in|trying to sign in)\b",
    re.IGNORECASE,
)
# "Enter 482913 to confirm": a number to type back somewhere (not a year).
_ENTER_CODE = re.compile(
    r"\b(?:enter|type)\s+((?!(?:19|20)\d\d(?!\d))\d{4,10}(?!\d)|(?:\d ){3,7}\d)",
    re.IGNORECASE,
)


def is_sign_in_code(text: str) -> bool:
    """Whether the words read like a sign-in code ("Your code is 482913",
    "Enter 4 8 2 9 1 3 to confirm") or a sign-in link or prompt."""
    words = text or ""
    return bool(
        _SIGN_IN_LINK.search(words)
        or _ENTER_CODE.search(words)
        or _CODE_RIGHT_AFTER.search(words)
        or (_SIGN_IN_WORDS.search(words) and _CODE_DIGITS.search(words))
    )
