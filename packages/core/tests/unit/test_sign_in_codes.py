from openexecutive.delegation.sign_in_codes import is_sign_in_code


def test_codes_and_sign_in_links_are_found() -> None:
    for text in (
        "AT&T: Your verification code is 482913",
        "Enter 4 8 2 9 1 3 to confirm",
        "Your sign-in code: 123-456",
        "Your Google verification code for sign-in: 991234",
        "Tap to sign in to your account",
        "Is this you signing in?",
        "Your PIN: 4821",
        "Your code is 1234",
        "482913 is your verification code", "G-482913 is your Google verification code",
        "Your Google code is G-482913", "Use 482913 to verify your account",
    ):
        assert is_sign_in_code(text), text


def test_ordinary_texts_are_not() -> None:
    for text in (
        "Running late, there by 2:30", "Lunch Friday?", "Call me at 4", "Gate code changed", "",
        "Can we use 2000 from the budget?", "Dress code for the 2026 gala is black tie",
        "Login meeting moved to 1030", "Sign in sheet for the 2026 offsite", "Enter 2026 in the year box",
        "Can't log in to the portal, call me at 555-123-4567", "Please verify the invoice total, it is 12500",
        "zip code 94103", "Can you verify the address? 1600 Amphitheatre Pkwy, 94043",
        "Login meeting moved, dial 4155551234",
        "Gate code is 4821", "Door code 1234#", "The wifi password is 84219375",
    ):
        assert not is_sign_in_code(text), text
