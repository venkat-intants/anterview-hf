"""Job simulations and portfolio rounds — PH4-D4.

WHO DOES WHAT
An HR manager writes a task round's brief and items (``round_tasks``) and may
attach reference materials. Once the workflow runner moves a candidate onto a
``job_simulation`` or ``portfolio`` round, it mints them a magic link
(``task_submissions``) the same way an exam link is minted — a token in the
URL fragment, presented as the ``X-Task-Token`` header, hashed at rest. The
candidate opens it with no account, works (autosaved), and submits. HR then
assigns reviewers through the EXISTING scorecard machinery
(``interviewer_scorecards``, widened to these two kinds) and records the
round's pass/hold through the EXISTING ``post_round_review`` — this module
never advances, holds or scores anyone.

WHAT IT NEVER DOES
No path here writes ``enrolments.status`` or moves an application toward a
decision, and nothing here reaches an LLM client. A submission is evidence; a
person evaluates it against the round's frozen ``round_criteria``, exactly
like a human_review round (CLAUDE.md constraint 9). Candidate-authored
content — the brief a candidate writes back, a link they paste — is never
read by an agent (AST-tested).

THE LINK
Same shape as ``app/offer_security.py``: a 256-bit random token, presented as
a header, hashed with HMAC before it is ever stored. ``task_link_secret``
blank derives a namespaced secret from ``jwt_secret``, never reusing it
verbatim.

EXTERNAL LINKS
Portfolio links are validated (https only, no userinfo, no IP literal, no
non-standard port, a suffix match against an allow-list) and stored — never
fetched or previewed by this server, so there is no SSRF surface. A reviewer
sees an interstitial before following one (frontend). See
``docs/ACCEPTED-RISKS.md``.

Callers commit — every function here does at most ``db.flush()``.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import re
import secrets
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import accommodations
from app import document_storage as store
from app.config import settings
from app.guest_identity import provision_guest_user
from app.interviewer_scorecards import RequestMeta
from app.mailer import candidate_language, enqueue_email
from app.models import AuditLog
from app.notifications_util import create_notification

log = structlog.get_logger(__name__)

TASK_KINDS: tuple[str, ...] = ("job_simulation", "portfolio")
RESPONSE_TYPES: tuple[str, ...] = ("text", "file", "link")
LINK_KINDS: tuple[str, ...] = (
    "repository", "design", "document", "video", "website", "other",
)
_TOKEN_BYTES = 32
_ITEM_KEY_RE = re.compile(r"^[a-z0-9_]{1,80}$")
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
_IP_LITERAL_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

#: Domains a portfolio accepts when HR sets no explicit allow-list.
DEFAULT_LINK_DOMAINS: tuple[str, ...] = (
    "github.com", "gitlab.com", "bitbucket.org", "behance.net", "dribbble.com",
    "figma.com", "kaggle.com", "youtube.com", "vimeo.com",
)

#: The transition table ``task_submissions_lifecycle`` also enforces
#: (migration ``a5d7f9b1c3e8``) — mirrored here so the app can refuse with a
#: sentence before ever reaching the trigger.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "assigned": frozenset({"in_progress", "submitted", "expired", "withdrawn"}),
    "in_progress": frozenset({"submitted", "expired", "withdrawn"}),
    "submitted": frozenset(),
    "expired": frozenset(),
    "withdrawn": frozenset(),
}


class TaskError(Exception):
    """Refused. Carries the HTTP status and a sentence for a person."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


NOT_AVAILABLE = "This task link isn't available."


# ---------------------------------------------------------------------------
# Security — mirrors app/offer_security.py
# ---------------------------------------------------------------------------
def _derive(label: str) -> str:
    return hmac.new(settings.jwt_secret.encode(), f"ph4:{label}".encode(), hashlib.sha256).hexdigest()


def _link_secret() -> str:
    return settings.task_link_secret or _derive("task_link")


def mint_task_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_task_token(raw: str) -> str:
    return hmac.new(_link_secret().encode(), raw.encode(), hashlib.sha256).hexdigest()


def task_link(raw_token: str) -> str:
    return f"{settings.app_base_url.rstrip('/')}/task#{raw_token}"


# ---------------------------------------------------------------------------
# Storage keys — the same object-storage helpers as preboarding documents
# (app/document_storage.py), with task-shaped prefixes and no person named.
# ---------------------------------------------------------------------------
def material_storage_key(company_id: uuid.UUID, round_id: uuid.UUID, material_id: uuid.UUID) -> str:
    return f"task_materials/{company_id}/{round_id}/{material_id}"


def response_storage_key(
    company_id: uuid.UUID, submission_id: uuid.UUID, response_id: uuid.UUID,
) -> str:
    return f"tasks/{company_id}/{submission_id}/{response_id}"


def submission_prefix(company_id: uuid.UUID, submission_id: uuid.UUID) -> str:
    return f"tasks/{company_id}/{submission_id}/"


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------
# Characters no link we accept may contain anywhere. A backslash is the one
# that mattered: Python's urlsplit keeps it inside the hostname, while a
# browser treats it as a path separator -- so
# https://attacker.example\.github.com/x read as a github.com subdomain here
# and opened attacker.example there. C0/C1 controls and whitespace are refused
# rather than stripped, so what is stored is exactly what was typed.
_LINK_FORBIDDEN_RE = re.compile(r"[\\\s\x00-\x1f\x7f-\x9f]")


def validate_link(url: str, allowed_domains: list[str] | None) -> str:
    r"""https only; no userinfo; no IP literal host; no port but 443; IDNA
    hostname; a suffix match on a DOT BOUNDARY against ``allowed_domains``
    (or ``DEFAULT_LINK_DOMAINS``) — so ``evilgithub.com`` is refused for
    ``github.com``. Returns the canonical URL, with any fragment dropped.
    Never fetches the link (that would be a server-side request).

    THE HOST MUST MEAN THE SAME THING TO US AND TO THE BROWSER. Python's URL
    parser and the WHATWG parser browsers use disagree about ``\``: a
    security review showed ``https://attacker.example\.github.com/x`` passing
    as a github.com subdomain while a browser opened ``attacker.example``. The
    fullwidth ``＼`` (U+FF3C) did the same after IDNA mapped it to ``\``. So a
    backslash, whitespace or a control character anywhere is refused, and the
    host AFTER IDNA must be nothing but letters, digits, hyphens and dots.
    """
    raw = (url or "").strip()
    if not raw or len(raw) > 2000:
        raise TaskError(422, "Give a link of up to 2000 characters.")
    if _LINK_FORBIDDEN_RE.search(raw):
        raise TaskError(422, "That link contains characters a web address cannot have.")
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise TaskError(422, "That does not look like a link.") from exc
    if parsed.scheme.lower() != "https":
        raise TaskError(422, "Links must start with https://.")
    if parsed.username or parsed.password:
        raise TaskError(422, "A link may not carry a username or password.")
    host = parsed.hostname or ""
    if not host:
        raise TaskError(422, "That link has no address.")
    if _IP_LITERAL_RE.match(host) or ":" in host:
        raise TaskError(422, "Links to a bare IP address are not accepted.")
    try:
        port = parsed.port
    except ValueError as exc:  # ":abc" or ":99999" -- was an uncaught 500
        raise TaskError(422, "Links may only use the standard https port.") from exc
    if port not in (None, 443):
        raise TaskError(422, "Links may only use the standard https port.")
    try:
        idna_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise TaskError(422, "That link's address is not valid.") from exc
    # After IDNA the host is ASCII, so this is the check that closes the
    # fullwidth-backslash route (U+FF3C maps to "\\") along with "%" and NUL.
    if not _DOMAIN_RE.fullmatch(idna_host):
        raise TaskError(422, "That link's address is not valid.")
    domains = tuple((d or "").strip().lower() for d in (allowed_domains or DEFAULT_LINK_DOMAINS) if d)
    if not any(idna_host == d or idna_host.endswith("." + d) for d in domains):
        raise TaskError(
            422, f"Links to {host} are not on the approved list for this task."
        )
    return urlunsplit((
        "https", idna_host,
        quote(parsed.path or "", safe="/-._~!$&'()*+,;=:@%"),
        quote(parsed.query, safe="/-._~!$&'()*+,;=:@%?"),
        "",
    ))


def _clean_domain(raw: str) -> str:
    d = unicodedata.normalize("NFKC", str(raw or "")).strip().lower().rstrip(".")
    with contextlib.suppress(UnicodeError):
        d = d.encode("idna").decode("ascii")
    if not _DOMAIN_RE.match(d):
        raise TaskError(422, f"'{raw}' is not a valid domain.")
    return d


def validate_config(kind: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise a ``round_tasks`` payload. Pure."""
    if kind not in TASK_KINDS:
        raise TaskError(422, "Unknown task kind.")
    brief = str(cfg.get("brief") or "").strip()
    if not 1 <= len(brief) <= 20000:
        raise TaskError(422, "Give the candidate a brief, up to 20000 characters.")
    translations_in = cfg.get("brief_translations")
    translations: dict[str, str] | None = None
    if translations_in:
        if not isinstance(translations_in, dict) or set(translations_in) - {"hi", "te"}:
            raise TaskError(422, "Translations are only for hi and te.")
        translations = {}
        for lang, value in translations_in.items():
            text_value = str(value or "").strip()
            if not 1 <= len(text_value) <= 20000:
                raise TaskError(422, "Each translation is up to 20000 characters.")
            translations[lang] = text_value

    items_in = cfg.get("items") or []
    if not isinstance(items_in, list) or len(items_in) > 20:
        raise TaskError(422, "A task has at most 20 items.")
    items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for raw in items_in:
        if not isinstance(raw, dict):
            raise TaskError(422, "Each item must be an object.")
        key = str(raw.get("key") or "").strip()
        if not _ITEM_KEY_RE.match(key):
            raise TaskError(
                422, "Each item needs a key of lowercase letters, digits and underscores."
            )
        if key in seen_keys:
            raise TaskError(422, f"Item key '{key}' is used twice.")
        seen_keys.add(key)
        prompt = str(raw.get("prompt") or "").strip()
        if not 1 <= len(prompt) <= 4000:
            raise TaskError(422, f"Item '{key}' needs a prompt of up to 4000 characters.")
        response_type = raw.get("response_type")
        if response_type not in RESPONSE_TYPES:
            raise TaskError(422, f"Item '{key}': choose a text, file or link response.")
        max_chars = raw.get("max_chars")
        if max_chars is not None and (
            isinstance(max_chars, bool) or not isinstance(max_chars, int)
            or not 1 <= max_chars <= 20000
        ):
            raise TaskError(422, f"Item '{key}': max_chars must be between 1 and 20000.")
        items.append({
            "key": key, "prompt": prompt, "response_type": response_type,
            "required": bool(raw.get("required", True)), "max_chars": max_chars,
        })

    out: dict[str, Any] = {"brief": brief, "brief_translations": translations, "items": items}
    if kind == "portfolio":
        min_a, max_a = cfg.get("min_artifacts"), cfg.get("max_artifacts")
        if min_a is None or max_a is None:
            raise TaskError(
                422, "Set how many artifacts a portfolio needs, at minimum and at most."
            )
        min_a, max_a = int(min_a), int(max_a)
        if not (0 <= min_a <= max_a <= 20):
            raise TaskError(422, "Artifact counts must be 0 to 20, minimum at most maximum.")
        allow_files = bool(cfg.get("allow_files", True))
        allow_links = bool(cfg.get("allow_links", True))
        if not allow_files and not allow_links:
            raise TaskError(422, "A portfolio must accept files, links, or both.")
        domains_in = cfg.get("allowed_link_domains")
        domains = list(DEFAULT_LINK_DOMAINS) if domains_in is None else (
            [_clean_domain(d) for d in domains_in] if domains_in else
            (_ for _ in ()).throw(TaskError(
                422, "Give at least one allowed link domain, or omit the field for the default list."
            ))
        )
        out.update({
            "min_artifacts": min_a, "max_artifacts": max_a, "allow_files": allow_files,
            "allow_links": allow_links, "allowed_link_domains": domains,
        })
    else:
        if not items:
            raise TaskError(422, "A job simulation needs at least one item.")
        out.update({
            "min_artifacts": None, "max_artifacts": None, "allow_files": True,
            "allow_links": True, "allowed_link_domains": None,
        })
    return out


def config_digest(cfg: dict[str, Any]) -> str:
    blob = json.dumps(cfg, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def due_and_limit(
    now: datetime, *, deadline_days: int, time_limit_seconds: int | None,
    accommodation: accommodations.AccommodationRow | None,
) -> tuple[datetime, int | None, int]:
    """(due_at, effective_time_limit_seconds, extra_time_seconds).

    ``time_limit_seconds`` scales the same way an exam round's does
    (``accommodations.scaled``); ``deadline_days`` gets any recorded
    extension, exactly like an exam assignment's expiry.
    """
    extra_days = accommodation.deadline_extension_days if accommodation else None
    pct = accommodation.extra_time_percent if accommodation else None
    due_at = now + timedelta(days=int(deadline_days) + int(extra_days or 0))
    extra_seconds = accommodations.extra_seconds(time_limit_seconds, pct)
    effective_limit = accommodations.scaled(time_limit_seconds, pct)
    return due_at, effective_limit, extra_seconds


def item_index(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {i["key"]: i for i in items}


# ---------------------------------------------------------------------------
# Records — facts only
# ---------------------------------------------------------------------------
def _audit(
    db: AsyncSession, *, actor: uuid.UUID | None, action: str, resource_id: uuid.UUID,
    details: dict[str, Any], meta: RequestMeta, actor_type: str = "user",
) -> None:
    db.add(AuditLog(
        actor_id=actor, actor_type=actor_type, action=action, resource_type="task_submission",
        resource_id=resource_id, details=details, ip_address=meta.ip_address,
        user_agent=meta.user_agent, event_ts=datetime.now(tz=UTC),
    ))


async def _event(
    db: AsyncSession, *, company_id: uuid.UUID, submission_id: uuid.UUID | None,
    round_id: uuid.UUID | None, action: str, actor_type: str, actor: uuid.UUID | None,
    details: dict[str, Any] | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO task_events (id, company_id, submission_id, round_id, action,"
            " actor_type, actor_user_id, details) VALUES (gen_random_uuid(), :c, :s, :r, :a,"
            " :t, :u, CAST(:d AS jsonb))"
        ),
        {"c": company_id, "s": submission_id, "r": round_id, "a": action, "t": actor_type,
         "u": actor, "d": json.dumps(details or {}, default=str)},
    )


def _reason_facts(reason: str | None) -> dict[str, Any]:
    return {"has_reason": bool(reason), "reason_chars": len(reason or "")}


# ---------------------------------------------------------------------------
# Configuration (HR)
# ---------------------------------------------------------------------------
async def _config_lock_reason(db: AsyncSession, company_id: uuid.UUID, round_id: uuid.UUID) -> str | None:
    """The sentence for a locked round's task config, mirroring
    ``app/exam_locks.py`` — round_tasks freezes with its workflow
    (``workflow_children_immutable``), never with attempts (a task round has
    no concept of "taken" at the config level)."""
    row = (
        await db.execute(
            text(
                "SELECT w.status, w.review_status FROM workflow_rounds wr"
                "  JOIN workflows w ON w.id = wr.workflow_id"
                " WHERE wr.id = :r AND wr.company_id = :c AND wr.deleted_at IS NULL"
            ),
            {"r": round_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise TaskError(404, "Round not found.")
    if row["status"] in ("published", "archived"):
        return (
            f"This workflow version is {row['status']}; its task configuration is fixed. "
            "Edit a new version instead."
        )
    if row["review_status"] in ("in_review", "approved"):
        return (
            "This version is waiting for review or has been approved, so its task "
            "configuration is fixed. Withdraw or reopen it to make changes."
        )
    return None


async def _round(db: AsyncSession, company_id: uuid.UUID, round_id: uuid.UUID) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                "SELECT id, company_id, workflow_id, kind, title, deadline_days,"
                " time_limit_seconds FROM workflow_rounds"
                " WHERE id = :r AND company_id = :c AND deleted_at IS NULL"
            ),
            {"r": round_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise TaskError(404, "Round not found.")
    if row["kind"] not in TASK_KINDS:
        raise TaskError(409, f"'{row['title']}' is not a job simulation or portfolio round.")
    return dict(row)


def _config_out(row: Any) -> dict[str, Any]:
    return {
        "round_id": str(row["round_id"]), "kind": row["kind"], "brief": row["brief"],
        "brief_translations": row["brief_translations"], "items": row["items"],
        "min_artifacts": row["min_artifacts"], "max_artifacts": row["max_artifacts"],
        "allow_files": row["allow_files"], "allow_links": row["allow_links"],
        "allowed_link_domains": row["allowed_link_domains"],
    }


async def get_config(db: AsyncSession, *, company_id: uuid.UUID, round_id: uuid.UUID) -> dict[str, Any] | None:
    await _round(db, company_id, round_id)
    row = (
        await db.execute(
            text("SELECT * FROM round_tasks WHERE round_id = :r AND company_id = :c"),
            {"r": round_id, "c": company_id},
        )
    ).mappings().first()
    return _config_out(row) if row else None


async def put_config(
    db: AsyncSession, *, company_id: uuid.UUID, round_id: uuid.UUID, cfg: dict[str, Any],
    actor: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    """Write a task round's brief and items — draft only. Caller commits."""
    round_ = await _round(db, company_id, round_id)
    reason = await _config_lock_reason(db, company_id, round_id)
    if reason is not None:
        raise TaskError(409, reason)
    clean = validate_config(round_["kind"], cfg)
    existing = await db.scalar(
        text("SELECT 1 FROM round_tasks WHERE round_id = :r"), {"r": round_id},
    )
    now = datetime.now(tz=UTC)
    if existing:
        await db.execute(
            text(
                "UPDATE round_tasks SET brief = :b, brief_translations = CAST(:bt AS jsonb),"
                " items = CAST(:it AS jsonb), min_artifacts = :mina, max_artifacts = :maxa,"
                " allow_files = :af, allow_links = :al, allowed_link_domains = :dom,"
                " updated_at = :n WHERE round_id = :r"
            ),
            {"b": clean["brief"],
             "bt": json.dumps(clean["brief_translations"]) if clean["brief_translations"] else None,
             "it": json.dumps(clean["items"]), "mina": clean["min_artifacts"],
             "maxa": clean["max_artifacts"], "af": clean["allow_files"], "al": clean["allow_links"],
             "dom": clean["allowed_link_domains"], "n": now, "r": round_id},
        )
        action = "updated"
    else:
        await db.execute(
            text(
                "INSERT INTO round_tasks (id, company_id, round_id, kind, brief,"
                " brief_translations, items, min_artifacts, max_artifacts, allow_files,"
                " allow_links, allowed_link_domains, created_at, updated_at)"
                " VALUES (:i,:c,:r,:k,:b, CAST(:bt AS jsonb), CAST(:it AS jsonb), :mina, :maxa,"
                " :af, :al, :dom, :n, :n)"
            ),
            {"i": uuid.uuid4(), "c": company_id, "r": round_id, "k": round_["kind"],
             "b": clean["brief"],
             "bt": json.dumps(clean["brief_translations"]) if clean["brief_translations"] else None,
             "it": json.dumps(clean["items"]), "mina": clean["min_artifacts"],
             "maxa": clean["max_artifacts"], "af": clean["allow_files"], "al": clean["allow_links"],
             "dom": clean["allowed_link_domains"], "n": now},
        )
        action = "created"
    await _event(
        db, company_id=company_id, submission_id=None, round_id=round_id,
        action="round_task_updated", actor_type="user", actor=actor,
        details={"action": action, "items": len(clean["items"])},
    )
    _audit(db, actor=actor, action="round_task.updated", resource_id=round_id,
          details={"company_id": str(company_id), "round_id": str(round_id), "action": action},
          meta=meta)
    return await get_config(db, company_id=company_id, round_id=round_id) or {}


def _material_out(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "title": row["title"], "original_name": row["original_name"],
        "content_type": row["content_type"], "size_bytes": row["size_bytes"],
        "position": row["position"],
    }


async def list_materials(db: AsyncSession, *, company_id: uuid.UUID, round_id: uuid.UUID) -> list[dict[str, Any]]:
    await _round(db, company_id, round_id)
    rows = (
        await db.execute(
            text(
                "SELECT * FROM round_task_materials WHERE round_id = :r AND company_id = :c"
                " ORDER BY position, created_at"
            ),
            {"r": round_id, "c": company_id},
        )
    ).mappings().all()
    return [_material_out(r) for r in rows]


async def add_material(
    db: AsyncSession, *, company_id: uuid.UUID, round_id: uuid.UUID, title: str,
    data: bytes, filename: str | None, actor: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    """Store one HR reference file. Returns ``_storage_key`` for the router,
    which removes the object again if the commit fails."""
    await _round(db, company_id, round_id)
    reason = await _config_lock_reason(db, company_id, round_id)
    if reason is not None:
        raise TaskError(409, reason)
    title = (title or "").strip()
    if not 1 <= len(title) <= 200:
        raise TaskError(422, "Give the material a title of up to 200 characters.")
    try:
        checked = store.check(data, filename, max_bytes=settings.task_material_max_bytes)
    except store.DocumentRejectedError as exc:
        raise TaskError(422, str(exc)) from exc
    material_id = uuid.uuid4()
    key = material_storage_key(company_id, round_id, material_id)
    now = datetime.now(tz=UTC)
    position = await db.scalar(
        text("SELECT COALESCE(max(position), -1) + 1 FROM round_task_materials WHERE round_id = :r"),
        {"r": round_id},
    )
    await db.execute(
        text(
            "INSERT INTO round_task_materials (id, company_id, round_id, title, storage_key,"
            " original_name, content_type, size_bytes, sha256, position, created_at, updated_at)"
            " VALUES (:i,:c,:r,:t,:k,:n,:ct,:sz,:sha,:p,:now,:now)"
        ),
        {"i": material_id, "c": company_id, "r": round_id, "t": title, "k": key,
         "n": checked.safe_name, "ct": checked.content_type, "sz": checked.size_bytes,
         "sha": checked.sha256, "p": position, "now": now},
    )
    await store.store(settings, key, data, checked.content_type)
    _audit(db, actor=actor, action="round_task_material.added", resource_id=material_id,
          details={"company_id": str(company_id), "round_id": str(round_id)}, meta=meta)
    row = (
        await db.execute(text("SELECT * FROM round_task_materials WHERE id = :i"), {"i": material_id})
    ).mappings().one()
    return {**_material_out(row), "_storage_key": key}


async def remove_material(
    db: AsyncSession, *, company_id: uuid.UUID, round_id: uuid.UUID, material_id: uuid.UUID,
    actor: uuid.UUID, meta: RequestMeta,
) -> None:
    await _round(db, company_id, round_id)
    reason = await _config_lock_reason(db, company_id, round_id)
    if reason is not None:
        raise TaskError(409, reason)
    row = (
        await db.execute(
            text(
                "SELECT storage_key FROM round_task_materials"
                " WHERE id = :i AND round_id = :r AND company_id = :c"
            ),
            {"i": material_id, "r": round_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise TaskError(404, "Material not found.")
    await db.execute(text("DELETE FROM round_task_materials WHERE id = :i"), {"i": material_id})
    # A clone shares the storage key (workflows.clone_for_edit) — the object
    # is removed only once nothing else still names it.
    still_used = await db.scalar(
        text("SELECT 1 FROM round_task_materials WHERE storage_key = :k"), {"k": row["storage_key"]},
    )
    if not still_used:
        await store.remove(settings, [row["storage_key"]])
    _audit(db, actor=actor, action="round_task_material.removed", resource_id=material_id,
          details={"company_id": str(company_id), "round_id": str(round_id)}, meta=meta)


async def material_download(
    db: AsyncSession, *, company_id: uuid.UUID, round_id: uuid.UUID, material_id: uuid.UUID,
) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                "SELECT storage_key, original_name FROM round_task_materials"
                " WHERE id = :i AND round_id = :r AND company_id = :c"
            ),
            {"i": material_id, "r": round_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise TaskError(404, "Material not found.")
    url = await store.signed_download(settings, row["storage_key"], row["original_name"] or "material")
    return {"url": url, "expires_in": store.PRESIGN_SECONDS}


# ---------------------------------------------------------------------------
# Runner hook — issuing a task
# ---------------------------------------------------------------------------
async def issue(
    db: AsyncSession, *, enrolment: dict[str, Any], round_: dict[str, Any], workflow: dict[str, Any],
) -> uuid.UUID | None:
    """Mint a task link for a candidate landing on this round. Caller commits.

    A no-op (logged, not raised) when the round has no configuration yet —
    publish validation already refuses this, so reaching it means the round
    changed after the workflow went live, exactly the shape
    ``exam_round_problem`` handles for exam-backed rounds.
    """
    company_id = enrolment["company_id"]
    cfg_row = (
        await db.execute(
            text("SELECT * FROM round_tasks WHERE round_id = :r AND company_id = :c"),
            {"r": round_["id"], "c": company_id},
        )
    ).mappings().first()
    if cfg_row is None:
        log.warning("job_tasks.issue.no_config", round_id=str(round_["id"]))
        if workflow.get("created_by_user_id"):
            await create_notification(
                db, user_id=workflow["created_by_user_id"], kind="round_not_ready",
                title=f"{enrolment['full_name']} could not start {round_['title']}",
                body="This round has no task configuration. Add a brief and items in the "
                     "workflow builder.",
                link="/hr/workflows",
            )
        return None

    now = datetime.now(tz=UTC)
    # One live link per (enrolment, round): supersede any prior row first —
    # the partial unique index refuses two live rows otherwise.
    prior = (
        await db.execute(
            text(
                "SELECT id, attempt_no FROM task_submissions"
                " WHERE enrolment_id = :e AND round_id = :r AND superseded_at IS NULL"
                " FOR UPDATE"
            ),
            {"e": enrolment["id"], "r": round_["id"]},
        )
    ).mappings().first()
    attempt_no = 1
    if prior is not None:
        prior_is_live = await db.scalar(
            text("SELECT status IN ('assigned','in_progress') FROM task_submissions WHERE id = :i"),
            {"i": prior["id"]},
        )
        if prior_is_live:
            log.info("job_tasks.issue.already_live", enrolment_id=str(enrolment["id"]))
            return uuid.UUID(str(prior["id"]))
        attempt_no = int(prior["attempt_no"]) + 1

    adj_row = await accommodations.effective_for(
        db, company_id=company_id, applicant_id=enrolment["applicant_id"],
        enrolment_id=enrolment["id"], workflow_round_id=round_["id"],
    )
    due_at, time_limit, extra_seconds = due_and_limit(
        now, deadline_days=int(round_.get("deadline_days") or 7),
        time_limit_seconds=round_.get("time_limit_seconds"), accommodation=adj_row,
    )
    cfg = _config_out(cfg_row)
    digest = config_digest({k: v for k, v in cfg.items() if k != "round_id"})
    raw = mint_task_token()
    sub_id = uuid.uuid4()

    if prior is not None:
        await db.execute(
            text(
                "UPDATE task_submissions SET superseded_at = :n, superseded_by_id = :new"
                " WHERE id = :old"
            ),
            {"n": now, "new": sub_id, "old": prior["id"]},
        )
    await db.execute(
        text(
            "INSERT INTO task_submissions (id, company_id, enrolment_id, round_id,"
            " applicant_id, kind, status, token_hash, due_at, time_limit_seconds,"
            " base_time_limit_seconds, extra_time_seconds, deadline_extension_days,"
            " accommodation_id, config_digest, attempt_no, issued_by_user_id, created_at,"
            " updated_at)"
            " VALUES (:i,:c,:e,:r,:a,:k,'assigned',:th,:due,:tl,:btl,:extra,:dext,:acc,:dig,"
            " :attn,:by,:n,:n)"
        ),
        {"i": sub_id, "c": company_id, "e": enrolment["id"], "r": round_["id"],
         "a": enrolment["applicant_id"], "k": round_["kind"], "th": hash_task_token(raw),
         "due": due_at, "tl": time_limit, "btl": round_.get("time_limit_seconds"),
         "extra": extra_seconds, "dext": adj_row.deadline_extension_days if adj_row else None,
         "acc": adj_row.id if adj_row else None, "dig": digest, "attn": attempt_no,
         "by": workflow.get("created_by_user_id"), "n": now},
    )
    if adj_row is not None:
        await accommodations.record_applied(
            db, company_id=company_id, accommodation_id=adj_row.id,
            target_kind="task_submission", target_id=sub_id,
        )
    await _event(
        db, company_id=company_id, submission_id=sub_id, round_id=round_["id"], action="issued",
        actor_type="system", actor=None, details={"attempt_no": attempt_no},
    )
    lang = await candidate_language(db, enrolment["applicant_id"])
    await enqueue_email(
        db, to=enrolment.get("email"), template="task_assigned", lang=lang,
        ctx={
            "name": enrolment.get("full_name"), "round_title": round_["title"],
            "kind": round_["kind"], "task_url": task_link(raw),
            "due": due_at.strftime("%d %b %Y, %H:%M UTC"),
        },
        company_id=company_id, related_kind="task_submission", related_id=sub_id,
        dedupe_key=f"task_assign:{sub_id}",
    )
    if workflow.get("created_by_user_id"):
        await create_notification(
            db, user_id=workflow["created_by_user_id"], kind="task_issued",
            title=f"{enrolment['full_name']} was sent {round_['title']}",
            body=f"Due {due_at:%d %b %Y}", link="/hr/requisitions",
        )
    log.info("job_tasks.issued", enrolment_id=str(enrolment["id"]), round_id=str(round_["id"]))
    return sub_id


# ---------------------------------------------------------------------------
# Candidate side — reached only by the link
# ---------------------------------------------------------------------------
_BY_TOKEN_SQL = """
SELECT s.*, rt.brief, rt.brief_translations, rt.items, rt.min_artifacts, rt.max_artifacts,
       rt.allow_files, rt.allow_links, rt.allowed_link_domains,
       wr.title AS round_title, a.full_name AS candidate_name, a.email AS candidate_email,
       a.user_id AS candidate_user_id, co.name AS company_name,
       e.status AS enrolment_status
  FROM task_submissions s
  JOIN round_tasks rt ON rt.round_id = s.round_id AND rt.company_id = s.company_id
  JOIN workflow_rounds wr ON wr.id = s.round_id
  JOIN applicants a ON a.id = s.applicant_id
  JOIN companies co ON co.id = s.company_id
  JOIN enrolments e ON e.id = s.enrolment_id
 WHERE s.token_hash = :h AND s.redacted_at IS NULL
 FOR UPDATE OF s
"""


async def by_token(db: AsyncSession, raw: str | None) -> dict[str, Any]:
    """The submission a link opens, or one 404 for every failure — no oracle."""
    if not raw or len(raw) > 200:
        raise TaskError(404, NOT_AVAILABLE)
    row = (await db.execute(text(_BY_TOKEN_SQL), {"h": hash_task_token(raw)})).mappings().first()
    if row is None:
        raise TaskError(404, NOT_AVAILABLE)
    sub = dict(row)
    if sub["status"] not in ("assigned", "in_progress", "submitted"):
        raise TaskError(404, NOT_AVAILABLE)
    return sub


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


async def _responses_for(db: AsyncSession, submission_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT * FROM task_responses WHERE submission_id = :s AND redacted_at IS NULL"
                " ORDER BY position, created_at"
            ),
            {"s": submission_id},
        )
    ).mappings().all()
    return [
        {
            "id": str(r["id"]), "item_key": r["item_key"], "response_type": r["response_type"],
            "text_value": r["text_value"], "link_url": r["link_url"], "link_kind": r["link_kind"],
            "title": r["title"], "description": r["description"],
            "original_name": r["original_name"], "content_type": r["content_type"],
            "size_bytes": r["size_bytes"],
        }
        for r in rows
    ]


async def candidate_view(db: AsyncSession, sub: dict[str, Any]) -> dict[str, Any]:
    lang = await candidate_language(db, sub["applicant_id"])
    brief = sub["brief"]
    if lang in ("hi", "te") and sub.get("brief_translations"):
        brief = sub["brief_translations"].get(lang, brief)
    materials = await list_materials(db, company_id=sub["company_id"], round_id=sub["round_id"])
    return {
        "status": sub["status"], "kind": sub["kind"], "round_title": sub["round_title"],
        "company": sub["company_name"], "brief": brief, "items": sub["items"],
        "min_artifacts": sub["min_artifacts"], "max_artifacts": sub["max_artifacts"],
        "allow_files": sub["allow_files"], "allow_links": sub["allow_links"],
        "allowed_link_domains": sub["allowed_link_domains"], "materials": materials,
        "due_at": _iso(sub["due_at"]), "time_limit_seconds": sub["time_limit_seconds"],
        "started_at": _iso(sub["started_at"]), "submitted_at": _iso(sub["submitted_at"]),
        "adjustments": {
            "extra_time_seconds": sub["extra_time_seconds"],
            "deadline_extended": bool(sub["deadline_extension_days"]),
        },
        "responses": await _responses_for(db, sub["id"]),
    }


async def open_submission(db: AsyncSession, *, raw: str | None, meta: RequestMeta) -> dict[str, Any]:
    sub = await by_token(db, raw)
    recent = await db.scalar(
        text(
            "SELECT 1 FROM task_events WHERE submission_id = :s AND action = 'opened'"
            " AND created_at > now() - interval '1 hour' LIMIT 1"
        ),
        {"s": sub["id"]},
    )
    if not recent:
        await _event(
            db, company_id=sub["company_id"], submission_id=sub["id"], round_id=sub["round_id"],
            action="opened", actor_type="candidate", actor=sub["candidate_user_id"],
        )
    return await candidate_view(db, sub)


def _deadline_passed(sub: dict[str, Any], now: datetime) -> bool:
    grace = timedelta(seconds=settings.task_submit_grace_seconds)
    if sub["due_at"] + grace <= now:
        return True
    if sub["time_limit_seconds"] and sub["started_at"]:
        return sub["started_at"] + timedelta(seconds=sub["time_limit_seconds"]) + grace <= now
    return False


async def start(db: AsyncSession, *, raw: str | None, meta: RequestMeta) -> dict[str, Any]:
    sub = await by_token(db, raw)
    if sub["status"] == "assigned":
        now = datetime.now(tz=UTC)
        if _deadline_passed(sub, now):
            raise TaskError(409, "This task's window has closed.")
        await db.execute(
            text(
                "UPDATE task_submissions SET status = 'in_progress', started_at = :n,"
                " updated_at = :n WHERE id = :i"
            ),
            {"n": now, "i": sub["id"]},
        )
        await _event(
            db, company_id=sub["company_id"], submission_id=sub["id"], round_id=sub["round_id"],
            action="started", actor_type="candidate", actor=sub["candidate_user_id"],
        )
        sub = await by_token(db, raw)
    return await candidate_view(db, sub)


def _item_or_raise(sub: dict[str, Any], item_key: str) -> dict[str, Any]:
    items = item_index(sub["items"])
    item = items.get(item_key)
    if item is None:
        raise TaskError(404, "That item is not part of this task.")
    return item


async def save_response(
    db: AsyncSession, *, raw: str | None, item_key: str, text_value: str | None,
    link_url: str | None, meta: RequestMeta,
) -> dict[str, Any]:
    """Autosave one item's answer. Caller commits."""
    sub = await by_token(db, raw)
    if sub["status"] not in ("assigned", "in_progress"):
        raise TaskError(409, "This task is no longer open for changes.")
    now = datetime.now(tz=UTC)
    if _deadline_passed(sub, now):
        raise TaskError(409, "This task's window has closed; nothing more can be saved.")
    item = _item_or_raise(sub, item_key)
    response_type = item["response_type"]
    if response_type == "text":
        value = (text_value or "").strip()
        limit = item.get("max_chars") or 20000
        if item.get("required") and not value:
            raise TaskError(422, "Give an answer before saving.")
        if len(value) > limit:
            raise TaskError(422, f"This answer is limited to {limit} characters.")
        params = {"tv": value or None, "lu": None, "lk": None}
    elif response_type == "link":
        if not link_url:
            if item.get("required"):
                raise TaskError(422, "Give a link before saving.")
            params = {"tv": None, "lu": None, "lk": None}
        else:
            clean = validate_link(link_url, sub.get("allowed_link_domains"))
            params = {"tv": None, "lu": clean, "lk": "other"}
    else:
        raise TaskError(422, "This item takes a file — upload it as an artifact instead.")

    if sub["status"] == "assigned":
        await db.execute(
            text("UPDATE task_submissions SET status = 'in_progress', started_at = :n WHERE id = :i"),
            {"n": now, "i": sub["id"]},
        )
    existing = await db.scalar(
        text("SELECT id FROM task_responses WHERE submission_id = :s AND item_key = :k"),
        {"s": sub["id"], "k": item_key},
    )
    if existing:
        await db.execute(
            text(
                "UPDATE task_responses SET response_type = :rt, text_value = :tv,"
                " link_url = :lu, link_kind = :lk, updated_at = :n WHERE id = :i"
            ),
            {"rt": response_type, "n": now, "i": existing, **params},
        )
    else:
        await db.execute(
            text(
                "INSERT INTO task_responses (id, company_id, submission_id, item_key, position,"
                " response_type, text_value, link_url, link_kind, created_at, updated_at)"
                " VALUES (:i,:c,:s,:k,:p,:rt,:tv,:lu,:lk,:n,:n)"
            ),
            {"i": uuid.uuid4(), "c": sub["company_id"], "s": sub["id"], "k": item_key,
             "p": len(sub["items"]), "rt": response_type, "n": now, **params},
        )
    await _event(
        db, company_id=sub["company_id"], submission_id=sub["id"], round_id=sub["round_id"],
        action="saved", actor_type="candidate", actor=sub["candidate_user_id"],
        details={"item_key": item_key},
    )
    return {"item_key": item_key, "saved": True}


MAX_ARTIFACT_TITLE = 200
MAX_ARTIFACT_DESCRIPTION = 2000


async def add_artifact(
    db: AsyncSession, *, raw: str | None, kind: str, data: bytes | None, filename: str | None,
    link_url: str | None, link_kind: str | None, title: str | None, description: str | None,
    meta: RequestMeta,
) -> dict[str, Any]:
    """A free-form portfolio artifact (a file, or an approved link) — up to
    ``max_artifacts``. Returns ``_storage_key`` for the router on a file."""
    sub = await by_token(db, raw)
    if sub["kind"] != "portfolio":
        raise TaskError(409, "Only a portfolio round accepts artifacts.")
    if sub["status"] not in ("assigned", "in_progress"):
        raise TaskError(409, "This task is no longer open for changes.")
    now = datetime.now(tz=UTC)
    if _deadline_passed(sub, now):
        raise TaskError(409, "This task's window has closed; nothing more can be added.")
    count = await db.scalar(
        text(
            "SELECT count(*) FROM task_responses WHERE submission_id = :s"
            "   AND item_key IS NULL AND redacted_at IS NULL"
        ),
        {"s": sub["id"]},
    )
    if sub["max_artifacts"] is not None and int(count or 0) >= int(sub["max_artifacts"]):
        raise TaskError(422, f"At most {sub['max_artifacts']} artifacts are accepted.")
    title = (title or "").strip() or None
    if title and len(title) > MAX_ARTIFACT_TITLE:
        raise TaskError(422, f"A title is at most {MAX_ARTIFACT_TITLE} characters.")
    description = (description or "").strip() or None
    if description and len(description) > MAX_ARTIFACT_DESCRIPTION:
        raise TaskError(422, f"A description is at most {MAX_ARTIFACT_DESCRIPTION} characters.")
    if link_kind is not None and link_kind not in LINK_KINDS:
        raise TaskError(422, "Choose a valid artifact type.")

    if sub["status"] == "assigned":
        await db.execute(
            text("UPDATE task_submissions SET status = 'in_progress', started_at = :n WHERE id = :i"),
            {"n": now, "i": sub["id"]},
        )

    response_id = uuid.uuid4()
    storage_key: str | None = None
    if kind == "file":
        if not sub["allow_files"]:
            raise TaskError(409, "This portfolio does not accept files.")
        if not data:
            raise TaskError(422, "Choose a file to upload.")
        try:
            checked = store.check(data, filename, max_bytes=settings.task_response_max_bytes)
        except store.DocumentRejectedError as exc:
            raise TaskError(422, str(exc)) from exc
        storage_key = response_storage_key(sub["company_id"], sub["id"], response_id)
        await db.execute(
            text(
                "INSERT INTO task_responses (id, company_id, submission_id, item_key, position,"
                " response_type, title, description, link_kind, storage_key, original_name,"
                " content_type, size_bytes, sha256, created_at, updated_at)"
                " VALUES (:i,:c,:s,NULL,:p,'file',:t,:d,:lk,:k,:n,:ct,:sz,:sha,:now,:now)"
            ),
            {"i": response_id, "c": sub["company_id"], "s": sub["id"], "p": int(count or 0),
             "t": title, "d": description, "lk": link_kind, "k": storage_key,
             "n": checked.safe_name, "ct": checked.content_type, "sz": checked.size_bytes,
             "sha": checked.sha256, "now": now},
        )
        await store.store(settings, storage_key, data, checked.content_type)
    elif kind == "link":
        if not sub["allow_links"]:
            raise TaskError(409, "This portfolio does not accept links.")
        clean = validate_link(link_url or "", sub.get("allowed_link_domains"))
        await db.execute(
            text(
                "INSERT INTO task_responses (id, company_id, submission_id, item_key, position,"
                " response_type, title, description, link_kind, link_url, created_at, updated_at)"
                " VALUES (:i,:c,:s,NULL,:p,'link',:t,:d,:lk,:lu,:now,:now)"
            ),
            {"i": response_id, "c": sub["company_id"], "s": sub["id"], "p": int(count or 0),
             "t": title, "d": description, "lk": link_kind, "lu": clean, "now": now},
        )
    else:
        raise TaskError(422, "An artifact is a file or a link.")

    await _event(
        db, company_id=sub["company_id"], submission_id=sub["id"], round_id=sub["round_id"],
        action="artifact_added", actor_type="candidate", actor=sub["candidate_user_id"],
        details={"kind": kind},
    )
    return {"id": str(response_id), "_storage_key": storage_key}


async def remove_artifact(
    db: AsyncSession, *, raw: str | None, response_id: uuid.UUID, meta: RequestMeta,
) -> str | None:
    """Remove a free-form artifact. Returns the storage key to delete, if any."""
    sub = await by_token(db, raw)
    if sub["status"] not in ("assigned", "in_progress"):
        raise TaskError(409, "This task is no longer open for changes.")
    row = (
        await db.execute(
            text(
                "SELECT storage_key FROM task_responses"
                " WHERE id = :i AND submission_id = :s AND item_key IS NULL"
                "   AND redacted_at IS NULL"
            ),
            {"i": response_id, "s": sub["id"]},
        )
    ).mappings().first()
    if row is None:
        raise TaskError(404, "Artifact not found.")
    await db.execute(text("DELETE FROM task_responses WHERE id = :i"), {"i": response_id})
    await _event(
        db, company_id=sub["company_id"], submission_id=sub["id"], round_id=sub["round_id"],
        action="artifact_removed", actor_type="candidate", actor=sub["candidate_user_id"],
    )
    return row["storage_key"]


CONSENT_TYPE = "assessment_submission"


async def _record_submission_consent(
    db: AsyncSession, *, user_id: uuid.UUID, sub: dict[str, Any],
) -> None:
    """DPDP: the candidate's consent to send their work to the hiring team.
    Booked against the candidate's own identity, at the moment of the act —
    never re-granted over a withdrawal (the public_apply.py precedent)."""
    existing = await db.scalar(
        text(
            "SELECT revoked_at IS NOT NULL AS was_revoked FROM dpdp_consent_ledger"
            " WHERE user_id = :u AND consent_type = :ct AND purpose = 'recruitment'"
            " ORDER BY granted_at DESC LIMIT 1"
        ),
        {"u": user_id, "ct": CONSENT_TYPE},
    )
    if existing is not None:
        if existing:
            log.info("job_tasks.consent.not_regranted_after_withdrawal", user_id=str(user_id))
        return
    await db.execute(
        text(
            "INSERT INTO dpdp_consent_ledger (id, user_id, consent_type, granted, granted_at,"
            " purpose, evidence) VALUES (:id, :u, :ct, true, now(), 'recruitment', CAST(:ev AS jsonb))"
        ),
        {"id": uuid.uuid4(), "u": user_id, "ct": CONSENT_TYPE,
         "ev": json.dumps({"source": "task_submit", "submission_id": str(sub["id"]),
                          "company_id": str(sub["company_id"])})},
    )


def _min_artifacts_met(sub: dict[str, Any], artifact_count: int) -> bool:
    if sub["kind"] != "portfolio" or not sub["min_artifacts"]:
        return True
    return artifact_count >= int(sub["min_artifacts"])


async def submit(
    db: AsyncSession, *, raw: str | None, consent: bool, meta: RequestMeta,
) -> dict[str, Any]:
    sub = await by_token(db, raw)
    if sub["status"] not in ("assigned", "in_progress"):
        raise TaskError(409, f"This task is already {sub['status']}.")
    if not consent:
        raise TaskError(422, "Agree to send this work to the hiring team before submitting.")
    items = item_index(sub["items"])
    responses = {
        r["item_key"]: r for r in await _responses_for(db, sub["id"]) if r["item_key"]
    }
    missing = [
        i["prompt"] for key, i in items.items()
        if i.get("required") and key not in responses
    ]
    if missing:
        raise TaskError(422, "Answer every required item before submitting.")
    artifact_count = await db.scalar(
        text(
            "SELECT count(*) FROM task_responses WHERE submission_id = :s"
            "   AND item_key IS NULL AND redacted_at IS NULL"
        ),
        {"s": sub["id"]},
    )
    if not _min_artifacts_met(sub, int(artifact_count or 0)):
        raise TaskError(422, f"At least {sub['min_artifacts']} artifact(s) are needed to submit.")

    now = datetime.now(tz=UTC)
    user_id = sub["candidate_user_id"]
    if user_id is None:
        user_id = await provision_guest_user(
            db, applicant_id=sub["applicant_id"], full_name=sub["candidate_name"],
            company_id=sub["company_id"], language=await candidate_language(db, sub["applicant_id"]),
            email_prefix="task", now=now,
        )
    await db.execute(
        text(
            "UPDATE task_submissions SET status = 'submitted', submitted_at = :n,"
            " closed_by = 'candidate', consented_at = :n, updated_at = :n WHERE id = :i"
        ),
        {"n": now, "i": sub["id"]},
    )
    await _record_submission_consent(db, user_id=user_id, sub=sub)
    await _event(
        db, company_id=sub["company_id"], submission_id=sub["id"], round_id=sub["round_id"],
        action="submitted", actor_type="candidate", actor=user_id,
    )
    _audit(db, actor=user_id, action="task_submission.submitted", resource_id=sub["id"],
          details={"company_id": str(sub["company_id"]), "round_id": str(sub["round_id"])},
          meta=meta, actor_type="candidate")
    lang = await candidate_language(db, sub["applicant_id"])
    await enqueue_email(
        db, to=sub["candidate_email"], template="task_received", lang=lang,
        ctx={"name": sub["candidate_name"], "round_title": sub["round_title"]},
        to_user_id=user_id, company_id=sub["company_id"], related_kind="task_submission",
        related_id=sub["id"], dedupe_key=f"task_received:{sub['id']}",
    )
    for reviewer in await _reviewers_for(db, company_id=sub["company_id"], enrolment_id=sub["enrolment_id"], round_id=sub["round_id"]):
        await create_notification(
            db, user_id=reviewer, kind="task_submitted",
            title=f"{sub['candidate_name']} submitted {sub['round_title']}", link="/interviewer",
        )
    if sub["issued_by_user_id"]:
        await create_notification(
            db, user_id=sub["issued_by_user_id"], kind="task_submitted",
            title=f"{sub['candidate_name']} submitted {sub['round_title']}",
            link="/hr/requisitions",
        )
    fresh = await by_token(db, raw)
    return await candidate_view(db, fresh)


async def _reviewers_for(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID, round_id: uuid.UUID,
) -> list[uuid.UUID]:
    rows = (
        await db.execute(
            text(
                "SELECT DISTINCT interviewer_user_id FROM interviewer_scorecards"
                " WHERE company_id = :c AND enrolment_id = :e AND round_id = :r"
                "   AND status <> 'withdrawn' AND superseded_at IS NULL"
            ),
            {"c": company_id, "e": enrolment_id, "r": round_id},
        )
    ).all()
    return [uuid.UUID(str(r[0])) for r in rows]


# ---------------------------------------------------------------------------
# HR
# ---------------------------------------------------------------------------
_HR_SUBMISSION_SQL = """
SELECT s.*, wr.title AS round_title, a.full_name AS candidate_name, a.email AS candidate_email
  FROM task_submissions s
  JOIN workflow_rounds wr ON wr.id = s.round_id
  JOIN applicants a ON a.id = s.applicant_id
 WHERE s.id = :i AND s.company_id = :c
"""


async def _hr_submission(db: AsyncSession, company_id: uuid.UUID, submission_id: uuid.UUID) -> dict[str, Any]:
    row = (
        await db.execute(text(_HR_SUBMISSION_SQL + " FOR UPDATE OF s"), {"i": submission_id, "c": company_id})
    ).mappings().first()
    if row is None:
        raise TaskError(404, "Submission not found.")
    return dict(row)


def _submission_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "enrolment_id": str(row["enrolment_id"]),
        "round_id": str(row["round_id"]), "round_title": row.get("round_title"),
        "kind": row["kind"], "status": row["status"], "candidate_name": row.get("candidate_name"),
        "due_at": _iso(row["due_at"]), "started_at": _iso(row["started_at"]),
        "submitted_at": _iso(row["submitted_at"]), "attempt_no": row["attempt_no"],
        "created_at": _iso(row["created_at"]),
    }


async def for_enrolment(db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT s.*, wr.title AS round_title, a.full_name AS candidate_name"
                "  FROM task_submissions s"
                "  JOIN workflow_rounds wr ON wr.id = s.round_id"
                "  JOIN applicants a ON a.id = s.applicant_id"
                " WHERE s.enrolment_id = :e AND s.company_id = :c"
                " ORDER BY s.created_at DESC"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).mappings().all()
    return [_submission_out(dict(r)) for r in rows]


async def for_requisition(db: AsyncSession, *, company_id: uuid.UUID, requisition_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT s.*, wr.title AS round_title, a.full_name AS candidate_name"
                "  FROM task_submissions s"
                "  JOIN workflow_rounds wr ON wr.id = s.round_id"
                "  JOIN applicants a ON a.id = s.applicant_id"
                "  JOIN enrolments e ON e.id = s.enrolment_id"
                " WHERE s.company_id = :c AND e.requisition_id = :r"
                "   AND s.superseded_at IS NULL"
                " ORDER BY s.due_at"
            ),
            {"r": requisition_id, "c": company_id},
        )
    ).mappings().all()
    return [_submission_out(dict(r)) for r in rows]


async def reissue(
    db: AsyncSession, *, company_id: uuid.UUID, submission_id: uuid.UUID, actor: uuid.UUID,
    meta: RequestMeta,
) -> dict[str, Any]:
    """Supersede a submission and issue a fresh link for the same round."""
    sub = await _hr_submission(db, company_id, submission_id)
    if sub["superseded_at"] is not None:
        raise TaskError(409, "A newer link has already been issued for this round.")
    enrolment = (
        await db.execute(
            text(
                "SELECT e.id, e.company_id, e.applicant_id, a.full_name, a.email"
                "  FROM enrolments e JOIN applicants a ON a.id = e.applicant_id"
                " WHERE e.id = :e AND e.company_id = :c AND e.deleted_at IS NULL"
            ),
            {"e": sub["enrolment_id"], "c": company_id},
        )
    ).mappings().first()
    if enrolment is None:
        raise TaskError(404, "Application not found.")
    round_ = await _round(db, company_id, sub["round_id"])
    workflow = (
        await db.execute(
            text("SELECT created_by_user_id FROM workflow_rounds wr"
                 " JOIN workflows w ON w.id = wr.workflow_id WHERE wr.id = :r"),
            {"r": sub["round_id"]},
        )
    ).mappings().first()
    new_id = await issue(
        db, enrolment=dict(enrolment), round_=round_, workflow=dict(workflow or {}),
    )
    if new_id is None:
        raise TaskError(409, "This round has no task configuration to reissue against.")
    await _event(
        db, company_id=company_id, submission_id=submission_id, round_id=sub["round_id"],
        action="reissued", actor_type="user", actor=actor, details={"new_submission_id": str(new_id)},
    )
    _audit(db, actor=actor, action="task_submission.reissued", resource_id=submission_id,
          details={"company_id": str(company_id), "new_submission_id": str(new_id)}, meta=meta)
    return _submission_out(await _hr_submission(db, company_id, new_id))


async def withdraw(
    db: AsyncSession, *, company_id: uuid.UUID, submission_id: uuid.UUID, reason: str | None,
    actor: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    sub = await _hr_submission(db, company_id, submission_id)
    if sub["status"] not in ("assigned", "in_progress"):
        raise TaskError(409, f"This submission is {sub['status']}; it can no longer be withdrawn.")
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "UPDATE task_submissions SET status = 'withdrawn', token_hash = NULL, updated_at = :n"
            " WHERE id = :i"
        ),
        {"n": now, "i": submission_id},
    )
    await _event(
        db, company_id=company_id, submission_id=submission_id, round_id=sub["round_id"],
        action="withdrawn", actor_type="user", actor=actor, details=_reason_facts(reason),
    )
    _audit(db, actor=actor, action="task_submission.withdrawn", resource_id=submission_id,
          details={"company_id": str(company_id), **_reason_facts(reason)}, meta=meta)
    return _submission_out(await _hr_submission(db, company_id, submission_id))


async def artifact_download(
    db: AsyncSession, *, company_id: uuid.UUID, submission_id: uuid.UUID, response_id: uuid.UUID,
    actor: uuid.UUID | None, actor_type: str, meta: RequestMeta,
) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                "SELECT storage_key, original_name FROM task_responses"
                " WHERE id = :i AND submission_id = :s AND company_id = :c"
                "   AND redacted_at IS NULL"
            ),
            {"i": response_id, "s": submission_id, "c": company_id},
        )
    ).mappings().first()
    if row is None or row["storage_key"] is None:
        raise TaskError(404, "Artifact not found.")
    url = await store.signed_download(settings, row["storage_key"], row["original_name"] or "artifact")
    await _event(
        db, company_id=company_id, submission_id=submission_id, round_id=None,
        action="artifact_downloaded", actor_type=actor_type, actor=actor,
    )
    _audit(db, actor=actor, action="task_response.downloaded", resource_id=response_id,
          details={"company_id": str(company_id), "submission_id": str(submission_id)}, meta=meta,
          actor_type="user" if actor_type == "user" else "system")
    return {"url": url, "expires_in": store.PRESIGN_SECONDS}


# ---------------------------------------------------------------------------
# Reviewers — through the existing scorecard ownership check
# ---------------------------------------------------------------------------
async def submission_for_reviewer(
    db: AsyncSession, *, company_id: uuid.UUID, scorecard_id: uuid.UUID, interviewer_user_id: uuid.UUID,
) -> dict[str, Any]:
    from app.interviewer_scorecards import _load_owned  # noqa: PLC0415 — avoid a cycle

    card = await _load_owned(
        db, scorecard_id=scorecard_id, interviewer_user_id=interviewer_user_id, company_id=company_id,
    )
    row = (
        await db.execute(
            text(
                "SELECT s.* FROM task_submissions s"
                " WHERE s.enrolment_id = :e AND s.round_id = :r AND s.company_id = :c"
                " ORDER BY s.attempt_no DESC LIMIT 1"
            ),
            {"e": card["enrolment_id"], "r": card["round_id"], "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise TaskError(404, "No submission for this round yet.")
    sub = dict(row)
    await _event(
        db, company_id=company_id, submission_id=sub["id"], round_id=sub["round_id"],
        action="submission_viewed", actor_type="user", actor=interviewer_user_id,
    )
    materials = await list_materials(db, company_id=company_id, round_id=sub["round_id"])
    return {
        "submission_id": str(sub["id"]), "status": sub["status"], "kind": sub["kind"],
        "submitted_at": _iso(sub["submitted_at"]), "materials": materials,
        "responses": await _responses_for(db, sub["id"]),
    }


async def reviewer_artifact_download(
    db: AsyncSession, *, company_id: uuid.UUID, scorecard_id: uuid.UUID,
    interviewer_user_id: uuid.UUID, response_id: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    from app.interviewer_scorecards import _load_owned  # noqa: PLC0415

    card = await _load_owned(
        db, scorecard_id=scorecard_id, interviewer_user_id=interviewer_user_id, company_id=company_id,
    )
    row = (
        await db.execute(
            text(
                "SELECT r.storage_key, r.original_name FROM task_responses r"
                "  JOIN task_submissions s ON s.id = r.submission_id"
                " WHERE r.id = :i AND s.enrolment_id = :e AND s.round_id = :rd"
                "   AND s.company_id = :c AND r.redacted_at IS NULL"
            ),
            {"i": response_id, "e": card["enrolment_id"], "rd": card["round_id"], "c": company_id},
        )
    ).mappings().first()
    if row is None or row["storage_key"] is None:
        raise TaskError(404, "Artifact not found.")
    url = await store.signed_download(settings, row["storage_key"], row["original_name"] or "artifact")
    await _event(
        db, company_id=company_id, submission_id=None, round_id=card["round_id"],
        action="artifact_downloaded", actor_type="user", actor=interviewer_user_id,
    )
    return {"url": url, "expires_in": store.PRESIGN_SECONDS}


# ---------------------------------------------------------------------------
# Candidate link rotation (signed-in candidate)
# ---------------------------------------------------------------------------
async def candidate_link(
    db: AsyncSession, *, user_id: uuid.UUID, submission_id: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                "SELECT s.id, s.company_id, s.applicant_id FROM task_submissions s"
                "  JOIN applicants a ON a.id = s.applicant_id"
                " WHERE s.id = :i AND a.user_id = :u AND s.redacted_at IS NULL"
                "   AND s.status IN ('assigned', 'in_progress') FOR UPDATE OF s"
            ),
            {"i": submission_id, "u": user_id},
        )
    ).mappings().first()
    if row is None:
        raise TaskError(404, NOT_AVAILABLE)
    raw = mint_task_token()
    await db.execute(
        text("UPDATE task_submissions SET token_hash = :h, updated_at = now() WHERE id = :i"),
        {"h": hash_task_token(raw), "i": submission_id},
    )
    await _event(
        db, company_id=row["company_id"], submission_id=submission_id, round_id=None,
        action="link_rotated", actor_type="candidate", actor=user_id,
    )
    return {"url": task_link(raw)}


# ---------------------------------------------------------------------------
# Sweep — close_due
# ---------------------------------------------------------------------------
_DUE_SQL = """
SELECT id, company_id, status, due_at, time_limit_seconds, started_at
  FROM task_submissions
 WHERE status IN ('assigned', 'in_progress') AND redacted_at IS NULL
   AND (
        due_at + make_interval(secs => :grace) <= now()
     OR (time_limit_seconds IS NOT NULL AND started_at IS NOT NULL
         AND started_at + make_interval(secs => time_limit_seconds + :grace) <= now())
   )
 ORDER BY due_at
 LIMIT 200
 FOR UPDATE SKIP LOCKED
"""


async def close_due(db: AsyncSession) -> int:
    """Auto-close timed-out submissions: any WORK becomes ``submitted``
    (``closed_by='time_limit'``); an untouched ``assigned`` row becomes
    ``expired``. Caller commits."""
    rows = (
        await db.execute(text(_DUE_SQL), {"grace": settings.task_submit_grace_seconds})
    ).mappings().all()
    now = datetime.now(tz=UTC)
    closed = 0
    for r in rows:
        has_work = await db.scalar(
            text("SELECT 1 FROM task_responses WHERE submission_id = :s AND redacted_at IS NULL"),
            {"s": r["id"]},
        )
        if has_work:
            await db.execute(
                text(
                    "UPDATE task_submissions SET status = 'submitted', submitted_at = :n,"
                    " closed_by = 'time_limit', updated_at = :n WHERE id = :i"
                ),
                {"n": now, "i": r["id"]},
            )
            action = "closed_at_time_limit"
        else:
            await db.execute(
                text("UPDATE task_submissions SET status = 'expired', updated_at = :n WHERE id = :i"),
                {"n": now, "i": r["id"]},
            )
            action = "expired"
        await _event(
            db, company_id=r["company_id"], submission_id=r["id"], round_id=None,
            action=action, actor_type="system", actor=None,
        )
        closed += 1
    if closed:
        log.info("job_tasks.close_due", closed=closed)
    return closed


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------
_PURGEABLE_SQL = """
SELECT s.id, s.company_id FROM task_submissions s
 WHERE s.redacted_at IS NULL
   AND s.status IN ('submitted', 'expired', 'withdrawn')
   AND COALESCE(s.submitted_at, s.updated_at) < :cutoff
 LIMIT :lim
"""
_PURGE_BATCH = 500


async def purge(db: AsyncSession, *, retention_days: int, dry_run: bool) -> int:
    """Withdraw nothing further, redact responses and clear the link,
    ``retention_days`` after a submission closed. Honours RETENTION_DRY_RUN.
    Caller commits."""
    cutoff = datetime.now(tz=UTC) - timedelta(days=retention_days)
    rows = (await db.execute(text(_PURGEABLE_SQL), {"cutoff": cutoff, "lim": _PURGE_BATCH})).all()
    if dry_run or not rows:
        log.info("job_tasks.retention", candidates=len(rows), dry_run=dry_run)
        return len(rows)
    now = datetime.now(tz=UTC)
    removed_files = 0
    for sub_id, company_id in rows:
        keys = (
            await db.execute(
                text(
                    "SELECT storage_key FROM task_responses"
                    " WHERE submission_id = :s AND storage_key IS NOT NULL AND redacted_at IS NULL"
                ),
                {"s": sub_id},
            )
        ).scalars().all()
        keys = [k for k in keys if k] + await store.keys_under(settings, submission_prefix(company_id, sub_id))
        keys = list(dict.fromkeys(keys))
        if keys:
            removed = await store.remove(settings, keys)
            if removed < len(keys):
                log.warning("job_tasks.retention.shortfall", submission_id=str(sub_id))
                continue
            removed_files += removed
        await db.execute(
            text(
                "UPDATE task_responses SET text_value = NULL, link_url = NULL, title = NULL,"
                " description = NULL, storage_key = NULL, original_name = NULL, redacted_at = :n,"
                " updated_at = :n WHERE submission_id = :s AND redacted_at IS NULL"
            ),
            {"n": now, "s": sub_id},
        )
        await db.execute(
            text(
                "UPDATE task_submissions SET token_hash = NULL, redacted_at = :n, updated_at = :n"
                " WHERE id = :i"
            ),
            {"n": now, "i": sub_id},
        )
        # No task_events row here: the append-only action list (migration
        # a5d7f9b1c3e8) is a fixed set of candidate/HR/system LIFECYCLE
        # actions and deliberately has no 'redacted' member — a redaction is
        # not a new thing that happened to the submission's story, it is the
        # platform ending what the story may still say. structlog (below) and
        # the AuditLog row erasure writes are the record of it.
    log.info("job_tasks.retention.purged", submissions=len(rows), files=removed_files)
    return len(rows)
