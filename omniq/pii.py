"""PII tokenization: sensitive values become [KIND_n] tokens before any model call.

Every string about to leave for a model passes through a ``Vault``. Sensitive spans are replaced with synthetic
tokens such as ``[PERSON_1]``; the mapping back lives only in the vault, inside our boundary. The model reasons over
tokens and the answer is de-tokenized for the (already authorized) employee. A guard re-scans the final outbound
payload with the same detectors and the call is refused if any raw value survived.

Detection is deterministic: regexes with validators (Luhn for cards, IBAN mod-97) for structured identifiers, plus
org registries (the directory and CRM contacts, the customer list, project codenames) for the names that matter most
to a company. Fourteen categories:

  PERSON, EMAIL, PHONE, NATIONAL_ID (SSN, passport, driver's licence), CREDIT_CARD, BANK_ACCOUNT (IBAN, account and
  routing numbers), MONEY, IP_ADDRESS, HEALTH (medical record numbers, ICD-10 codes), CUSTOMER, PROJECT, SECRET
  (API keys, tokens, passwords, private keys), ADDRESS (street addresses), DATE_OF_BIRTH

A conversation keeps one vault across turns (``to_json`` / ``from_json``), so ``[PERSON_1]`` means the same person
in turn 1 and turn 7. The vault is stored with the conversation, which already holds the raw turns.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class Category(str, Enum):
    PERSON = "PERSON"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    NATIONAL_ID = "NATIONAL_ID"
    CREDIT_CARD = "CREDIT_CARD"
    BANK_ACCOUNT = "BANK_ACCOUNT"
    MONEY = "MONEY"
    IP_ADDRESS = "IP_ADDRESS"
    HEALTH = "HEALTH"
    CUSTOMER = "CUSTOMER"
    PROJECT = "PROJECT"
    SECRET = "SECRET"
    ADDRESS = "ADDRESS"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"


ALL_CATEGORIES: frozenset[Category] = frozenset(Category)

# Ordered so that more specific patterns win overlaps. A pattern may expose a named group ``v`` (or ``v2``, ``v3``);
# only that group is tokenized, which keeps labels such as "SSN:" or "DOB" readable for the model.
_PATTERNS: list[tuple[Category, re.Pattern[str]]] = [
    (Category.SECRET, re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"
        r"|\b(?:sk-(?:ant-)?[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}"
        r"|xox[baprs]-[A-Za-z0-9\-]{10,})\b"
        r"|(?i:\b(?:password|passwd|pwd|secret|api[_ ]?key|token)\b\s*[:=]\s*)(?P<v>\S{5,}[^\s.,;:!?)])"
    )),
    (Category.EMAIL, re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    (Category.BANK_ACCOUNT, re.compile(
        r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b"
        r"|(?i:\b(?:account|acct|routing|aba)\s*(?:no\.?|number|#)?\s*[:#]?\s*)(?P<v>\d{6,17})\b"
    )),
    (Category.CREDIT_CARD, re.compile(r"\b(?:\d[ \-]?){12,18}\d\b")),
    (Category.NATIONAL_ID, re.compile(
        r"\b\d{3}-\d{2}-\d{4}\b"
        r"|(?i:\b(?:passport|driver'?s? licen[cs]e|licen[cs]e no\.?|DL)\s*(?:no\.?|number|#)?\s*[:#]?\s*)"
        r"(?P<v>[A-Z0-9]{6,12})\b"
    )),
    (Category.HEALTH, re.compile(
        r"(?i:\b(?:MRN|medical record(?: number)?|patient id|member id)\s*[:#]?\s*)(?P<v>[A-Z0-9\-]{5,15})\b"
        r"|\b[A-TV-Z]\d{2}\.\d{1,4}\b"  # ICD-10 with a decimal, e.g. F41.1
    )),
    (Category.DATE_OF_BIRTH, re.compile(
        r"(?i:\b(?:DOB|date of birth|born(?: on)?)\s*[:\-]?\s*)"
        r"(?P<v>\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}"
        r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2},? \d{4})"
    )),
    (Category.IP_ADDRESS, re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
        r"|\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"
    )),
    (Category.PHONE, re.compile(
        r"(?<![\w.])(?:\+\d{1,3}[ .\-]?)?(?:\(\d{3}\)|\d{3})[ .\-]\d{3}[ .\-]\d{4}\b"
    )),
    (Category.ADDRESS, re.compile(
        r"\b\d{1,5} (?:[A-Z][a-z]+ ){1,3}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr"
        r"|Way|Court|Ct|Place|Pl|Terrace|Parkway)\b\.?(?:,? (?:Apt|Suite|Unit) \w+)?"
    )),
    (Category.MONEY, re.compile(
        r"(?:[$€£]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:[kKmMbB]|million|billion|thousand))?\b)"
        r"|\b\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP)\b"
    )),
    (Category.PERSON, re.compile(
        r"\b(?:Mr|Mrs|Ms|Mx|Dr|Prof)\.? (?P<v>[A-Z][a-z]+(?: [A-Z][a-z]+)?)"
        # "Jane Doe (jane@...)" / "Jane Doe <jane@...>": a name right before an email.
        r"|(?P<v2>[A-Z][a-z]+(?: [A-Z][a-z'\-]+){1,2})(?= ?[(<][^)>\s]+@)"
        r"|(?i:\b(?:champion|contact|attn|attendee|candidate|patient|employee name)s?\s*:\s*)"
        r"(?P<v3>[A-Z][a-z]+(?: [A-Z][a-z'\-]+){1,2})"
    )),
]
_FIRST = {Category.SECRET, Category.EMAIL}  # before the registries, so "maya.chen@…" stays one email
_TOKEN_RE = re.compile(r"\[(?:%s)_\d+\]" % "|".join(c.value for c in Category))

# Name parts that are also everyday capitalized words are left to full-name matching only.
COMMON_WORDS = frozenset({
    "will", "may", "mark", "grant", "case", "lee", "long", "young", "king", "hill", "park",
    "june", "april", "rose", "faith", "hope", "joy", "bill", "art", "dean", "chase", "page",
})


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _iban_ok(raw: str) -> bool:
    s = raw.replace(" ", "")
    if not (15 <= len(s) <= 34):
        return False
    s = s[4:] + s[:4]
    try:
        return int("".join(str(int(c, 36)) for c in s)) % 97 == 1
    except ValueError:
        return False


def _valid(category: Category, text: str, groupdict: dict) -> bool:
    if category is Category.CREDIT_CARD:
        digits = re.sub(r"\D", "", text)
        return 13 <= len(digits) <= 19 and _luhn_ok(digits)
    if category is Category.BANK_ACCOUNT and groupdict.get("v") is None:
        return _iban_ok(text)
    return True


def _normalize(category: Category, value: str) -> str:
    if category in (Category.PERSON, Category.CUSTOMER, Category.PROJECT, Category.EMAIL):
        return " ".join(value.lower().split())
    if category in (Category.PHONE, Category.CREDIT_CARD, Category.BANK_ACCOUNT, Category.NATIONAL_ID):
        return re.sub(r"[^0-9A-Za-z]", "", value).upper()
    return value


@dataclass
class Span:
    start: int
    end: int
    category: Category
    value: str


def _aliases(names: Iterable[str], category: Category) -> dict[tuple[Category, str], str]:
    """A bare first name, surname or codename shares its full form's token when only one full form has it."""
    seen: dict[str, set[str]] = {}
    for full in names:
        parts = full.split()
        if len(parts) < 2:
            continue
        for part in parts:
            if part.lower() not in ("project", "operation"):
                seen.setdefault(part.lower(), set()).add(full)
    return {(category, k): next(iter(v)) for k, v in seen.items() if len(v) == 1}


def _expand_projects(projects: Iterable[str]) -> list[str]:
    """"Project Bluefin" also registers the bare codename "Bluefin"."""
    names: list[str] = []
    for p in projects:
        p = p.strip()
        names.append(p)
        if p.lower().startswith(("project ", "operation ")):
            names.append(p.split(" ", 1)[1])
    return names


def _expand_people(people: Iterable[str]) -> tuple[list[str], list[str]]:
    full_names: list[str] = []
    parts: list[str] = []
    for full in people:
        full = full.strip()
        if not full:
            continue
        full_names.append(full)
        for part in full.split():
            if len(part) > 2 and part.lower() not in COMMON_WORDS:
                parts.append(part)
    return full_names, parts


class Tokenizer:
    """Finds sensitive spans. ``people``, ``customers`` and ``projects`` are exact names (case-insensitive, whole
    word) that are always found. Each person's capitalized first name and surname are registered too, so "Chen
    said…" is caught once "Maya Chen" is known."""

    def __init__(self, people: Iterable[str] = (), customers: Iterable[str] = (), projects: Iterable[str] = ()):
        projects = list(projects)
        full_names, name_parts = _expand_people(people)
        self.aliases = _aliases(full_names, Category.PERSON) | _aliases(projects, Category.PROJECT)
        self._registries: list[tuple[Category, re.Pattern[str]]] = []
        for category, names, flags in (
            (Category.PROJECT, _expand_projects(projects), "(?i)"),
            (Category.CUSTOMER, list(customers), "(?i)"),
            (Category.PERSON, full_names, "(?i)"),
            (Category.PERSON, name_parts, ""),  # lone first names / surnames only count when capitalized
        ):
            names = sorted({n.strip() for n in names if n.strip()}, key=len, reverse=True)
            if names:
                alt = "|".join(re.escape(n) for n in names)
                self._registries.append((category, re.compile(rf"{flags}(?<!\w)(?:{alt})(?!\w)")))

    def find(self, text: str, categories: Iterable[Category] = ALL_CATEGORIES) -> list[Span]:
        cats = frozenset(categories)
        candidates: list[Span] = []
        head = [p for p in _PATTERNS if p[0] in _FIRST]
        tail = [p for p in _PATTERNS if p[0] not in _FIRST]
        for category, pattern in head + self._registries + tail:
            if category not in cats:
                continue
            for m in pattern.finditer(text):
                gd = m.groupdict()
                group = next((g for g in ("v", "v2", "v3") if gd.get(g) is not None), None)
                start, end = m.span(group) if group else m.span()
                value = text[start:end]
                if _TOKEN_RE.fullmatch(value) or not _valid(category, value, gd):
                    continue
                candidates.append(Span(start, end, category, value))
        chosen: list[Span] = []  # on overlap, the earlier detector in the order above wins
        for span in candidates:
            if any(span.start < c.end and c.start < span.end for c in chosen):
                continue
            chosen.append(span)
        return sorted(chosen, key=lambda s: s.start)


class Names:
    """The org registries for detection: people (directory + CRM contacts), and optionally customers and project
    codenames. Built once per process (``assistant.name_registry``) because compiling the patterns is the slow part."""

    def __init__(self, names: Iterable[str], customers: Iterable[str] = (), projects: Iterable[str] = ()):
        self.full = sorted({n for n in names if n}, key=len, reverse=True)
        self.customers = sorted({c for c in customers if c})
        self.projects = sorted({p for p in projects if p})
        self._re = re.compile(r"\b(" + "|".join(re.escape(n) for n in self.full) + r")\b") if self.full else None
        self.tokenizer = Tokenizer(self.full, self.customers, self.projects)

    def find(self, text: str) -> list[str]:
        """Full names of known people in the text (exact case)."""
        return self._re.findall(text) if self._re else []


@dataclass
class Vault:
    """Token <-> value map for one request or one conversation. Never sent anywhere."""

    names: Names = field(default_factory=lambda: Names([]))
    categories: frozenset[Category] = ALL_CATEGORIES
    _by_value: dict[tuple[Category, str], str] = field(default_factory=dict)
    _by_token: dict[str, str] = field(default_factory=dict)
    _counters: dict[Category, int] = field(default_factory=dict)
    _used: set[str] = field(default_factory=set)  # tokens issued or reused in this request (for summary())

    def token_for(self, category: Category, value: str) -> str:
        key = (category, _normalize(category, value))
        if key not in self._by_value:
            n = self._counters.get(category, 0) + 1
            self._counters[category] = n
            token = f"[{category.value}_{n}]"
            self._by_value[key] = token
            self._by_token[token] = value
        self._used.add(self._by_value[key])
        return self._by_value[key]

    def tokenize(self, text: str) -> str:
        tok = self.names.tokenizer
        out, last = [], 0
        for span in tok.find(text, self.categories):
            out.append(text[last:span.start])
            value = tok.aliases.get((span.category, span.value.lower()), span.value)
            out.append(self.token_for(span.category, value))
            last = span.end
        out.append(text[last:])
        return "".join(out)

    def detokenize(self, text: str) -> str:
        return _TOKEN_RE.sub(lambda m: self._by_token.get(m.group(0), m.group(0)), text)

    def leaks(self, payload: str) -> list[str]:
        """Raw sensitive values still present in an outbound payload (the same detectors as ``tokenize``)."""
        return [s.value for s in self.names.tokenizer.find(payload, self.categories)]

    def summary(self) -> dict[str, int]:
        """Distinct values per kind tokenized in this request (a conversation vault also holds earlier turns')."""
        out: dict[str, int] = {}
        for tok in self._used:
            kind = tok[1:].rsplit("_", 1)[0]
            out[kind] = out.get(kind, 0) + 1
        return dict(sorted(out.items()))

    def to_json(self) -> str:
        return json.dumps({"values": [[c.value, norm, tok] for (c, norm), tok in self._by_value.items()],
                           "tokens": self._by_token, "counters": {c.value: n for c, n in self._counters.items()}})

    @classmethod
    def from_json(cls, raw: str | None, names: Names | None = None,
                  categories: Iterable[Category] = ALL_CATEGORIES) -> "Vault":
        v = cls(names or Names([]), frozenset(categories))
        if not raw:
            return v
        d = json.loads(raw)
        v._by_value = {(Category(c), norm): tok for c, norm, tok in d["values"]}
        v._by_token = dict(d["tokens"])
        v._counters = {Category(c): n for c, n in d["counters"].items()}
        return v
