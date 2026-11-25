"""
Sensitive-data masking (problem statement section 3: "Sensitive information
should be classified, masked, and protected before being submitted to an
LLM"). Standard library only.

Detected, in this order (earlier, more specific detectors win):
  EMAIL     email addresses
  IBAN      IBANs that pass the ISO 13616 mod-97 check
  ACCOUNT   account numbers next to a label ("account no", "a/c", "acct #")
  CARD      13-19 digit card numbers that pass the Luhn check
  SSN       US SSN-style numbers written 123-45-6789 (invalid areas skipped)
  AADHAAR   12-digit numbers starting 2-9, plain or grouped 4-4-4 (format
            only - Aadhaar's own checksum is not applied, so any number of
            that shape is masked: privacy first)
  PAN       Indian PAN-style ids: 5 letters (4th = PAN entity type), 4 digits, 1 letter
  PHONE     numbers with a country code (+91 ...), Indian 10-digit mobiles
            (6-9 first digit), or US formats (555) 123-4567 / 555-123-4567

Each value is replaced by a stable placeholder - the same value gets the same
placeholder everywhere in one request - such as [EMAIL_1] or [CARD_2]. The
placeholders are keyed by a SHA-256 of the normalised value, so the masker
never keeps the original text, and nothing returns or logs it. report() gives
counts by type and a classification label, with no raw values.

Limits: pattern matching cannot recognise names, addresses or free-text
identifiers, and unusual formats will be missed.
"""
import hashlib
import re

FINANCIAL_OR_IDENTITY = ("IBAN", "ACCOUNT", "CARD", "SSN", "AADHAAR", "PAN")
CONTACT = ("EMAIL", "PHONE")
TYPES = ("EMAIL", "IBAN", "ACCOUNT", "CARD", "SSN", "AADHAAR", "PAN", "PHONE")


def luhn_ok(digits: str) -> bool:
    total, double = 0, False
    for d in reversed(digits):
        n = int(d)
        if double:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
        double = not double
    return total % 10 == 0


def iban_ok(raw: str) -> bool:
    s = re.sub(r"\s", "", raw).upper()
    if not 15 <= len(s) <= 34:
        return False
    rearranged = s[4:] + s[:4]
    numeric = "".join(str(int(c, 36)) for c in rearranged)
    return int(numeric) % 97 == 1


def ssn_ok(raw: str) -> bool:
    area, group, serial = raw.split("-")
    return area not in ("000", "666") and not area.startswith("9") and group != "00" and serial != "0000"


# (type, compiled regex, group holding the value, validator or None)
_DETECTORS = [
    ("EMAIL", re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})(?![\w-])"), 1, None),
    ("IBAN", re.compile(r"(?<![A-Za-z0-9])([A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30})(?![A-Za-z0-9])"), 1, iban_ok),
    ("ACCOUNT", re.compile(r"(?i)\b(?:account|acct|a/c)\.?\s*(?:no\.?|num(?:ber)?|#)?\s*[:#-]?\s*(\d(?:[ -]?\d){5,19})(?!\d)"), 1, None),
    ("CARD", re.compile(r"(?<![\d-])(\d(?:[ -]?\d){12,18})(?![\d-])"), 1, lambda v: luhn_ok(re.sub(r"\D", "", v))),
    ("SSN", re.compile(r"(?<![\d-])(\d{3}-\d{2}-\d{4})(?![\d-])"), 1, ssn_ok),
    ("AADHAAR", re.compile(r"(?<![\d-])([2-9]\d{3}(?:[ -]?)\d{4}(?:[ -]?)\d{4})(?![\d-])"), 1,
     lambda v: len(re.sub(r"\D", "", v)) == 12),
    ("PAN", re.compile(r"(?<![A-Za-z0-9])([A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z])(?![A-Za-z0-9])"), 1, None),
    ("PHONE", re.compile(
        r"(?<![\w+])((?:\+\d{1,3}[\s-]?\d[\d\s-]{6,13}\d)"          # +91 98765 43210, +1 555 123 4567
        r"|(?:0?[6-9]\d{9})"                                         # Indian mobile, optional leading 0
        r"|(?:\(\d{3}\)\s?\d{3}-\d{4})|(?:\d{3}-\d{3}-\d{4}))(?![\w-])"), 1,
     lambda v: 9 <= len(re.sub(r"\D", "", v)) <= 15),
]


def _key(kind: str, value: str) -> str:
    norm = value.lower() if kind == "EMAIL" else re.sub(r"[^0-9A-Za-z]", "", value).upper()
    return hashlib.sha256(f"{kind}:{norm}".encode("utf-8")).hexdigest()


class Masker:
    """One per request, so the same value gets the same placeholder across every text in it."""

    def __init__(self):
        self._placeholders: dict[str, str] = {}   # sha256 key -> placeholder; never the value
        self.counts: dict[str, int] = {t: 0 for t in TYPES}
        self.fields: set[str] = set()

    def _placeholder(self, kind: str, value: str) -> str:
        key = _key(kind, value)
        if key not in self._placeholders:
            n = sum(1 for p in self._placeholders.values() if p.startswith(f"[{kind}_")) + 1
            self._placeholders[key] = f"[{kind}_{n}]"
        return self._placeholders[key]

    def mask(self, text: str, field: str = "") -> str:
        if not text:
            return text
        for kind, rx, group, valid in _DETECTORS:
            def repl(m, kind=kind, group=group, valid=valid):
                value = m.group(group)
                if valid and not valid(value):
                    return m.group(0)
                self.counts[kind] += 1
                if field:
                    self.fields.add(field)
                start, end = m.span(group)
                whole_start = m.start(0)
                return m.group(0)[:start - whole_start] + self._placeholder(kind, value) + m.group(0)[end - whole_start:]
            text = rx.sub(repl, text)
        return text

    def mask_obj(self, obj, field: str = ""):
        """Masks every string inside dicts/lists (keys are left alone)."""
        if isinstance(obj, str):
            return self.mask(obj, field)
        if isinstance(obj, list):
            return [self.mask_obj(x, field) for x in obj]
        if isinstance(obj, dict):
            return {k: self.mask_obj(v, field) for k, v in obj.items()}
        return obj

    def report(self) -> dict:
        found = [t for t in TYPES if self.counts[t]]
        if any(t in FINANCIAL_OR_IDENTITY for t in found):
            label = "contains sensitive financial or identity data"
        elif found:
            label = "contains personal contact data"
        else:
            label = "no sensitive data detected"
        return {"counts": {t: c for t, c in self.counts.items() if c}, "total": sum(self.counts.values()),
                "classification": label, "fields": sorted(self.fields),
                "note": "Values were replaced by placeholders before any model call or storage; originals are not kept."}


def detect(text: str) -> dict[str, int]:
    """Counts of sensitive values in text (no values returned). Used to check LLM output."""
    m = Masker()
    m.mask(text)
    return {t: c for t, c in m.counts.items() if c}
