"""Pydantic schemas for the jobs endpoints — S2-002 / B-033 / self-serve.

JobListItem        — id, title, description, level, language, is_active,
                     company_name, department, interview_type
JobDetail          — extends JobListItem + nos_codes, competencies, created_at, updated_at
JobsListResponse   — paginated wrapper: items, total, page, per_page
JobCreate          — request body for POST /jobs (admin)
JobCreateCustom    — request body for POST /jobs (self-serve, any authenticated user)
JobCreateResponse  — response body for POST /jobs (self-serve): id + title
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobListItem(BaseModel):
    """Minimal job representation returned in list results."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str
    level: str = Field(description="'entry' | 'mid' | 'senior'")
    language: str = Field(description="BCP-47 code, e.g. 'en', 'hi', 'te'")
    is_active: bool
    # B-033 — interview context fields
    company_name: str | None = Field(default=None, description="Hiring company name")
    department: str | None = Field(default=None, description="Hiring department")
    interview_type: str = Field(
        default="screening",
        description="'screening' | 'technical' | 'hr'",
    )


class JobDetail(JobListItem):
    """Full job representation returned for single-job lookup."""

    nos_codes: list[str] = Field(
        description="NSQF NOS codes linked to this job, e.g. ['SSC/N9001']"
    )
    competencies: dict[str, Any] = Field(
        description="JSONB blob with required/nice-to-have skill lists"
    )
    created_at: datetime
    updated_at: datetime


class JobsListResponse(BaseModel):
    """Paginated list of jobs."""

    items: list[JobListItem]
    total: int = Field(ge=0, description="Total matching rows (ignoring pagination)")
    page: int = Field(ge=1)
    per_page: int = Field(ge=1)


class JobCreate(BaseModel):
    """Request body for POST /jobs (admin-only job creation).

    B-033 adds company_name, department, and interview_type to this payload.
    """

    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    level: str = Field(description="'entry' | 'mid' | 'senior'")
    language: str = Field(default="en", description="BCP-47 code, e.g. 'en', 'hi', 'te'")
    nos_codes: list[str] = Field(default_factory=list)
    competencies: dict[str, Any] = Field(default_factory=dict)
    # B-033 — interview context fields
    company_name: str | None = Field(default=None, description="Hiring company name")
    department: str | None = Field(default=None, description="Hiring department")
    interview_type: str = Field(
        default="screening",
        description="'screening' | 'technical' | 'hr'",
    )


class JobCreateCustom(BaseModel):
    """Request body for self-serve POST /jobs (any authenticated user).

    The authenticated user becomes the owner (created_by_user_id).
    description is optional — defaults to title when omitted so the NOT NULL
    DB column is satisfied without burdening the user.
    jd_text is optional — the user may paste the JD text directly here instead
    of (or before) uploading a PDF via POST /jobs/{id}/jd-document.
    """

    title: str = Field(min_length=1, description="Job title, e.g. 'Backend Engineer'")
    company_name: str = Field(default="", description="Hiring company name")
    department: str = Field(default="", description="Department or team name")
    jd_text: str = Field(default="", description="Plain-text job description (optional)")
    level: str = Field(
        default="entry",
        description="Experience tier: 'entry' | 'mid' | 'senior'",
    )
    interview_type: str = Field(
        default="screening",
        description="Interview style: 'screening' | 'technical' | 'hr'",
    )
    description: str = Field(
        default="",
        description=(
            "Human-readable description shown in the UI. "
            "Defaults to the job title when left blank."
        ),
    )


class JobCreateResponse(BaseModel):
    """Response body for a successful self-serve POST /jobs (HTTP 201)."""

    id: uuid.UUID
    title: str
