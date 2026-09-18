"""The engagement model: which client a run was for, and who may decide on it (R-24).

Before this module the only engagement-shaped thing a run carried was
``RUN.label``, a free-text string the browser never sent. A practice running
the engine across clients must be able to answer, from the data, which client
a run belonged to, who sponsored it, what the data cut date and scope were,
and whether client A's backlog could ever appear in client B's session.

An ``EngagementRecord`` holds the cover facts and the reviewer roster; the
roster is the concrete answer to open decision D-08 ("which reviewer role
can Accept per domain") for one engagement. Three tables carry it: ``ENGAGEMENT``,
``ENGAGEMENT_REVIEWER`` and ``RUN_ENGAGEMENT``, which links a run to its
engagement with the ``config_version`` it ran on. The link table exists
because ``RUN`` belongs to ``dpre/store.py``; adding ``engagement_id`` there is
a one-line migration this module does not make. Segregation between clients
is by workspace (one ``engine.db`` per engagement, ``dpre serve --workspace``),
which this module records rather than replaces; ``unattributed_runs`` is the
detective control that finds a run nobody claimed.

``dpre/export/engagement.py::Engagement`` is the cover-page view the pack
already uses; ``to_export_engagement`` converts to it, so the export layer
does not change.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from ..export.engagement import Engagement as ExportEngagement

DEFAULT_CONFIDENTIALITY = "Confidential - prepared for the named recipients only"
ENGAGEMENT_STATUSES = ("active", "closed")

# Roles a roster entry may hold. The first five are the HTTP roles in
# dpre/server/security.py::ROLE_ACTIONS; the last three are engagement roles
# the specification names but the API does not need to authenticate.
ROSTER_ROLES = ("reviewer", "steward", "council", "catalog_admin", "operator",
                "privacy_officer", "programme_sponsor", "rationalization_lead")

# Which roster role may take which decision (D-08). Kept alongside the HTTP
# matrix rather than in place of it: the API authorises requests, the roster
# says who was appointed on this engagement.
DECISION_ROLES: dict[str, tuple[str, ...]] = {
    "Accept": ("reviewer",),
    "AcceptWithException": ("reviewer",),
    "Reject": ("reviewer",),
    "Merge": ("reviewer",),
    "Split": ("reviewer",),
    "Defer": ("reviewer",),
    "Override": ("reviewer",),
    "Reverse": ("reviewer", "council"),
    "resolve_conflict": ("steward",),
    "accept_metric_name": ("steward",),
    "approve_weights": ("council",),
    "waive": ("council",),
    "configure": ("council",),
    "decide": ("council", "programme_sponsor", "privacy_officer", "catalog_admin",
               "rationalization_lead"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS ENGAGEMENT (
    engagement_id TEXT PRIMARY KEY, client TEXT NOT NULL, code TEXT, name TEXT, sponsor TEXT,
    lead_partner TEXT, manager TEXT, firm TEXT, start_date TEXT, end_date TEXT, scope TEXT,
    domains TEXT, data_cut_date TEXT, confidentiality TEXT, status TEXT, created_at TEXT,
    created_by TEXT, json TEXT
);
CREATE TABLE IF NOT EXISTS ENGAGEMENT_REVIEWER (
    engagement_id TEXT NOT NULL, identity TEXT NOT NULL, role TEXT NOT NULL, domains TEXT,
    name TEXT, PRIMARY KEY (engagement_id, identity, role)
);
CREATE TABLE IF NOT EXISTS RUN_ENGAGEMENT (
    run_id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, config_version TEXT,
    attached_at TEXT, attached_by TEXT
);
CREATE INDEX IF NOT EXISTS IX_RUN_ENGAGEMENT ON RUN_ENGAGEMENT (engagement_id);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


class EngagementError(ValueError):
    """Raised for an engagement without a client, or a roster entry with an unknown role."""


@dataclass(frozen=True)
class RosterEntry:
    identity: str
    role: str
    domains: tuple[str, ...] = ()     # empty means every domain
    name: str = ""

    def covers(self, domain: str) -> bool:
        if not self.domains or not domain:
            return True
        wanted = domain.strip().casefold()
        return any(d.strip().casefold() == wanted for d in self.domains)


@dataclass(frozen=True)
class EngagementRecord:
    client: str
    code: str = ""
    name: str = "Data product rationalisation"
    engagement_id: str = ""
    sponsor: str = ""
    lead_partner: str = ""
    manager: str = ""
    firm: str = ""
    start_date: str = ""
    end_date: str = ""
    scope: str = ""
    domains: tuple[str, ...] = ()
    data_cut_date: str = ""
    confidentiality: str = DEFAULT_CONFIDENTIALITY
    status: str = "active"
    reviewers: tuple[RosterEntry, ...] = ()
    created_at: str = ""
    created_by: str = ""

    def __post_init__(self) -> None:
        if not (self.client or "").strip():
            raise EngagementError("an engagement must name the client")
        if self.status not in ENGAGEMENT_STATUSES:
            raise EngagementError(f"status must be one of {', '.join(ENGAGEMENT_STATUSES)}")
        for entry in self.reviewers:
            if entry.role not in ROSTER_ROLES:
                raise EngagementError(f"unknown roster role '{entry.role}'; expected one of "
                                      + ", ".join(ROSTER_ROLES))
            if not entry.identity:
                raise EngagementError("a roster entry needs an identity")
        for label in ("start_date", "end_date", "data_cut_date"):
            value = getattr(self, label)
            if value:
                _dt.date.fromisoformat(value)     # raises on a malformed date
        if not self.engagement_id:
            object.__setattr__(self, "engagement_id", engagement_id_for(self.client, self.code))

    @property
    def title(self) -> str:
        return f"{self.client} - {self.name}"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["domains"] = list(self.domains)
        payload["reviewers"] = [asdict(r) | {"domains": list(r.domains)} for r in self.reviewers]
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EngagementRecord":
        fields = set(cls.__dataclass_fields__)
        data = {k: v for k, v in payload.items() if k in fields}
        data["domains"] = tuple(data.get("domains") or ())
        data["reviewers"] = tuple(
            RosterEntry(identity=r["identity"], role=r["role"],
                        domains=tuple(r.get("domains") or ()), name=r.get("name", ""))
            for r in (data.get("reviewers") or ()))
        return cls(**data)

    def roster_for(self, role: str) -> list[RosterEntry]:
        return [r for r in self.reviewers if r.role == role]


def engagement_id_for(client: str, code: str = "") -> str:
    """Stable id from client and code, so the same engagement gets the same id everywhere."""
    material = f"{client.strip().casefold()}|{(code or '').strip().casefold()}"
    return "ENG-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:10].upper()


# --------------------------------------------------------------------------
# D-08: who may decide what on this engagement
# --------------------------------------------------------------------------

def reviewer_may(record: EngagementRecord, identity: str, action: str,
                 domain: str = "") -> tuple[bool, str]:
    """Whether ``identity`` is on the roster with a role that may take ``action`` in ``domain``.

    Returns ``(allowed, reason)``; the reason is what the API should put in a
    403 and what the decision row should carry as the appointment behind it.
    """
    roles = DECISION_ROLES.get(action)
    if roles is None:
        return False, f"unknown action '{action}'"
    entries = [r for r in record.reviewers if r.identity.casefold() == (identity or "").casefold()]
    if not entries:
        return False, f"{identity or 'anonymous'} is not on the roster of {record.engagement_id}"
    for entry in entries:
        if entry.role in roles and entry.covers(domain):
            return True, f"{identity} appointed {entry.role} for " + (
                ", ".join(entry.domains) if entry.domains else "every domain")
    held = ", ".join(sorted({e.role for e in entries}))
    return False, (f"{identity} holds {held} on {record.engagement_id}; {action} needs "
                   f"{' or '.join(roles)}" + (f" covering {domain}" if domain else ""))


def hosted_roles(record: EngagementRecord) -> dict[str, list[str]]:
    """identity -> roles, in the shape dpre/server/security.py's token map uses."""
    out: dict[str, list[str]] = {}
    for entry in record.reviewers:
        out.setdefault(entry.identity, [])
        if entry.role not in out[entry.identity]:
            out[entry.identity].append(entry.role)
    return out


def token_map_entries(record: EngagementRecord) -> dict[str, dict[str, Any]]:
    """The roster as ``{identity: {identity, roles, domains}}`` for a DPRE_TOKENS file.

    Keys are identities, not tokens: the operator issues the tokens. Only the
    roles the HTTP matrix knows are emitted.
    """
    http_roles = ("reviewer", "steward", "council", "catalog_admin", "operator")
    out: dict[str, dict[str, Any]] = {}
    for entry in record.reviewers:
        if entry.role not in http_roles:
            continue
        item = out.setdefault(entry.identity, {"identity": entry.identity, "roles": [],
                                               "domains": []})
        if entry.role not in item["roles"]:
            item["roles"].append(entry.role)
        for domain in entry.domains:
            if domain not in item["domains"]:
                item["domains"].append(domain)
    return out


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def save_engagement(connection: sqlite3.Connection, record: EngagementRecord,
                    created_by: str = "", created_at: str | None = None) -> EngagementRecord:
    """Insert or replace the engagement and its roster; returns the stored record."""
    ensure_schema(connection)
    existing = get_engagement(connection, record.engagement_id)
    stored = replace(
        record,
        created_at=existing.created_at if existing else (created_at or _now()),
        created_by=existing.created_by if existing and existing.created_by
        else (created_by or record.created_by))
    connection.execute(
        "INSERT OR REPLACE INTO ENGAGEMENT VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (stored.engagement_id, stored.client, stored.code, stored.name, stored.sponsor,
         stored.lead_partner, stored.manager, stored.firm, stored.start_date, stored.end_date,
         stored.scope, json.dumps(list(stored.domains)), stored.data_cut_date,
         stored.confidentiality, stored.status, stored.created_at, stored.created_by,
         json.dumps(stored.to_dict(), sort_keys=True)))
    connection.execute("DELETE FROM ENGAGEMENT_REVIEWER WHERE engagement_id = ?",
                       (stored.engagement_id,))
    connection.executemany(
        "INSERT INTO ENGAGEMENT_REVIEWER VALUES (?,?,?,?,?)",
        [(stored.engagement_id, r.identity, r.role, json.dumps(list(r.domains)), r.name)
         for r in stored.reviewers])
    connection.commit()
    return stored


def get_engagement(connection: sqlite3.Connection, engagement_id: str) -> EngagementRecord | None:
    ensure_schema(connection)
    row = connection.execute("SELECT json FROM ENGAGEMENT WHERE engagement_id = ?",
                             (engagement_id,)).fetchone()
    if not row:
        return None
    return EngagementRecord.from_dict(json.loads(row[0]))


def list_engagements(connection: sqlite3.Connection, status: str | None = None) -> list[dict]:
    ensure_schema(connection)
    sql = "SELECT engagement_id, client, code, name, sponsor, data_cut_date, status, created_at " \
          "FROM ENGAGEMENT"
    params: tuple = ()
    if status:
        sql += " WHERE status = ?"
        params = (status,)
    cursor = connection.execute(sql + " ORDER BY client, code", params)
    names = [c[0] for c in cursor.description]
    rows = [dict(zip(names, r)) for r in cursor.execute(sql + " ORDER BY client, code", params)]
    for row in rows:
        row["runs"] = connection.execute(
            "SELECT COUNT(*) FROM RUN_ENGAGEMENT WHERE engagement_id = ?",
            (row["engagement_id"],)).fetchone()[0]
    return rows


def close_engagement(connection: sqlite3.Connection, engagement_id: str) -> EngagementRecord:
    record = get_engagement(connection, engagement_id)
    if record is None:
        raise EngagementError(f"engagement {engagement_id} not found")
    return save_engagement(connection, replace(record, status="closed"))


def attach_run(connection: sqlite3.Connection, run_id: str, engagement_id: str,
               config_version: str = "", attached_by: str = "",
               attached_at: str | None = None) -> dict:
    """Link a run to its engagement. A run belongs to exactly one engagement."""
    ensure_schema(connection)
    if get_engagement(connection, engagement_id) is None:
        raise EngagementError(f"engagement {engagement_id} not found; create it first")
    existing = connection.execute("SELECT engagement_id FROM RUN_ENGAGEMENT WHERE run_id = ?",
                                  (run_id,)).fetchone()
    if existing and existing[0] != engagement_id:
        raise EngagementError(f"run {run_id} is already attributed to {existing[0]}; a run is "
                              "never moved between clients")
    row = {"run_id": run_id, "engagement_id": engagement_id, "config_version": config_version,
           "attached_at": attached_at or _now(), "attached_by": attached_by}
    connection.execute("INSERT OR REPLACE INTO RUN_ENGAGEMENT VALUES (?,?,?,?,?)",
                       tuple(row.values()))
    connection.commit()
    return row


def engagement_for_run(connection: sqlite3.Connection, run_id: str) -> EngagementRecord | None:
    ensure_schema(connection)
    row = connection.execute("SELECT engagement_id FROM RUN_ENGAGEMENT WHERE run_id = ?",
                             (run_id,)).fetchone()
    return get_engagement(connection, row[0]) if row else None


def run_attribution(connection: sqlite3.Connection, run_id: str) -> dict | None:
    ensure_schema(connection)
    cursor = connection.execute("SELECT * FROM RUN_ENGAGEMENT WHERE run_id = ?", (run_id,))
    names = [c[0] for c in cursor.description]
    row = cursor.fetchone()
    return dict(zip(names, row)) if row else None


def runs_for_engagement(connection: sqlite3.Connection, engagement_id: str) -> list[dict]:
    """RUN rows for an engagement, newest first; the link rows alone if RUN is absent."""
    ensure_schema(connection)
    has_run_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'RUN'").fetchone()
    if has_run_table:
        cursor = connection.execute(
            "SELECT r.run_id, r.mode, r.industry, r.catalog, r.as_of_date, r.started_at, "
            "r.published, r.label, e.config_version, e.attached_at FROM RUN_ENGAGEMENT e "
            "JOIN RUN r ON r.run_id = e.run_id WHERE e.engagement_id = ? "
            "ORDER BY r.started_at DESC", (engagement_id,))
    else:
        cursor = connection.execute(
            "SELECT run_id, engagement_id, config_version, attached_at FROM RUN_ENGAGEMENT "
            "WHERE engagement_id = ? ORDER BY attached_at DESC", (engagement_id,))
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, r)) for r in cursor.fetchall()]


def unattributed_runs(connection: sqlite3.Connection) -> list[str]:
    """Runs in RUN with no engagement: the detective control behind C-23."""
    ensure_schema(connection)
    has_run_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'RUN'").fetchone()
    if not has_run_table:
        return []
    return [r[0] for r in connection.execute(
        "SELECT r.run_id FROM RUN r LEFT JOIN RUN_ENGAGEMENT e ON e.run_id = r.run_id "
        "WHERE e.run_id IS NULL ORDER BY r.started_at")]


# --------------------------------------------------------------------------
# Converters: the export layer's cover-page view
# --------------------------------------------------------------------------

def to_export_engagement(record: EngagementRecord, prepared_for: str = "",
                         as_of_override: str = "") -> ExportEngagement:
    """The ``dpre.export.engagement.Engagement`` the pack, dossiers and summary take."""
    return ExportEngagement(
        client=record.client,
        engagement=record.name,
        reference=record.code,
        prepared_by=record.firm or "Data product engagement team",
        prepared_for=prepared_for or record.sponsor or "Data product council",
        partner=record.lead_partner,
        manager=record.manager,
        confidentiality=record.confidentiality,
        as_of_override=as_of_override or record.data_cut_date,
    )


def from_export_engagement(engagement: ExportEngagement, **extra: Any) -> EngagementRecord:
    """Lift a cover-page ``Engagement`` into a record; extra fields fill what it lacks."""
    data = {
        "client": engagement.client, "code": engagement.reference, "name": engagement.engagement,
        "lead_partner": engagement.partner, "manager": engagement.manager,
        "firm": engagement.prepared_by, "sponsor": engagement.prepared_for,
        "confidentiality": engagement.confidentiality,
        "data_cut_date": engagement.as_of_override,
    }
    data.update(extra)
    return EngagementRecord.from_dict(data)


def run_context(record: EngagementRecord | None, manifest: Any = None,
                label: str = "") -> dict[str, Any]:
    """What the topbar, the run pill and the Runs tab should show for a run."""
    return {
        "client": record.client if record else "",
        "engagement": record.name if record else "",
        "engagement_id": record.engagement_id if record else "",
        "code": record.code if record else "",
        "sponsor": record.sponsor if record else "",
        "data_cut_date": record.data_cut_date if record else "",
        "confidentiality": record.confidentiality if record else DEFAULT_CONFIDENTIALITY,
        "label": label or (getattr(manifest, "label", "") if manifest else ""),
        "run_id": getattr(manifest, "run_id", "") if manifest else "",
        "as_of_date": getattr(manifest, "as_of_date", "") if manifest else "",
        "synthetic": bool(getattr(manifest, "synthetic", False)) if manifest else False,
    }
