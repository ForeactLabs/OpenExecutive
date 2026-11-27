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
    r"(?<!gate )(?<!door )(?<!garage )(?<!alarm )(?<!lockbox )"
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
# A gate, door, garage, alarm, lockbox or wifi code, however it is worded,
# is something people text each other and ask about later: never dropped.
# The place word must belong to the code ("The gate entry code is 4821",
# "The code for the gate is 4821", "Wifi: HomeNet, password 84219375",
# "Enter 4821 at the gate"), never just appear somewhere in the text.
# A Wi-Fi "code" is usually a hotel or airport portal's one-time code, so
# only a Wi-Fi password counts.
_PLACE = r"(?:gates?|doors?|garage|alarm|lock ?box|keypad|wi-?fi|building|lobby)"
# The home code's own number, a few words on in the same sentence, goes
# with it ("Gate code for Saturday is 4821"), so
# nothing left behind pairs with a later "code" ("Text me if the code
# doesn't work").
_HOME_TAIL = r"(?:[^\d\n.!?]{0,25}?" + _NUMBER + r")?"
_HOME_CODE = re.compile(
    r"(?:\b(?:gates?|doors?|garage|alarm|lock ?box|keypad|building|lobby)(?:\s+(?:entry|access|opener|door|alarm))*"
    r"\s+(?:codes?|passcodes?|passwords?|pins?)\b"
    r"|\b(?:wi-?fi(?:\s+network)?|network)\s+(?:passwords?|pass|pw)\b"
    r"|\b(?:codes?|passcodes?|passwords?|pins?)\s+(?:for|to)\s+(?:the\s+|your\s+)?" + _PLACE + r"\b"
    r"|\b(?:wi-?fi|network|ssid)\s*(?::|is)\s*(?!(?:your|the|a|my)\b)\S+\s+(?:password|pass|pw)\b)" + _HOME_TAIL
    + r"|\b(?:codes?|passcodes?|pins?)\s+" + _NUMBER + r"\s+(?:for|to)\s+(?:the\s+|your\s+)?" + _PLACE + r"\b"
    r"|" + _NUMBER + r"\s+(?:at|on|into)\s+(?:the\s+)?" + _PLACE + r"\b",
    re.IGNORECASE,
)
# Words only a sign-in code has: with one of them, nothing keeps it
# ("Enter 482913 to confirm your Wi-Fi account").
_SIGN_IN_WORD = re.compile(
    r"\b(?:verification|confirmation code|(?:to|and) (?:confirm|verify)|verify your|sign[- ]?in|log[- ]?in|otp"
    r"|one[- ]time|2fa|two[- ]factor|authenticat\w*|security code|reset|recover\w*|(?:do not|don'?t|never) share)\b",
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
    if _SIGN_IN_LINK.search(words):
        return True
    # Only the home code itself is left out: "Your code is 482913. Building
    # code is 4821" still has a sign-in code in it.
    if not _SIGN_IN_WORD.search(words):
        words = _HOME_CODE.sub(" ", words)
    return bool(_ENTER_CODE.search(words) or _CODE_NEAR.search(words))
