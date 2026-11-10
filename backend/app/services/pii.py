"""Redact personal data from stored transcripts and summaries (tenant setting)."""
import re

PATTERNS = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("emirates_id", re.compile(r"\b784[-\s]?\d{4}[-\s]?\d{7}[-\s]?\d\b")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b")),
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("aadhaar", re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b")),
    ("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("phone", re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d")),
]
# Numbers said as words ("nine eight seven six ...") - 6+ digit words in a row
_WORDS = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|double|triple)"
SPOKEN = re.compile(rf"\b{_WORDS}(?:[\s,-]+{_WORDS}){{5,}}\b", re.I)


def _luhn(s: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", s)]
    if not 13 <= len(d) <= 19:
        return False
    total = 0
    for i, n in enumerate(reversed(d)):
        if i % 2:
            n *= 2
            n = n - 9 if n > 9 else n
        total += n
    return total % 10 == 0


def redact(text: str | None) -> str:
    if not text:
        return text or ""
    for kind, rx in PATTERNS:
        if kind == "card":
            text = rx.sub(lambda m: "[card]" if _luhn(m.group()) else m.group(), text)
        else:
            text = rx.sub(f"[{kind}]", text)
    return SPOKEN.sub("[number]", text)
