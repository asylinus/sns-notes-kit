"""Pattern detection / masking for phones, emails, bank-account-like numbers, @mentions."""
import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(
    r"(?<![\d-])(?:\+\d{1,3}[ -]?\d{1,2}|0\d{1,2})[ .-]?\d{3,4}[ .-]?\d{4}(?![\d-])"
)
ACCOUNT = re.compile(
    r"(?<![\d-])\d{3,6}-\d{2,6}-\d{4,8}(?:-\d{1,3})?(?![\d-])|(?<!\d)\d{11,14}(?!\d)"
)
MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9_][A-Za-z0-9._]{0,29})")

# order matters: email first, then phone, then account (so one number is counted once)
_RULES = (("email", EMAIL), ("phone", PHONE), ("account", ACCOUNT))
MASKS = {"email": "[REDACTED-EMAIL]", "phone": "[REDACTED-PHONE]", "account": "[REDACTED-ACCOUNT]"}


def redact(text: str) -> str:
    for name, rx in _RULES:
        text = rx.sub(MASKS[name], text)
    return text


def scan_text(text: str, own_account: str = "") -> dict[str, int]:
    """Return counts per category for one text. Values are never returned."""
    counts = {"phone": 0, "email": 0, "account": 0, "mention": 0}
    for name, rx in _RULES:
        counts[name] += len(rx.findall(text))
        text = rx.sub(" ", text)
    own = own_account.lower().lstrip("@")
    for m in MENTION.finditer(text):
        handle = m.group(1).rstrip(".").lower()
        if handle and handle != own:
            counts["mention"] += 1
    return counts
