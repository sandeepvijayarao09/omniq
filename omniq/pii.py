"""PII tokenization: sensitive values become [KIND_n] tokens before any model call.

The vault lives only for the request (or conversation). A guard re-scans the
final outbound payload and refuses the call if any raw value survived.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("PHONE", re.compile(r"(?:\+\d{1,2}\s?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("CARD", re.compile(r"\b(?:\d{4}[ -]){3}\d{4}\b")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}\b")),
    ("ACCOUNT", re.compile(r"\b(?:acct|account|routing)\s*(?:no\.?|number|#)?\s*:?\s*\d{6,17}\b", re.I)),
]


class Names:
    """Registry of known people (directory + CRM contacts) for name detection."""

    def __init__(self, names: list[str]):
        full = sorted({n for n in names if n}, key=len, reverse=True)
        self.full = full
        self._re = re.compile(r"\b(" + "|".join(re.escape(n) for n in full) + r")\b") if full else None

    def find(self, text: str) -> list[str]:
        return self._re.findall(text) if self._re else []


@dataclass
class Vault:
    names: Names
    forward: dict[str, str] = field(default_factory=dict)  # raw -> token
    counters: dict[str, int] = field(default_factory=dict)

    def _token(self, kind: str, raw: str) -> str:
        if raw not in self.forward:
            self.counters[kind] = self.counters.get(kind, 0) + 1
            self.forward[raw] = f"[{kind}_{self.counters[kind]}]"
        return self.forward[raw]

    def tokenize(self, text: str) -> str:
        for kind, pat in PATTERNS:
            text = pat.sub(lambda m, k=kind: self._token(k, m.group(0)), text)
        if self.names._re:
            text = self.names._re.sub(lambda m: self._token("PERSON", m.group(0)), text)
            # first names of already-tokenized people, e.g. "Maya" after "Maya Chen"
            for raw, tok in list(self.forward.items()):
                if tok.startswith("[PERSON_") and " " in raw:
                    first = raw.split()[0]
                    text = re.sub(rf"\b{re.escape(first)}\b", tok, text)
        return text

    def detokenize(self, text: str) -> str:
        for raw, tok in sorted(self.forward.items(), key=lambda kv: -len(kv[1])):
            text = text.replace(tok, raw)
        return text

    def leaks(self, payload: str) -> list[str]:
        """Raw sensitive values still present in an outbound payload."""
        found = [m.group(0) for _, pat in PATTERNS for m in pat.finditer(payload)]
        found += self.names.find(payload)
        return found

    def summary(self) -> dict[str, int]:
        return dict(self.counters)
