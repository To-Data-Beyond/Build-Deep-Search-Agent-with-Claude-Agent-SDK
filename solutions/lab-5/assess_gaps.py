"""Turn a merged profile into targeted planner input, without a model call."""
import argparse
import json
import math
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

from contracts import EvidenceRef, ResearchRequest, ResearchState


Timing = Literal['unknown', 'current_as_of_reference', 'historical_as_of_reference']


# Read only the fields needed for gap assessment; the full citations stay in the profile.
class Role(BaseModel):
    title: str = Field(min_length=1)
    organization: str = Field(min_length=1)


class Citation(BaseModel):
    evidence: EvidenceRef
    source_url: HttpUrl
    validator_timing: Timing


class Claim(BaseModel):
    role: Role
    timing: Timing
    timing_assessments_disagree: bool
    citations: list[Citation] = Field(min_length=1)


class PendingReview(BaseModel):
    evidence: EvidenceRef
    reasons: list[str]


class Profile(BaseModel):
    subject_name: str
    reference_date: date
    claims: list[Claim]
    needs_review: list[PendingReview]


def gap_id(prefix, *parts):
    payload = json.dumps(parts, ensure_ascii=False).encode()
    return f'{prefix}_{sha256(payload).hexdigest()[:16]}'


def assess_gaps(request: ResearchRequest, profile: Profile):
    if (request.subject_name != profile.subject_name
            or request.reference_date != profile.reference_date):
        raise ValueError('Profile subject or reference date does not match the request.')

    gaps = []
    subject, cutoff = request.subject_name, request.reference_date.isoformat()
    for claim in profile.claims:
        if claim.timing != 'unknown' and not claim.timing_assessments_disagree:
            continue
        role = claim.role
        refs = [c.evidence.model_dump(mode='json') for c in claim.citations]
        has_signal = any(c.validator_timing != 'unknown' for c in claim.citations)
        gaps.append({
            'id': gap_id('timing', *(' '.join(v.split()).casefold()
                                     for v in (role.organization, role.title))),
            'kind': 'timing_disagreement' if claim.timing_assessments_disagree
                    else 'unresolved_timing',
            'priority': 1 if claim.timing_assessments_disagree else 2 if has_signal else 3,
            'role': role.model_dump(),
            'evidence_refs': refs,
            'existing_source_urls': sorted({str(c.source_url) for c in claim.citations}),
            'question': (f'What dated primary evidence establishes whether {subject} held '
                         f'the role of {role.title} at {role.organization} on {cutoff}, '
                         'or establishes a period when the role was held?'),
            'evidence_requirements': [
                'Find a dated primary passage tying the person, organization, and role '
                'to a start date, end date, period, or explicit status on the reference date.',
                'Keep timing unresolved if the dated passage does not establish it. '
                'A retrieval timestamp or undated biography is insufficient.',
            ],
            'preferred_source_types': ['official_company_page', 'university_profile'],
        })

    if not profile.claims:
        gaps.append({
            'id': 'no_accepted_roles', 'kind': 'coverage', 'priority': 1,
            'question': f'What professional roles for {subject} can primary sources establish?',
            'evidence_requirements': ['Find a primary passage naming the person, role, and organization.'],
            'preferred_source_types': ['official_company_page', 'university_profile', 'personal_website'],
        })

    gaps.sort(key=lambda g: (
        g['priority'], g.get('role', {}).get('organization', '').casefold(),
        g.get('role', {}).get('title', '').casefold(), g['id'],
    ))
    return {
        'subject_name': subject,
        'reference_date': cutoff,
        'gaps': gaps,
        'review_queue': [item.model_dump(mode='json') for item in profile.needs_review],
    }


def decide_next(state: ResearchState, assessment, *, elapsed_seconds: float,
                previous_gap_ids: set[str] | None = None):
    """Pure decision. The caller supplies the same run's elapsed time and prior gaps."""
    if state.phase != 'assessing':
        raise ValueError('The controller must finish merging before deciding.')
    if (assessment['subject_name'] != state.request.subject_name
            or assessment['reference_date'] != state.request.reference_date.isoformat()):
        raise ValueError('Gap assessment does not match this run.')
    if not math.isfinite(elapsed_seconds) or elapsed_seconds < 0:
        raise ValueError('Elapsed time must be finite and nonnegative.')

    gaps = assessment['gaps']
    open_ids = {g['id'] for g in gaps}
    limits = state.request.limits
    remaining = limits.max_assignments - state.assignments_started

    def stop(reason):
        return {'action': 'stop', 'stop_reason': reason, 'planner_gaps': []}

    if not gaps and not assessment['review_queue']:
        return stop('requirements_satisfied')
    if (state.round_index >= limits.max_rounds or remaining <= 0
            or elapsed_seconds >= limits.max_elapsed_seconds
            or state.estimated_cost_usd >= limits.max_estimated_cost_usd
            or not state.cost_accounting_complete):
        return stop('execution_limit')
    if not gaps or (previous_gap_ids is not None and not previous_gap_ids - open_ids):
        return stop('no_progress')
    return {'action': 'replan', 'stop_reason': None,
            'planner_gaps': gaps[:min(2, remaining)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    request = ResearchRequest.model_validate_json(args.request.read_text(encoding='utf-8'))
    profile = Profile.model_validate_json(args.profile.read_text(encoding='utf-8'))
    assessment = assess_gaps(request, profile)
    with args.out.open('x', encoding='utf-8') as output:
        json.dump(assessment, output, ensure_ascii=False, indent=2)
        output.write('\n')
    print(json.dumps({
        'actionable_gaps': len(assessment['gaps']),
        'pending_review': len(assessment['review_queue']),
        'first_followup': assessment['gaps'][0]['role']
            if assessment['gaps'] and 'role' in assessment['gaps'][0] else None,
    }, indent=2))
    print(f'GAP_ASSESSMENT={args.out.resolve()}')


if __name__ == '__main__':
    main()
