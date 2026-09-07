"""What needs a human right now — the Roll-up's attention panel.

    GET /hr/attention

The detection rules already existed and were already good: stalled candidates,
a funnel losing almost everyone, an exam question nobody passes, an opening
with candidates and no workflow. What they lacked was a way to *ask*. They ran
nightly and wrote notifications, and a notification is a push channel — you see
it once, when it fires, if you were looking.

COMPUTED LIVE, NOT READ BACK FROM NOTIFICATIONS
-----------------------------------------------
The obvious implementation is to list this company's unread watcher
notifications. It is also wrong, and wrong in a way that would look fine in
testing.

Nightly delivery is deduplicated on purpose — ``dedupe_key`` keeps "7
applicants stalled" from notifying the same person every night until they act,
because that is how a notification bell becomes wallpaper. But suppression is a
statement about *telling* someone, not about whether the problem is still
there. A panel built on delivered notifications would go quiet a day after the
first alert and stay quiet while the condition persisted, which is the opposite
of what a panel is for.

So this runs the same pure rules against fresh data every time it is asked.
Push and pull, one rule set: the bell tells you when something starts, the
panel tells you what is true now.

DELIBERATELY NOT GATED ON ``WATCHERS_ENABLED``
----------------------------------------------
That flag means "do not run the nightly sweep" — it exists so a deployment can
switch off unsolicited notifications. Someone opening this panel is asking
directly, and refusing to answer because background delivery is off would be a
setting doing something nobody meant by it.

WHAT AN HR MANAGER DOES NOT SEE
-------------------------------
The DPDP erasure watcher. Its input is gathered separately and platform-wide,
and its findings go to platform owners once rather than to every company's HR
team — a statutory deadline is the Intants core's obligation, not a hiring
task. That scoping comes free here: ``gather_company_input`` does not populate
``erasure_requests``, so the rule finds nothing to report.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, Field
from shared.agents import run_watchers

from app.agents.watch_runner import gather_company_input
from app.database import DbSessionDep
from app.dependencies import HrCtxDep
from app.rate_limit import rate_limit

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/hr", tags=["hr-attention"])


class AttentionCitation(BaseModel):
    """The record a finding is about, so the panel can link straight to it."""

    kind: str
    id: str
    label: str
    href: str | None = None


class AttentionItem(BaseModel):
    """One thing that needs a person.

    ``watcher`` and ``dedupe_key`` are carried for the client's benefit: the
    key is a stable identity for a finding across refreshes, which is what a
    list key wants, and the watcher name lets the panel group or mute by rule
    later without another API change.
    """

    watcher: str
    severity: str  # critical | warning | info — already sorted, worst first
    title: str
    body: str
    link: str | None = None
    dedupe_key: str = ""
    citations: list[AttentionCitation] = Field(default_factory=list)


class AttentionOut(BaseModel):
    generated_at: str
    total: int
    items: list[AttentionItem] = Field(default_factory=list)


@router.get(
    "/attention",
    response_model=AttentionOut,
    summary="What needs a human in this company's hiring right now",
    # Each call is a handful of bounded aggregate queries. Capped so a dashboard
    # left open on a polling interval cannot turn into a load generator.
    dependencies=[rate_limit("hr_attention", 30)],
)
async def get_attention(ctx: HrCtxDep, db: DbSessionDep) -> AttentionOut:
    """Run the watcher rules against this company's current data.

    ``run_watchers`` never raises: a rule that throws is skipped with a log
    line rather than taking the panel down, so one broken detection cannot cost
    a recruiter every other alert. That is the library's guarantee, relied on
    here rather than re-implemented.
    """
    _hr_uid, company_id = ctx
    data = await gather_company_input(db, str(company_id))
    findings = run_watchers(data)

    log.info(
        "hr.attention.read",
        company_id=str(company_id),
        findings=len(findings),
        # Counts only — a finding's title can name a candidate.
        critical=sum(1 for f in findings if f.severity == "critical"),
    )

    return AttentionOut(
        generated_at=datetime.now(tz=UTC).isoformat(),
        total=len(findings),
        items=[
            AttentionItem(
                watcher=f.watcher,
                severity=f.severity,
                title=f.title,
                body=f.body,
                link=f.link,
                dedupe_key=f.dedupe_key,
                citations=[
                    AttentionCitation(
                        kind=c.kind, id=c.id, label=c.label, href=getattr(c, "href", None)
                    )
                    for c in f.citations
                ],
            )
            for f in findings
        ],
    )
