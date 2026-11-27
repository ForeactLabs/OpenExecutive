from openexecutive.delegation.sign_in_codes import is_sign_in_code


def test_codes_and_sign_in_links_are_found() -> None:
    for text in (
        "AT&T: Your verification code is 482913",
        "Enter 4 8 2 9 1 3 to confirm",
        "Your sign-in code: 123-456",
        "Use 991234 to log in",
        "Tap to sign in to your account",
        "Is this you signing in?",
    ):
        assert is_sign_in_code(text), text


def test_ordinary_texts_are_not() -> None:
    for text in ("Running late, there by 2:30", "Lunch Friday?", "Call me at 4", "Gate code changed", ""):
        assert not is_sign_in_code(text), text
