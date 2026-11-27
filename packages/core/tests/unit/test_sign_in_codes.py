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
        "123 456 is your Instagram code. Don't share it.", "123456 is your Amazon OTP. Do not share it with anyone.",
        "4821 is your Uber code",
        "482913 is your Bank of America code", "123456 is your Wells Fargo SafePass code",
        "482913 is the code for your account", "482913 - WhatsApp code",
        "482913 is your verification code", "G-482913 is your Google verification code",
        "Your Google code is G-482913", "Use 482913 to verify your account",
        "Your DoorDash verification code is 482913", "Your PlayStation Network verification code is 482913", "Wi-Fi login code: 482913", "Your WiFi access code is 482913. Valid for 24 hours.", "Your Wi-Fi code is 482913", "Your code is 482913. Building code is 4821", "Xfinity WiFi: Your password reset code is 482913", "Your wifi password reset code is 482913", "PlayStation Network: your code is 482913", "Network Solutions code: 482913. Do not share it.", "Your Alarm.com code is 482913", "Enter 482913 to confirm your Wi-Fi account", "Your code is 482913. Never share it, not even with someone at your door",
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
        "Apt 1402, the code is on the fridge", "Room 1402, the password is on the fridge",
        "Suite 1200, the door code is on the sign", "Door code is 4821, see you at 7", "Room 1402, wifi password is on the fridge",
        "The code for the gate is 4821", "The gate entry code is 4821", "Garage door opener code is 4821", "Wifi: HomeNet, password 84219375", "Wifi is HomeNet, password 84219375", "Network: HomeNet Password: 84219375", "Gate access code is 4821", "Building access code is 4821", "Enter 4821 at the gate", "The network password is 84219375", "Network password: 84219375", "Gate code 4821, PIN 9999 for the alarm", "The code for the gate is 4821, can you confirm you got it?",
    ):
        assert not is_sign_in_code(text), text
