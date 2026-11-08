from typing import Literal

from pydantic import Field

from contracts import Assignment, Contract


class ResearchPlan(Contract):
    rationale: str = Field(min_length=1)
    assignments: list[Assignment] = Field(min_length=1)


class PlanReview(Contract):
    verdict: Literal['approve', 'revise']
    issues: list[str]
    advisories: list[str] = Field(default_factory=list)
