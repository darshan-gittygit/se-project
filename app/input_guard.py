"""
One guard per API request: every user-supplied text goes through it before
any model call or storage.

- the prompt-injection filter runs on the text as received; a match raises
  InjectionBlocked (400) and writes an audit event naming the field and the
  pattern - never the text;
- the text is then masked (app/masking.py) with one Masker per request, so a
  value gets the same placeholder in every field;
- report() is the masking report returned to the caller (counts and a
  classification, no values).

Uploaded documents (section 3): JSON {filename, text}, only .txt/.md/.csv,
at most MAX_DOCUMENT_CHARS characters each and MAX_DOCUMENTS per request.
"""
import os

from app.audit import append_event
from app.masking import Masker
from app.prompt_safety import injection_pattern

ALLOWED_EXTENSIONS = (".txt", ".md", ".csv")
MAX_DOCUMENT_CHARS = 20_000
MAX_DOCUMENTS = 5
MAX_FILENAME_CHARS = 120


class InjectionBlocked(Exception):
    def __init__(self, field: str, pattern: str):
        self.field, self.pattern = field, pattern
        super().__init__(f"Input rejected by the prompt-injection filter: field {field!r} matched pattern {pattern!r}. "
                         "Rephrase the text; instructions addressed to the AI are not accepted as input.")


class DocumentRejected(Exception):
    """Upload not allowed (400)."""


class Guard:
    def __init__(self, role: str, run_id: str | None = None, endpoint: str = ""):
        self.role, self.run_id, self.endpoint = role, run_id, endpoint
        self.masker = Masker()

    def text(self, field: str, value: str | None) -> str | None:
        if value is None:
            return None
        pattern = injection_pattern(value)
        if pattern:
            append_event("injection_blocked", self.role, run_id=self.run_id,
                         details={"endpoint": self.endpoint, "field": field, "pattern": pattern})
            raise InjectionBlocked(field, pattern)
        return self.masker.mask(value, field)

    def obj(self, field: str, value):
        """Checks and masks every string inside a dict/list."""
        if isinstance(value, str):
            return self.text(field, value)
        if isinstance(value, list):
            return [self.obj(field, v) for v in value]
        if isinstance(value, dict):
            return {k: self.obj(field, v) for k, v in value.items()}
        return value

    def output(self, value):
        """Masks model output before it is stored, logged or returned (no injection check: it is not input)."""
        return self.masker.mask_obj(value, "output")

    def answers(self, answers: list[dict]) -> list[dict]:
        return [{**a, "question": self.text(f"answers[{i}].question", a["question"]),
                 "answer": self.text(f"answers[{i}].answer", a["answer"])} for i, a in enumerate(answers)]

    def documents(self, documents: list[dict]) -> list[dict]:
        if len(documents) > MAX_DOCUMENTS:
            raise DocumentRejected(f"At most {MAX_DOCUMENTS} documents per request.")
        out = []
        for i, d in enumerate(documents):
            name = (d.get("filename") or "").strip()
            if (not name or len(name) > MAX_FILENAME_CHARS or os.path.basename(name) != name or "\\" in name
                    or name.startswith(".") or any(ord(c) < 32 for c in name)):
                raise DocumentRejected(f"documents[{i}]: give a plain file name (no folders), at most {MAX_FILENAME_CHARS} characters.")
            if not name.lower().endswith(ALLOWED_EXTENSIONS):
                raise DocumentRejected(f"documents[{i}]: only {', '.join(ALLOWED_EXTENSIONS)} files are accepted.")
            text = d.get("text") or ""
            if not text.strip():
                raise DocumentRejected(f"documents[{i}] ({name}) is empty.")
            if len(text) > MAX_DOCUMENT_CHARS:
                raise DocumentRejected(f"documents[{i}] ({name}) is too long ({len(text)} characters; "
                                       f"the limit is {MAX_DOCUMENT_CHARS}).")
            out.append({"filename": name, "text": self.text(f"documents[{i}]", text)})
        return out

    def report(self) -> dict:
        return self.masker.report()
