"""Membership-scoped workspace evidence and explicit, email-bound invitations.

This module reads imported customer claims. It does not run adapters, infer
agent correctness from prose, send mail, or turn absent coverage into a pass.
"""
from __future__ import annotations

import re
import secrets
from collections import Counter
from datetime import timedelta
from functools import lru_cache
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, insert, select, update

from .team_db import invitations, memberships, opaque_id, projects, reports, suites, token_hash, utcnow

VERDICTS = ("PASS", "FAIL", "INCONCLUSIVE", "UNSUPPORTED")
NOTICE = ("Customer-generated reports, explicitly uploaded by your team. Counts exclude expired reports. "
    "Suite snapshots use the latest matching retained evidence, which may mix agent builds and seeds; they are not release approval. "
    "Suggestions follow recorded checks and require local reproduction; they are not an automatic diagnosis or repair.")
CHECK_GUIDANCE = {
    "no_duplicate_effects": "Reuse one operation key for the same logical request across retries. After a lost response, reconcile the existing refund before attempting another write.",
    "correct_target": "Resolve the authenticated requester, customer and order together; reject ambiguous matches and check order ownership before a write.",
    "amount_authorization": "Keep amounts in integer minor units with explicit currency. Check the authorized amount and remaining refundable balance; do not silently change the request.",
    "exact_amount": "Reconcile the authorized request against actual refund records and terminal status. Distinguish a missing refund from a pending one before retrying.",
    "authorized_writes": "Check requester authorization and current write permission before each operation. Treat permission refusal as a refusal, not successful completion.",
    "ticket_target": "Confirm the ticket belongs to the same authorized customer and order before adding a note or changing its status.",
    "ticket_consistent": "Retry the ticket step independently after a successful refund. Record the actual refund reference once instead of repeating the payment workflow.",
    "claims_supported": "Build completion claims from observed refund IDs and terminal statuses. An acknowledgement or pending state does not establish success.",
    "required_outcome_claim": "Return a structured completion claim supported by the result, or escalate only when this drill explicitly permits it.",
    "bounded_retries": "Cap attempts for each logical request, retain its operation key, and stop or escalate at the defined budget.",
    "retry_delay": "Honor the reported retry delay before the next payment attempt and keep the total wait within the task deadline.",
    "event_dedup": "Deduplicate repeated event IDs and preserve the logical operation identity for ticket effects; a redelivery must not create a fresh note or refund.",
    "concurrent_edit_preserved": "Use the ticket version precondition, reread conflicts, and preserve concurrent customer changes before retrying the update.",
    "logical_deadline": "Bound reconciliation and polling by the task deadline; keep an unresolved result explicit rather than claiming completion.",
    "adapter_capabilities": "Declare and implement the drill's required adapter capabilities, including event handling when required, before rerunning.",
}


@lru_cache(maxsize=1)
def catalog():
    from breakroom.scenarios import content_hash, list_cases
    return tuple({key: case[key] for key in ("case_id", "case_version", "name", "summary", "severity", "tags")} |
        {"manifest_hash": content_hash(case)} for case in list_cases())


def counts(values):
    result = {verdict: 0 for verdict in VERDICTS}
    result.update(Counter(values))
    return {"total": sum(result.values()), **result}


def compatible(row, expected):
    report, privacy = row["upload"]["report"], row["upload"]["privacy"]
    # Redaction rewrites identifiers and manifest hashes. The envelope retains
    # original hashes, including the original source for generated variations.
    hashes = {privacy["original_case_manifest_hash"], privacy.get("original_source_manifest_hash")}
    return (row["case_id"] == expected["case_id"] and report["case"]["case_version"] == expected["case_version"]
        and expected["manifest_hash"] in hashes)


def evidence_status(row):
    report = row["upload"]["report"]
    checks = report["checks"]
    if row["verdict"] == "FAIL" or any(check["status"] == "fail" for check in checks):
        return "FAIL"
    if row["verdict"] == "UNSUPPORTED":
        return "UNSUPPORTED"
    by_id = {check["id"]: check for check in checks}
    if (row["verdict"] != "PASS" or report["execution"]["status"] != "completed"
            or any(check["status"] == "unknown" for check in checks)
            or by_id.get("fault_coverage", {}).get("status") != "pass"):
        return "INCONCLUSIVE"
    return "PASS"


def recommendations(row):
    checks = row["upload"]["report"]["checks"]
    failed = [check["id"] for check in checks if check["status"] == "fail"]
    unknown = [check["id"] for check in checks if check["status"] == "unknown"]
    advice = []
    if failed:
        advice.append("Inspect the recorded evidence for failed checks: " + ", ".join(failed) + ". Reproduce locally before changing the adapter.")
        for check_id in failed:
            if check_id in CHECK_GUIDANCE:
                advice.append(check_id + ": " + CHECK_GUIDANCE[check_id])
        if row["case_id"] == "reordered-events":
            advice.append("Reconcile reordered events against authoritative current refund state; delivery order must not regress a terminal result.")
    if unknown:
        advice.append("Collect the missing evidence for: " + ", ".join(unknown) + ". Unknown results cannot pass a release check.")
    coverage = next((check for check in checks if check["id"] == "fault_coverage"), None)
    if not coverage or coverage["status"] != "pass":
        advice.append("Rerun with the required fault exercised; an untriggered fault leaves coverage incomplete.")
    if row["verdict"] == "UNSUPPORTED":
        advice.append("Implement the required adapter capabilities before treating this drill as covered.")
    if not advice:
        advice.append("Compare this result with the same case, seed and agent build after your next change; a single passing upload does not prove broader safety.")
    return advice


def suite_readiness(suite, rows):
    entries = []
    for case in suite["cases"]:
        same_case = [row for row in rows if row["case_id"] == case["case_id"]]
        matching = next((row for row in same_case if compatible(row, case)), None)
        status = evidence_status(matching) if matching else "INCOMPATIBLE" if same_case else "MISSING"
        entries.append({**case, "status": status, "report_id": matching["id"] if matching else None})
    summary = {status: 0 for status in (*VERDICTS, "MISSING", "INCOMPATIBLE")}
    summary.update(Counter(entry["status"] for entry in entries))
    status = "FAIL" if summary["FAIL"] else "PASS" if entries and all(entry["status"] == "PASS" for entry in entries) else "INCONCLUSIVE"
    return {"id": suite["id"], "name": suite["name"], "status": status, "counts": summary, "cases": entries}


class InvitationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=254)
    role: Literal["viewer", "developer"]
    expires_days: int = Field(default=7, ge=1, le=7)

    @field_validator("email")
    @classmethod
    def email_address(cls, value):
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Provide an email address")
        return value.casefold()


class InvitationAccept(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


def invitation_view(row):
    return {key: row[key] for key in ("id", "email", "role", "created_at", "expires_at", "revoked_at", "accepted_at")}


def configure_workspace(app, settings, store, identity, authorize, billing, project_view, report_view):
    def human(who):
        if who["key"]:
            raise HTTPException(403, "This action requires a signed-in person")
        return who

    @app.get("/api/team/catalog")
    def get_catalog(request: Request):
        with store.engine.connect() as con:
            human(identity(request, con))
        return {"items": catalog(), "count": len(catalog())}

    @app.get("/api/team/workspace")
    def workspace(request: Request):
        with store.engine.connect() as con:
            who = human(identity(request, con))
            owned = list(con.execute(select(projects, memberships.c.role).join(memberships).where(
                memberships.c.user_id == who["user"]["id"]).order_by(projects.c.created_at.desc())).mappings())
            ids = [project["id"] for project in owned]
            groups = con.execute(select(reports.c.project_id, reports.c.verdict, func.count(), func.max(reports.c.created_at)).where(
                reports.c.project_id.in_(ids), reports.c.expires_at > utcnow()).group_by(reports.c.project_id, reports.c.verdict)).all()
            project_counts = {project_id: counts([]) for project_id in ids}
            last = {project_id: None for project_id in ids}
            for project_id, verdict, count, timestamp in groups:
                project_counts[project_id][verdict] = count
                project_counts[project_id]["total"] += count
                last[project_id] = max(last[project_id], timestamp) if last[project_id] else timestamp
            suite_counts = dict(con.execute(select(suites.c.project_id, func.count()).where(suites.c.project_id.in_(ids)).group_by(suites.c.project_id)).all())
            values = [project_view(project, project["role"]) | {"report_counts": project_counts[project["id"]],
                "suite_count": suite_counts.get(project["id"], 0), "last_report_at": last[project["id"]]} for project in owned]
            recent = con.execute(select(reports, projects.c.name.label("project_name")).join(projects).where(
                reports.c.project_id.in_(ids), reports.c.expires_at > utcnow()).order_by(reports.c.created_at.desc(), reports.c.id.desc()).limit(10)).mappings()
            recent_values = [report_view(row) | {"project_name": row["project_name"]} for row in recent]
            totals = {"projects": len(values), "reports": sum(value["report_counts"]["total"] for value in values),
                **{status: sum(value["report_counts"][status] for value in values) for status in VERDICTS}}
            return {"projects": values, "totals": totals, "recent_reports": recent_values, "notice": NOTICE, "provenance": "customer_generated"}

    @app.get("/api/team/projects/{project_id}/insights")
    def insights(project_id: str, request: Request):
        with store.engine.connect() as con:
            authorize(request, con, project_id)
            # Project imports have a hard cap of 1000. Read only this tenant's
            # retained evidence; the response includes bounded checks, no events.
            projection = func.jsonb_build_object("privacy", reports.c.upload["privacy"], "report",
                func.jsonb_build_object("case", func.jsonb_build_object("case_version", reports.c.upload["report"]["case"]["case_version"]),
                    "execution", func.jsonb_build_object("status", reports.c.upload["report"]["execution"]["status"]),
                    "checks", reports.c.upload["report"]["checks"]))
            rows = list(con.execute(select(reports.c.id, reports.c.case_id, reports.c.verdict, reports.c.created_at,
                projection.label("upload")).where(reports.c.project_id == project_id,
                reports.c.expires_at > utcnow()).order_by(reports.c.created_at.desc(), reports.c.id.desc()).limit(1000)).mappings())
            latest = {}
            for row in rows:
                if row["case_id"] not in latest:
                    case = row["upload"]["report"]["case"]
                    latest[row["case_id"]] = {"case_id": row["case_id"], "report_id": row["id"], "verdict": evidence_status(row),
                        "created_at": row["created_at"], "case_version": case["case_version"],
                        "manifest_hash": row["upload"]["privacy"]["original_case_manifest_hash"],
                        "checks": row["upload"]["report"]["checks"], "recommendations": recommendations(row)}
            missing, incompatible = [], []
            for case in catalog():
                same = [row for row in rows if row["case_id"] == case["case_id"]]
                if not same:
                    missing.append(case["case_id"])
                elif not any(compatible(row, case) for row in same):
                    incompatible.append(case["case_id"])
            readiness = [suite_readiness(suite, rows) for suite in con.execute(select(suites).where(
                suites.c.project_id == project_id).order_by(suites.c.created_at).limit(100)).mappings()]
            return {"project_id": project_id, "provenance": "customer_generated", "notice": NOTICE,
                "report_counts": counts(row["verdict"] for row in rows), "latest_cases": list(latest.values()),
                "coverage": {"catalog_cases": len(catalog()), "reported_cases": len(catalog()) - len(missing) - len(incompatible),
                    "missing_case_ids": missing, "incompatible_case_ids": incompatible}, "suites": readiness}

    @app.get("/api/team/projects/{project_id}/invitations")
    def list_invitations(project_id: str, request: Request):
        with store.engine.connect() as con:
            _, _, who = authorize(request, con, project_id, minimum="owner", scope="owner:manage")
            human(who)
            rows = con.execute(select(invitations).where(invitations.c.project_id == project_id).order_by(invitations.c.created_at.desc()).limit(100)).mappings()
            return {"items": [invitation_view(row) for row in rows]}

    @app.post("/api/team/projects/{project_id}/invitations", status_code=201)
    def create_invitation(project_id: str, body: InvitationInput, request: Request):
        with store.engine.begin() as con:
            _, _, who = authorize(request, con, project_id, minimum="owner", scope="owner:manage", lock=True)
            human(who)
            # A copied invitation reserves no paid seat. Check capacity now and
            # again under the project lock when the matching person accepts.
            billing.enforce(con, project_id, added_seats=1)
            now = utcnow()
            active = con.execute(select(func.count()).select_from(invitations).where(invitations.c.project_id == project_id,
                invitations.c.revoked_at.is_(None), invitations.c.accepted_at.is_(None), invitations.c.expires_at > now)).scalar_one()
            if active >= 100:
                raise HTTPException(409, "Project has 100 pending invitations; revoke unused links")
            # Reissuing for the same address invalidates its earlier links.
            con.execute(update(invitations).where(invitations.c.project_id == project_id, invitations.c.email == body.email,
                invitations.c.accepted_at.is_(None), invitations.c.revoked_at.is_(None)).values(revoked_at=now))
            secret = secrets.token_urlsafe(32)
            row = {"id": opaque_id(), "project_id": project_id, "invited_by": who["user"]["id"], "email": body.email,
                "role": body.role, "token_hash": token_hash(secret), "created_at": now,
                "expires_at": now + timedelta(days=body.expires_days), "revoked_at": None, "accepted_at": None}
            con.execute(insert(invitations).values(**row))
            return invitation_view(row) | {"invite_url": settings.public_origin.rstrip("/") + "/invite#token=" + secret}

    @app.delete("/api/team/projects/{project_id}/invitations/{invitation_id}")
    def revoke_invitation(project_id: str, invitation_id: str, request: Request):
        with store.engine.begin() as con:
            _, _, who = authorize(request, con, project_id, minimum="owner", scope="owner:manage", lock=True)
            human(who)
            result = con.execute(update(invitations).where(invitations.c.id == invitation_id,
                invitations.c.project_id == project_id).values(revoked_at=utcnow()))
            if not result.rowcount:
                raise HTTPException(404, "Invitation not found")
            return {"revoked": True, "id": invitation_id}

    @app.post("/api/team/invitations/accept")
    def accept_invitation(body: InvitationAccept, request: Request):
        from .password_db import user_email_verified
        with store.engine.begin() as con:
            who = human(identity(request, con))
            # Read its project id first, then acquire locks in the same order as
            # create/revoke/member changes: project, invitation, membership.
            row = con.execute(select(invitations).where(invitations.c.token_hash == token_hash(body.token))).mappings().first()
            if not row:
                raise HTTPException(404, "Invitation is unavailable")
            project = con.execute(select(projects).where(projects.c.id == row["project_id"]).with_for_update()).mappings().first()
            row = con.execute(select(invitations).where(invitations.c.id == row["id"]).with_for_update()).mappings().first()
            now = utcnow()
            if not project or not row or row["revoked_at"] or row["accepted_at"] or row["expires_at"] <= now:
                raise HTTPException(410, "Invitation expired, was revoked, or has already been accepted")
            inviter = con.execute(select(memberships.c.role).where(memberships.c.project_id == project["id"],
                memberships.c.user_id == row["invited_by"])).scalar_one_or_none()
            if inviter != "owner":
                raise HTTPException(410, "The inviter no longer has permission to add members")
            user = who["user"]
            local_identity = user["issuer"] == "breakroom:development" and settings.dev_login and settings.environment in {"development", "test"}
            if not local_identity and not user_email_verified(con, user):
                raise HTTPException(403, "Verify your email before accepting an invitation")
            if user["email"].casefold() != row["email"]:
                raise HTTPException(403, "Sign in with the email address this invitation was created for")
            member = con.execute(select(memberships).where(memberships.c.project_id == project["id"], memberships.c.user_id == user["id"])).mappings().first()
            if not member:
                billing.enforce(con, project["id"], added_seats=1)
                con.execute(insert(memberships).values(project_id=project["id"], user_id=user["id"], role=row["role"]))
            con.execute(update(invitations).where(invitations.c.id == row["id"]).values(accepted_at=now))
            return {"project_id": project["id"], "role": member["role"] if member else row["role"], "already_member": bool(member)}
