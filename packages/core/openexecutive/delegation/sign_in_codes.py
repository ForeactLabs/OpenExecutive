"""Sign-in codes: texts that are never kept or read.

A text like "Your code is 482913", "Enter 4 8 2 9 1 3 to confirm" or a
sign-in link lets whoever reads it into someone's account. The person's
texts (``delegation.texts``) drop them before they are kept, and the
browser extension (``packages/extension/texts.js``, ``T.isSignInCode``)
doesn't send them at all; the two use the same patterns.
"""
from __future__ import annotations

import re

# A code: 4 to 8 digits, 123-456, or 1 2 3 4 5 6, maybe after a letter and
# a hyphen (Google's G-482913). Never part of a phone number (555-123-4567,
# 4155551234) and never a year (2026).
_NUMBER = r"(?<!\d)(?<!\d-)(?!(?:19|20)\d\d(?![\d-]))(?:\d{4,8}|\d{3}[- ]\d{3}|(?:\d ){3,7}\d)(?![\d-])"
# A code word, not a zip, area, postal, dress, promo or discount code, and
# not a gate, door, garage, alarm, lockbox or wifi one, which people text
# each other and ask about later ("what's the gate code Sam sent?").
_CODE_WORD = (
    r"(?<!zip )(?<!area )(?<!postal )(?<!dress )(?<!promo )(?<!discount )"
    r"(?<!gate )(?<!door )(?<!garage )(?<!alarm )(?<!lockbox )(?<!wifi )(?<!wi-fi )(?<!network )"
    r"\b(?:codes?|passcodes?|passwords?|passwd|otp|pins?)\b"
)
# The code close to its word, either way round: "code is 482913", "PIN: 4821",
# "482913 is your Bank of America code" (a number naming a place, never). A
# gate, door or wifi code is left
# out by its word (``_CODE_WORD``), so "Suite 1200, the door code is on the
# sign" isn't one.
# A number that names a place (apt 1402, suite 1200, room 12, #305) is not
# a code that comes before its word: "Apt 1402, the code is on the fridge".
_NOT_A_PLACE = (
    r"(?<!apt )(?<!apt\. )(?<!apartment )(?<!suite )(?<!ste )(?<!room )(?<!rm )(?<!unit )(?<!floor )"
    r"(?<!building )(?<!bldg )(?<!lot )(?<!box )(?<!#)(?<!no\. )"
)
_CODE_NEAR = re.compile(
    _CODE_WORD + r"[^\d\n]{0,25}" + _NUMBER + "|" + _NOT_A_PLACE + _NUMBER + r"[^\d\n]{0,40}" + _CODE_WORD,
    re.IGNORECASE,
)
# A link or prompt that signs someone in, with no code at all.
_SIGN_IN_LINK = re.compile(
    r"\b(magic link|sign[- ]?in link|log[- ]?in link|is this you signing in|approve (?:this |the )?sign[- ]?in|"
    r"tap to (?:sign|log) in|trying to sign in)\b",
    re.IGNORECASE,
)
# "Enter 482913 to confirm", "Use 482913 to verify your account": a number
# to type back somewhere.
_ENTER_CODE = re.compile(
    r"\b(?:enter|type)\s+" + _NUMBER
    + r"|\buse\s+" + _NUMBER + r"\s+(?:to|for)\s+(?:verify|confirm|sign|log|authenticat)",
    re.IGNORECASE,
)


def is_sign_in_code(text: str) -> bool:
    """Whether the words read like a sign-in code ("Your code is 482913",
    "Enter 4 8 2 9 1 3 to confirm") or a sign-in link or prompt."""
    words = text or ""
    return bool(
        _SIGN_IN_LINK.search(words)
        or _ENTER_CODE.search(words)
        or _CODE_NEAR.search(words)
    )
