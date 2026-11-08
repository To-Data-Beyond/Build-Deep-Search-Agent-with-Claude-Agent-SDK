from datetime import date
from typing import Literal

from pydantic import Field, HttpUrl

from collector import EvidenceInput
from contracts import Contract, EvidenceRef

Timing = Literal['current_as_of_reference', 'historical_as_of_reference', 'unknown']


class SavedEvidence(EvidenceInput):
    id: str = Field(pattern=r'^E[1-9][0-9]*$')
    sub_question_id: str
    source_id: str
    source_snapshot: str
    resolved_source_url: HttpUrl | None
    retrieval_date: date
    validation_status: Literal['pending']


class Role(Contract):
    title: str = Field(min_length=1)
    organization: str = Field(min_length=1)


class Assessment(Contract):
    evidence_id: str
    identity: Literal['matched', 'mismatch', 'unclear']
    authority: Literal['primary', 'secondary', 'unclear']
    role_support: Literal['supported', 'contradicted', 'insufficient']
    role: Role | None
    role_excerpt: str | None
    timing: Timing
    timing_excerpt: str | None
    reason: str = Field(min_length=1)


class ReviewBatch(Contract):
    assessments: list[Assessment] = Field(min_length=1)


class Decision(Contract):
    evidence: EvidenceRef
    source_snapshot: str
    verdict: Literal['accepted', 'rejected', 'needs_review']
    reasons: list[str]
    assessment: Assessment | None = None
    accepted_role: Role | None = None
    timing: Timing = 'unknown'
