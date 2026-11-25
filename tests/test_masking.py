"""Phase 5B step 1: sensitive-data masking (section 3). Offline, no LLM."""
import json

import pytest

from app.masking import Masker, detect, iban_ok, luhn_ok
from app.schemas import _normalise


def mask(text):
    m = Masker()
    return m.mask(text, "scenario"), m


@pytest.mark.parametrize("text, kind", [
    ("mail ravi.k@example.co.in now", "EMAIL"),
    ("card 4111 1111 1111 1111", "CARD"),
    ("card 4111-1111-1111-1111", "CARD"),
    ("card 378282246310005", "CARD"),                      # 15-digit Amex test number
    ("IBAN GB82 WEST 1234 5698 7654 32", "IBAN"),
    ("IBAN DE89370400440532013000", "IBAN"),
    ("Aadhaar 2345 6789 0123", "AADHAAR"),
    ("Aadhaar 234567890123", "AADHAAR"),
    ("PAN ABCPE1234F", "PAN"),
    ("SSN 123-45-6789", "SSN"),
    ("account no: 0012345678901", "ACCOUNT"),
    ("a/c 123456789", "ACCOUNT"),
    ("Acct # 9876-5432-1098", "ACCOUNT"),
    ("call +91 98765 43210", "PHONE"),
    ("call 9876543210", "PHONE"),
    ("call (555) 123-4567", "PHONE"),
])
def test_each_type_is_masked(text, kind):
    out, m = mask(text)
    assert f"[{kind}_1]" in out and m.counts[kind] == 1


@pytest.mark.parametrize("text", [
    "card 4111-1111-1111-1112",                 # fails Luhn
    "order 1234567890123",                      # 13 digits, not Luhn-valid
    "IBAN GB00 WEST 1234 5698 7654 32",         # fails mod-97
    "SSN 000-12-3456",                          # invalid area
    "SSN 900-12-3456",
    "12-digit starting with 1: 123456789012",   # Aadhaar never starts with 0 or 1
    "PAN-like ABCDE1234F",                      # 4th letter D is not a PAN entity type
    "Loans above 500000 need a second approver.",
    "Respond within 2 seconds at 99.999% availability for 20,000 employees in 2026.",
    "Amount INR 1,00,000 and limit 5,00,00,000.",
    "Version 3.14.159 and ISO 27001 and port 8001.",
    "The system shall process 1200 transactions per second.",
])
def test_ordinary_numbers_and_invalid_values_are_not_masked(text):
    out, m = mask(text)
    assert out == text and m.report()["total"] == 0


def test_validators():
    assert luhn_ok("4111111111111111") and not luhn_ok("4111111111111112")
    assert iban_ok("GB82 WEST 1234 5698 7654 32") and not iban_ok("GB00WEST12345698765432")


def test_same_value_same_placeholder_across_texts_and_formats():
    m = Masker()
    a = m.mask("Card 4111 1111 1111 1111 and email A@B.co", "scenario")
    b = m.mask("Same card 4111-1111-1111-1111, email a@b.co, other card 5555 5555 5555 4444", "answers")
    assert "[CARD_1]" in a and "[CARD_1]" in b and "[CARD_2]" in b
    assert "[EMAIL_1]" in a and "[EMAIL_1]" in b
    assert m.report()["fields"] == ["answers", "scenario"]


def test_report_has_counts_and_label_but_no_values():
    m = Masker()
    m.mask("pay 4111 1111 1111 1111 from ravi@example.com", "scenario")
    r = m.report()
    assert r["counts"] == {"EMAIL": 1, "CARD": 1} and r["total"] == 2
    assert r["classification"] == "contains sensitive financial or identity data"
    blob = json.dumps(r) + json.dumps(m._placeholders) + json.dumps(m.counts)
    assert "4111" not in blob and "ravi" not in blob   # the masker never keeps originals


@pytest.mark.parametrize("text, label", [
    ("write to a@b.co", "contains personal contact data"),
    ("nothing sensitive here", "no sensitive data detected"),
    ("PAN ABCPE1234F", "contains sensitive financial or identity data"),
])
def test_classification_labels(text, label):
    _, m = mask(text)
    assert m.report()["classification"] == label


def test_mask_obj_walks_nested_values_but_not_keys():
    m = Masker()
    out = m.mask_obj({"a@b.co": ["call 9876543210", {"x": "ok"}], "n": 5}, "output")
    assert out == {"a@b.co": ["call [PHONE_1]", {"x": "ok"}], "n": 5}


def test_masking_is_idempotent():
    once, _ = mask("card 4111 1111 1111 1111, a@b.co, +91 98765 43210")
    twice, m2 = mask(once)
    assert twice == once and m2.report()["total"] == 0


def test_quote_grounding_works_on_masked_text():
    masked, _ = mask("Applicants with PAN ABCPE1234F must be verified; contact a@b.co for disputes.")
    quote = "with PAN [PAN_1] must be verified"
    assert _normalise(quote) in _normalise(masked)


def test_detect_returns_counts_only():
    assert detect("leak 4111 1111 1111 1111") == {"CARD": 1}
    assert detect("all clear, 12 apples") == {}
