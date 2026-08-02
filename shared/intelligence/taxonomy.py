"""Domain taxonomy — the deterministic backbone of the role engine.

This is what makes the platform genuinely role-agnostic rather than
role-agnostic-by-instruction. The previous approach told the LLM "always follow
the role, never assume software" in prose; that helps, but it is unverifiable
and gives the scorer nothing to calibrate against. Here a role is classified
into a domain family by explicit keyword evidence, and each family carries a
baseline competency set built from archetypes.

The taxonomy has two jobs:

1. **Fallback.** When the LLM is unavailable, out of credits, or returns
   garbage, ``baseline_profile()`` still produces a usable, role-appropriate
   rubric. A welder interview must not degrade into data-structures questions
   just because Gemini 503'd — which is exactly what the old prose-only
   approach did.

2. **Seed.** On the LLM path the baseline is handed to the model as a starting
   point (see ``prompt.py``), so it refines a sane structure instead of
   inventing one from a job title. That measurably reduces the "five vague
   competencies that are all really 'communication'" failure mode.

Coverage is deliberately broad — engineering colleges, skill universities and
state SDCs interview for trades, healthcare, retail and agriculture far more
than for software. ``generic`` catches anything unmatched and is a perfectly
serviceable interview, not a failure state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

from shared.intelligence.archetypes import get_archetype
from shared.intelligence.schema import Competency, RoleProfile, Seniority

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CompetencySpec:
    """A family's use of one archetype: weight plus optional specialisation."""

    archetype_id: str
    weight: float
    name: str | None = None
    probes: tuple[str, ...] | None = None

    def build(self) -> Competency:
        return get_archetype(self.archetype_id).to_competency(
            weight=self.weight, name=self.name, probes=self.probes
        )


@dataclass(frozen=True)
class DomainFamily:
    """One occupational family and the competencies that define it."""

    id: str
    label: str
    # Matched case-insensitively on word boundaries against title / skills /
    # department / JD. Multi-word phrases are matched as phrases.
    keywords: tuple[str, ...]
    specs: tuple[CompetencySpec, ...]
    # Fills the profile summary when no JD is available, so the interviewer
    # prompt always has a concrete picture of the work.
    summary: str = ""

    def competencies(self) -> list[Competency]:
        return [spec.build() for spec in self.specs]


# ---------------------------------------------------------------------------
# Families
#
# Weights are relative importance, normalised by RoleProfile — they are written
# here as readable numbers (they happen to sum to ~1.0) rather than exact
# fractions.
# ---------------------------------------------------------------------------

_FAMILIES: tuple[DomainFamily, ...] = (
    DomainFamily(
        id="software_it",
        label="Software & IT",
        keywords=(
            "software", "developer", "engineer sde", "sde", "programmer", "backend",
            "frontend", "full stack", "fullstack", "web developer", "mobile developer",
            "android", "ios", "devops", "sre", "site reliability", "qa engineer",
            "test engineer", "automation tester", "python", "java", "javascript",
            "typescript", "react", "node", "django", "spring boot", "kubernetes",
            "cloud engineer", "api", "microservices", "database administrator",
        ),
        summary=(
            "Builds, tests and maintains software systems — writing code, "
            "reviewing others' code, debugging production issues and shipping "
            "changes safely."
        ),
        specs=(
            CompetencySpec("domain_fundamentals", 0.24, name="Engineering Fundamentals",
                           probes=(
                               "Ask them to explain a core concept from their stack in their own words.",
                               "Ask how they would structure a small system for a described requirement.",
                               "Ask about a trade-off they made between two technical approaches.",
                           )),
            CompetencySpec("problem_solving", 0.22, name="Debugging & Problem Solving",
                           probes=(
                               "Ask how they tracked down a bug that was not reproducible locally.",
                               "Give a described failure and ask what they would check first, and why.",
                               "Ask what they do when the logs show nothing useful.",
                           )),
            CompetencySpec("hands_on_execution", 0.20, name="Delivery & Code Ownership"),
            CompetencySpec("quality_and_accuracy", 0.14, name="Testing & Code Quality"),
            CompetencySpec("collaboration", 0.12),
            CompetencySpec("communication_clarity", 0.08),
        ),
    ),
    DomainFamily(
        id="data_analytics",
        label="Data & Analytics",
        keywords=(
            "data analyst", "data scientist", "data engineer", "analytics", "bi",
            "business intelligence", "machine learning", "ml engineer", "ai engineer",
            "statistician", "power bi", "tableau", "sql analyst", "big data",
            "data warehouse", "etl", "mis executive",
        ),
        summary=(
            "Turns raw data into decisions — sourcing and cleaning data, "
            "analysing it, and communicating what it means to people who will "
            "act on it."
        ),
        specs=(
            CompetencySpec("data_reasoning", 0.28, name="Analytical Reasoning"),
            CompetencySpec("domain_fundamentals", 0.22, name="Statistical & Modelling Fundamentals"),
            CompetencySpec("tools_and_systems", 0.18, name="Data Tooling"),
            CompetencySpec("communication_clarity", 0.16, name="Communicating Findings"),
            CompetencySpec("quality_and_accuracy", 0.10, name="Data Quality Rigour"),
            CompetencySpec("collaboration", 0.06),
        ),
    ),
    DomainFamily(
        id="electronics_electrical",
        label="Electronics & Electrical",
        keywords=(
            "electrical", "electronics", "electrician", "wireman", "instrumentation",
            "plc", "scada", "embedded", "vlsi", "pcb", "circuit", "power systems",
            "substation", "transformer", "switchgear", "lineman", "control panel",
            "motor rewinding", "solar technician",
        ),
        summary=(
            "Installs, tests and maintains electrical or electronic systems to "
            "standard, diagnosing faults and working safely with live equipment."
        ),
        specs=(
            CompetencySpec("domain_fundamentals", 0.24, name="Electrical Fundamentals",
                           probes=(
                               "Ask them to explain a core electrical principle behind work they do.",
                               "Ask how they size or select a component for a described load.",
                               "Ask why a standard in their work exists, not just what it says.",
                           )),
            CompetencySpec("safety_and_compliance", 0.22, name="Electrical Safety & Standards"),
            CompetencySpec("hands_on_execution", 0.22, name="Installation & Maintenance"),
            CompetencySpec("problem_solving", 0.18, name="Fault Diagnosis"),
            CompetencySpec("collaboration", 0.08),
            CompetencySpec("communication_clarity", 0.06),
        ),
    ),
    DomainFamily(
        id="mechanical_manufacturing",
        label="Mechanical & Manufacturing",
        keywords=(
            "mechanical", "manufacturing", "production engineer", "cnc", "vmc",
            "lathe", "machinist", "fitter", "turner", "welder", "welding",
            "tool and die", "cad", "solidworks", "autocad mechanical", "catia",
            "maintenance engineer", "quality inspector", "shop floor", "assembly line",
            "hvac", "automobile", "diesel mechanic", "millwright",
        ),
        summary=(
            "Makes, assembles or maintains physical products and machinery to "
            "specification, working to drawings, tolerances and safety rules."
        ),
        specs=(
            CompetencySpec("hands_on_execution", 0.26, name="Machining & Assembly Skill"),
            CompetencySpec("domain_fundamentals", 0.22, name="Mechanical Fundamentals",
                           probes=(
                               "Ask them to read out what a drawing or specification is telling them.",
                               "Ask how they decide on a tolerance, fit or material for a job.",
                               "Ask why a process step in their work exists.",
                           )),
            CompetencySpec("quality_and_accuracy", 0.20, name="Measurement & Quality Control"),
            CompetencySpec("safety_and_compliance", 0.16, name="Shop-floor Safety"),
            CompetencySpec("problem_solving", 0.10, name="Breakdown Diagnosis"),
            CompetencySpec("collaboration", 0.06),
        ),
    ),
    DomainFamily(
        id="civil_construction",
        label="Civil & Construction",
        keywords=(
            "civil", "construction", "site engineer", "structural", "surveyor",
            "quantity surveyor", "estimator", "draughtsman", "draftsman",
            "project engineer construction", "rcc", "concrete", "masonry", "mason",
            "plumber", "bar bender", "shuttering", "highway", "irrigation",
            "town planning", "architect",
        ),
        summary=(
            "Plans, supervises or executes construction work — reading drawings, "
            "controlling quality and quantity on site, and keeping work safe and "
            "on schedule."
        ),
        specs=(
            CompetencySpec("domain_fundamentals", 0.24, name="Civil & Structural Fundamentals",
                           probes=(
                               "Ask them to explain a specification or code requirement they work to.",
                               "Ask how they check that materials arriving on site are acceptable.",
                               "Ask why a construction sequence has to run in a particular order.",
                           )),
            CompetencySpec("hands_on_execution", 0.22, name="Site Execution"),
            CompetencySpec("quality_and_accuracy", 0.18, name="Quantity & Quality Control"),
            CompetencySpec("safety_and_compliance", 0.16, name="Site Safety"),
            CompetencySpec("planning_and_coordination", 0.14),
            CompetencySpec("communication_clarity", 0.06),
        ),
    ),
    DomainFamily(
        id="healthcare_nursing",
        label="Healthcare & Nursing",
        keywords=(
            "nurse", "nursing", "gnm", "anm", "staff nurse", "paramedic",
            "lab technician", "pharmacist", "physiotherapist", "radiographer",
            "ot technician", "dialysis", "phlebotomist", "healthcare", "clinical",
            "ward", "patient care", "medical assistant", "asha", "hospital",
        ),
        summary=(
            "Delivers clinical or patient-facing care — following protocol, "
            "observing and escalating changes in a patient's condition, and "
            "maintaining hygiene and documentation."
        ),
        specs=(
            CompetencySpec("domain_fundamentals", 0.24, name="Clinical Knowledge",
                           probes=(
                               "Ask them to explain a clinical procedure they perform and why each step matters.",
                               "Ask what observations would worry them about a patient and why.",
                               "Ask how they decide when something must be escalated to a doctor.",
                           )),
            CompetencySpec("safety_and_compliance", 0.22, name="Patient Safety & Protocol"),
            CompetencySpec("people_handling", 0.20, name="Patient & Family Handling"),
            CompetencySpec("hands_on_execution", 0.16, name="Procedural Skill"),
            CompetencySpec("quality_and_accuracy", 0.12, name="Documentation Accuracy"),
            CompetencySpec("collaboration", 0.06),
        ),
    ),
    DomainFamily(
        id="sales_bd",
        label="Sales & Business Development",
        keywords=(
            "sales", "business development", "bd executive", "account manager",
            "relationship manager", "field sales", "inside sales", "telesales",
            "presales", "key account", "channel sales", "territory", "sales officer",
            "marketing executive", "growth", "lead generation",
        ),
        summary=(
            "Finds, qualifies and closes business — building relationships, "
            "handling objections and working to a number."
        ),
        specs=(
            CompetencySpec("commercial_acumen", 0.26, name="Sales Acumen"),
            CompetencySpec("customer_interaction", 0.24, name="Client Handling & Objections"),
            CompetencySpec("communication_clarity", 0.18, name="Persuasive Communication"),
            CompetencySpec("planning_and_coordination", 0.14, name="Pipeline Management"),
            CompetencySpec("ownership_and_learning", 0.12, name="Resilience & Drive"),
            CompetencySpec("domain_fundamentals", 0.06, name="Product & Market Knowledge"),
        ),
    ),
    DomainFamily(
        id="customer_support_bpo",
        label="Customer Support & BPO",
        keywords=(
            "customer support", "customer service", "call center", "call centre",
            "bpo", "kpo", "voice process", "non voice", "chat support",
            "technical support", "help desk", "helpdesk", "service desk",
            "backend process", "csr", "customer care",
        ),
        summary=(
            "Resolves customer issues over voice or chat — understanding the "
            "problem quickly, following process, and keeping the customer calm "
            "and informed."
        ),
        specs=(
            CompetencySpec("customer_interaction", 0.28, name="Customer Handling"),
            CompetencySpec("communication_clarity", 0.24, name="Spoken Clarity & Listening"),
            CompetencySpec("problem_solving", 0.18, name="Issue Resolution"),
            CompetencySpec("tools_and_systems", 0.12, name="CRM & Process Tools"),
            CompetencySpec("quality_and_accuracy", 0.10, name="Process Adherence"),
            CompetencySpec("ownership_and_learning", 0.08),
        ),
    ),
    DomainFamily(
        id="finance_accounting",
        label="Finance & Accounting",
        keywords=(
            "accountant", "accounts", "finance", "audit", "auditor", "taxation",
            "gst", "tally", "bookkeeping", "accounts payable", "accounts receivable",
            "financial analyst", "ca ", "cma", "payroll", "treasury", "billing",
            "credit analyst", "banking", "loan officer",
        ),
        summary=(
            "Keeps financial records accurate and compliant — recording "
            "transactions, reconciling, reporting and meeting statutory "
            "deadlines."
        ),
        specs=(
            CompetencySpec("domain_fundamentals", 0.26, name="Accounting & Regulatory Knowledge"),
            CompetencySpec("quality_and_accuracy", 0.24, name="Accuracy & Reconciliation"),
            CompetencySpec("tools_and_systems", 0.16, name="Accounting Systems"),
            CompetencySpec("safety_and_compliance", 0.14, name="Statutory Compliance"),
            CompetencySpec("data_reasoning", 0.12, name="Financial Analysis"),
            CompetencySpec("communication_clarity", 0.08),
        ),
    ),
    DomainFamily(
        id="hr_admin",
        label="HR & Administration",
        keywords=(
            "human resource", "hr executive", "hr manager", "recruiter",
            "talent acquisition", "hr generalist", "payroll executive",
            "admin executive", "office administrator", "front office",
            "receptionist", "personnel", "employee relations",
        ),
        summary=(
            "Runs people and office processes — hiring, onboarding, records, "
            "employee queries and day-to-day coordination."
        ),
        specs=(
            CompetencySpec("people_handling", 0.26, name="People & Stakeholder Handling"),
            CompetencySpec("planning_and_coordination", 0.20, name="Process Coordination"),
            CompetencySpec("domain_fundamentals", 0.18, name="HR & Labour Knowledge"),
            CompetencySpec("communication_clarity", 0.16),
            CompetencySpec("quality_and_accuracy", 0.12, name="Records & Confidentiality"),
            CompetencySpec("ownership_and_learning", 0.08),
        ),
    ),
    DomainFamily(
        id="education_training",
        label="Education & Training",
        keywords=(
            "teacher", "lecturer", "professor", "trainer", "faculty", "tutor",
            "instructor", "academic", "counsellor", "counselor", "principal",
            "curriculum", "tgt", "pgt", "assistant professor", "training officer",
        ),
        summary=(
            "Teaches or trains learners — planning what to cover, explaining it "
            "so it lands, and checking whether it actually did."
        ),
        specs=(
            CompetencySpec("communication_clarity", 0.26, name="Explanation & Delivery"),
            CompetencySpec("domain_fundamentals", 0.24, name="Subject Mastery"),
            CompetencySpec("people_handling", 0.18, name="Learner Engagement"),
            CompetencySpec("planning_and_coordination", 0.14, name="Lesson & Curriculum Planning"),
            CompetencySpec("data_reasoning", 0.10, name="Assessing Learning"),
            CompetencySpec("ownership_and_learning", 0.08),
        ),
    ),
    DomainFamily(
        id="logistics_supply_chain",
        label="Logistics & Supply Chain",
        keywords=(
            "logistics", "supply chain", "warehouse", "inventory", "procurement",
            "purchase executive", "store keeper", "storekeeper", "dispatch",
            "transport", "fleet", "shipping", "import export", "material planning",
            "vendor management", "last mile",
        ),
        summary=(
            "Moves and accounts for goods — planning stock and transport, "
            "coordinating vendors and drivers, and keeping records that match "
            "reality."
        ),
        specs=(
            CompetencySpec("planning_and_coordination", 0.26, name="Planning & Coordination"),
            CompetencySpec("quality_and_accuracy", 0.20, name="Stock & Record Accuracy"),
            CompetencySpec("domain_fundamentals", 0.18, name="Supply Chain Knowledge"),
            CompetencySpec("problem_solving", 0.16, name="Handling Disruptions"),
            CompetencySpec("tools_and_systems", 0.12, name="ERP & Tracking Systems"),
            CompetencySpec("communication_clarity", 0.08),
        ),
    ),
    DomainFamily(
        id="retail_hospitality",
        label="Retail & Hospitality",
        keywords=(
            "retail", "store manager", "sales associate", "cashier", "merchandiser",
            "hospitality", "hotel", "front desk", "housekeeping", "chef", "cook",
            "steward", "waiter", "food and beverage", "f&b", "restaurant",
            "guest relations", "travel", "tourism",
        ),
        summary=(
            "Serves customers or guests in person — delivering the experience "
            "consistently, handling rushes and complaints, and keeping standards "
            "up."
        ),
        specs=(
            CompetencySpec("customer_interaction", 0.28, name="Guest & Customer Service"),
            CompetencySpec("hands_on_execution", 0.20, name="Service Execution"),
            CompetencySpec("quality_and_accuracy", 0.16, name="Standards & Presentation"),
            CompetencySpec("safety_and_compliance", 0.14, name="Hygiene & Safety"),
            CompetencySpec("collaboration", 0.12),
            CompetencySpec("communication_clarity", 0.10),
        ),
    ),
    DomainFamily(
        id="agriculture_allied",
        label="Agriculture & Allied",
        keywords=(
            "agriculture", "agri", "agronomy", "horticulture", "farm", "dairy",
            "poultry", "veterinary", "fisheries", "soil", "irrigation technician",
            "seed", "fertilizer", "agri extension", "food processing",
        ),
        summary=(
            "Works in crop, livestock or food production — applying agronomic "
            "practice, managing inputs and seasons, and advising or supervising "
            "on the ground."
        ),
        specs=(
            CompetencySpec("domain_fundamentals", 0.26, name="Agronomic & Technical Knowledge"),
            CompetencySpec("hands_on_execution", 0.22, name="Field Practice"),
            CompetencySpec("problem_solving", 0.18, name="Diagnosing Field Problems"),
            CompetencySpec("planning_and_coordination", 0.14, name="Season & Input Planning"),
            CompetencySpec("people_handling", 0.12, name="Working with Farmers"),
            CompetencySpec("safety_and_compliance", 0.08, name="Input & Residue Safety"),
        ),
    ),
    DomainFamily(
        id="design_creative",
        label="Design & Creative",
        keywords=(
            "designer", "graphic design", "ui ux", "ui/ux", "product design",
            "visual design", "motion graphics", "video editor", "animator",
            "content writer", "copywriter", "creative", "illustrator", "fashion design",
            "interior design",
        ),
        summary=(
            "Creates work for an audience and a brief — interpreting the "
            "requirement, iterating on feedback, and defending or changing "
            "choices for good reasons."
        ),
        specs=(
            CompetencySpec("domain_fundamentals", 0.24, name="Craft Fundamentals"),
            CompetencySpec("hands_on_execution", 0.22, name="Portfolio & Execution"),
            CompetencySpec("communication_clarity", 0.20, name="Presenting & Defending Work"),
            CompetencySpec("tools_and_systems", 0.14, name="Design Tooling"),
            CompetencySpec("customer_interaction", 0.12, name="Working to a Brief"),
            CompetencySpec("ownership_and_learning", 0.08),
        ),
    ),
    DomainFamily(
        id="legal_compliance",
        label="Legal & Compliance",
        keywords=(
            "legal", "lawyer", "advocate", "paralegal", "compliance officer",
            "company secretary", "contract", "litigation", "regulatory affairs",
            "risk officer", "kyc", "aml",
        ),
        summary=(
            "Applies law or regulation to real situations — reading the "
            "requirement precisely, assessing risk and advising on what can and "
            "cannot be done."
        ),
        specs=(
            CompetencySpec("domain_fundamentals", 0.30, name="Legal & Regulatory Knowledge"),
            CompetencySpec("quality_and_accuracy", 0.20, name="Precision & Drafting"),
            CompetencySpec("problem_solving", 0.18, name="Risk Assessment"),
            CompetencySpec("communication_clarity", 0.18, name="Advising Non-lawyers"),
            CompetencySpec("safety_and_compliance", 0.08, name="Ethics & Confidentiality"),
            CompetencySpec("collaboration", 0.06),
        ),
    ),
    DomainFamily(
        id="skilled_trades",
        label="Skilled Trades",
        keywords=(
            "technician", "apprentice", "iti", "trade", "operator", "helper",
            "carpenter", "painter", "sheet metal", "refrigeration", "ac technician",
            "mobile repair", "computer hardware", "networking technician",
            "beautician", "tailor", "driver",
        ),
        summary=(
            "Performs a skilled manual trade — working to standard with the "
            "right tools, diagnosing faults, and finishing the job safely."
        ),
        specs=(
            CompetencySpec("hands_on_execution", 0.30, name="Trade Skill"),
            CompetencySpec("domain_fundamentals", 0.20, name="Trade Knowledge"),
            CompetencySpec("problem_solving", 0.18, name="Fault Finding"),
            CompetencySpec("safety_and_compliance", 0.16, name="Safe Working Practice"),
            CompetencySpec("quality_and_accuracy", 0.10, name="Finish Quality"),
            CompetencySpec("customer_interaction", 0.06),
        ),
    ),
    # Terminal fallback. Never remove — classify() must always return a family.
    DomainFamily(
        id="generic",
        label="General Professional",
        keywords=(),
        summary=(
            "Performs the duties of the role competently — applying the "
            "knowledge it requires, executing reliably, and working well with "
            "the people around them."
        ),
        specs=(
            CompetencySpec("domain_fundamentals", 0.26, name="Role Knowledge"),
            CompetencySpec("hands_on_execution", 0.22, name="Practical Execution"),
            CompetencySpec("problem_solving", 0.18),
            CompetencySpec("communication_clarity", 0.14),
            CompetencySpec("collaboration", 0.12),
            CompetencySpec("role_motivation", 0.08),
        ),
    ),
)

FAMILIES: dict[str, DomainFamily] = {f.id: f for f in _FAMILIES}
GENERIC_FAMILY: DomainFamily = FAMILIES["generic"]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

# Evidence weights. The title is the strongest signal and the JD the weakest —
# a JD for a mechanical role routinely name-drops "Excel" and "communication",
# and an unweighted bag-of-words classifier happily files it under IT.
_W_TITLE = 6.0
_W_SKILLS = 3.0
_W_DEPARTMENT = 2.0
_W_JD = 1.0

# A single keyword can only score once per field. Without this, a JD that says
# "sales" fourteen times outweighs a title that says "Mechanical Engineer".
_JD_SCAN_CHARS = 4000

# Below this, the evidence is too thin to claim a family and we use ``generic``
# rather than pretending. Two title hits, or one title hit plus a skill, clears
# it comfortably.
_MIN_CONFIDENT_SCORE = 5.0


def _normalise(text: str) -> str:
    """Lowercase and collapse punctuation to spaces for keyword matching."""
    return re.sub(r"[^a-z0-9+#&/ ]+", " ", text.lower())


def _hits(keyword: str, haystack: str) -> bool:
    """Word-boundary containment test.

    Boundaries matter: without them ``"ca "`` matches "communication" and
    ``"bd"`` matches "bdo". The keyword is regex-escaped because a few contain
    ``+``, ``#`` or ``/`` (``"c++"``, ``"ui/ux"``, ``"f&b"``).
    """
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword.strip())}(?![a-z0-9])", haystack) is not None


def classify(
    *,
    job_title: str,
    jd_text: str = "",
    required_skills: list[str] | None = None,
    department: str = "",
) -> tuple[DomainFamily, float]:
    """Pick the best-matching domain family and return it with its score.

    Returns ``(GENERIC_FAMILY, score)`` when nothing clears
    ``_MIN_CONFIDENT_SCORE`` — an honest "I don't know this role" that still
    yields a working interview, which is much better than confidently filing a
    lab technician under Software & IT.
    """
    title = _normalise(job_title)
    skills = _normalise(" ".join(required_skills or []))
    dept = _normalise(department)
    jd = _normalise(jd_text[:_JD_SCAN_CHARS])

    best: DomainFamily = GENERIC_FAMILY
    best_score = 0.0
    for family in _FAMILIES:
        if not family.keywords:
            continue  # generic has no keywords — it is the fallback, not a match
        score = 0.0
        for kw in family.keywords:
            if _hits(kw, title):
                score += _W_TITLE
            if skills and _hits(kw, skills):
                score += _W_SKILLS
            if dept and _hits(kw, dept):
                score += _W_DEPARTMENT
            if jd and _hits(kw, jd):
                score += _W_JD
        if score > best_score:
            best, best_score = family, score

    if best_score < _MIN_CONFIDENT_SCORE:
        log.info(
            "intelligence.classify.low_confidence",
            job_title=job_title[:80],
            best_family=best.id,
            score=best_score,
            chosen="generic",
        )
        return GENERIC_FAMILY, best_score

    return best, best_score


def baseline_profile(
    *,
    profile_id: str,
    job_title: str,
    jd_text: str = "",
    required_skills: list[str] | None = None,
    department: str = "",
    seniority: Seniority = "mid",
) -> RoleProfile:
    """Build a deterministic ``RoleProfile`` with no LLM call.

    This is both the cold-start fallback and the seed handed to the LLM. It is
    pure and side-effect free, so it is safe to call on the hot path (the live
    interview worker calls it before the room even connects).
    """
    family, score = classify(
        job_title=job_title,
        jd_text=jd_text,
        required_skills=required_skills,
        department=department,
    )
    return RoleProfile(
        profile_id=profile_id,
        job_title=job_title.strip() or "the role",
        domain_family=family.id,
        domain_label=family.label,
        seniority=seniority,
        summary=family.summary,
        competencies=family.competencies(),
        red_flags=[],
        avoid_topics=[],
        source="taxonomy",
    )
