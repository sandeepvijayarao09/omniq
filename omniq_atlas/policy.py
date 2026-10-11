"""Role- and attribute-based access control, compiled into Atlas Vector Search pre-filters.

The ACL is enforced inside the database query, so unauthorized chunks are never
returned to the application, then re-checked per hit as a second line of defence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CATEGORIES = ("general", "engineering", "sales", "hr", "finance", "board", "personal")

OPEN_GRANTS: dict[str, set[str]] = {
    "basic": {"general"},
    "pro": {"general", "engineering", "sales"},
    "manager": {"general", "engineering", "sales"},
    "admin": set(CATEGORIES) - {"personal"},
    "executive": set(CATEGORIES) - {"personal"},
}
DEPARTMENT_GRANTS: dict[str, set[str]] = {"finance": {"finance"}, "legal": {"finance"}}


@dataclass(frozen=True)
class User:
    id: str
    name: str
    role: str
    department: str
    team: str = ""
    title: str = ""
    email: str = ""
    manages: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_doc(cls, d: dict[str, Any]) -> "User":
        return cls(id=d["id"], name=d["name"], role=d["role"], department=d.get("department", ""),
                   team=d.get("team", ""), title=d.get("title", ""), email=d.get("email", ""),
                   manages=tuple(d.get("manages") or ()))

    def open_categories(self) -> set[str]:
        grants = set(OPEN_GRANTS[self.role])
        if self.role in ("pro", "manager"):
            grants |= DEPARTMENT_GRANTS.get(self.department, set())
        return grants

    def hr_scope(self) -> tuple[str, ...]:
        if self.role != "manager":
            return ()
        return self.manages or (self.team,)


def vector_filter(user: User, focus: list[str] | None = None) -> dict[str, Any]:
    """The $vectorSearch `filter` for everything this user may read, optionally narrowed to an agent's focus.
    Focus can only ever narrow access: it is intersected with the user's grants, never added to them."""
    open_cats = user.open_categories() & set(focus) if focus else user.open_categories()
    clauses: list[dict[str, Any]] = [{"category": {"$in": sorted(open_cats) or ["__none__"]}}]
    if user.hr_scope() and (not focus or "hr" in focus):
        clauses.append({"$and": [{"category": {"$eq": "hr"}}, {"team": {"$in": list(user.hr_scope())}}]})
    clauses.append({"$and": [{"category": {"$eq": "personal"}}, {"owner": {"$eq": user.id}}]})
    return {"$or": clauses}


def can_read(user: User, doc: dict[str, Any]) -> bool:
    cat = doc.get("category", "general")
    if cat == "personal":
        return doc.get("owner") == user.id
    if cat in user.open_categories():
        return True
    return cat == "hr" and doc.get("team") in user.hr_scope()


def describe(user: User) -> str:
    parts = sorted(user.open_categories())
    if user.hr_scope():
        parts.append(f"hr({', '.join(user.hr_scope())})")
    return ", ".join(parts) + ", own personal files"
