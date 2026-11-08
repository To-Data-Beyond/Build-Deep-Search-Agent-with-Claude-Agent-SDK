from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class ResearchLimits(Contract):
    max_rounds: int = Field(default=2, ge=1, le=5)
    max_assignments: int = Field(default=4, ge=1, le=20)
    max_elapsed_seconds: int = Field(default=300, ge=1)
    max_estimated_cost_usd: float = Field(default=3.0, gt=0, allow_inf_nan=False)
    max_sources_per_assignment: int = Field(default=3, ge=1, le=10)
    max_records_per_assignment: int = Field(default=6, ge=1, le=30)


class ResearchRequest(Contract):
    subject_name: str = Field(min_length=1)
    identity_anchor_urls: list[HttpUrl] = Field(min_length=1)
    scope: str = Field(min_length=1)
    reference_date: date
    limits: ResearchLimits = Field(default_factory=ResearchLimits)


class Assignment(Contract):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]*$')
    question: str = Field(min_length=1)
    evidence_requirements: list[str] = Field(min_length=1)
    preferred_source_types: list[str] = Field(min_length=1)


class CollectionRef(Contract):
    collection_id: str = Field(min_length=1)
    assignment_id: str = Field(min_length=1)
    round_index: int = Field(ge=1)
    artifact_directory: str = Field(min_length=1)
    status: Literal['collected', 'no_evidence', 'incomplete']
    evidence_ids: list[str]


class EvidenceRef(Contract):
    collection_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)


class ResearchState(Contract):
    schema_version: Literal['1'] = '1'
    run_id: str = Field(min_length=1)
    request: ResearchRequest
    phase: Literal['created', 'planning', 'collecting', 'validating',
                   'merging', 'assessing', 'stopped'] = 'created'
    round_index: int = Field(default=0, ge=0)
    assignments_started: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    cost_accounting_complete: bool = True
    plan: list[Assignment] = Field(default_factory=list)
    collections: list[CollectionRef] = Field(default_factory=list)
    validation_artifacts: list[str] = Field(default_factory=list)
    merged_profile_artifact: str | None = None
    gap_assessment_artifact: str | None = None
    stop_reason: Literal['requirements_satisfied', 'no_progress',
                         'execution_limit', 'failure'] | None = None
