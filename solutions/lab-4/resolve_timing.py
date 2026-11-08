"""Prepare a targeted assignment or apply a human-reviewed timing decision."""
import argparse
import json
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import Field

from assess_gaps import Profile, assess_gaps
from contracts import Assignment, Contract, EvidenceRef, ResearchRequest
from evidence_archive import contains_excerpt, load_collection, load_snapshot, read_json
from merger import role_key
from validation_contracts import Role


class TimingReview(Contract):
    subject_name: str
    reference_date: date
    role: Role
    evidence: EvidenceRef
    fact: Literal['ended_on', 'held_on']
    date: date
    date_text: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    reviewer_note: str = Field(min_length=1)


def make_assignment(request, assessment, gap_id):
    if (assessment['subject_name'], assessment['reference_date']) != (
        request.subject_name, request.reference_date.isoformat()
    ):
        raise ValueError('Gap assessment does not match the request.')
    gap = next((item for item in assessment['gaps'] if item['id'] == gap_id), None)
    if gap is None or gap['kind'] not in ('unresolved_timing', 'timing_disagreement'):
        raise ValueError('Select an open timing gap.')
    known_urls = ', '.join(gap['existing_source_urls'])
    return Assignment(
        id=gap['id'], question=gap['question'],
        evidence_requirements=[
            *gap['evidence_requirements'],
            'Prefer a dated primary source with a documented end date or explicit '
            'status on the reference date.',
            f'Already cited for this role: {known_urls}. Reuse it only if it adds dated evidence.',
        ],
        preferred_source_types=gap['preferred_source_types'],
    )


def timing_status(review):
    if review.fact == 'ended_on' and review.date < review.reference_date:
        return 'historical_as_of_reference'
    if review.fact == 'held_on' and review.date == review.reference_date:
        return 'current_as_of_reference'
    return 'unknown'


def apply_review(request, profile, review, collection_dir):
    original = Profile.model_validate(profile)
    if (original.subject_name, original.reference_date) != (
        request.subject_name, request.reference_date
    ) or (review.subject_name, review.reference_date) != (
        request.subject_name, request.reference_date
    ):
        raise ValueError('Profile or timing review does not match the research request.')

    updated = deepcopy(profile)
    matching = [
        item for item in updated['claims']
        if role_key(Role.model_validate(item['role'])) == role_key(review.role)
    ]
    if len(matching) != 1 or matching[0]['timing'] != 'unknown':
        raise ValueError('Expected one matching claim with unresolved timing.')
    claim = matching[0]
    evidence_ref = review.evidence.model_dump(mode='json')
    citations = [item for item in claim['citations'] if item['evidence'] == evidence_ref]
    if len(citations) != 1:
        raise ValueError('The timing evidence must already be an accepted citation for this role.')
    citation = citations[0]

    collection_id, rows, sources = load_collection(collection_dir, request)
    row = next((row for row in rows if row.id == review.evidence.evidence_id), None)
    if (collection_id != review.evidence.collection_id or row is None
            or row.source_snapshot != citation['source_snapshot']):
        raise ValueError('The citation does not match the supplied evidence archive.')
    snapshot = load_snapshot(collection_dir, row, sources)
    if (snapshot['resolved_url'] != citation['source_url']
            or not contains_excerpt(snapshot['text'], review.quote)
            or not contains_excerpt(review.quote, review.date_text)):
        raise ValueError('The reviewed quote and date text must match the cited snapshot.')

    status = timing_status(review)
    signals = {item['validator_timing'] for item in claim['citations']} - {'unknown'}
    if (status != 'unknown' and not claim['timing_assessments_disagree']
            and not (signals - {status})):
        claim['timing'] = status
        claim['timing_review'] = review.model_dump(mode='json')
        updated['timing_policy'] = 'require_cited_review_for_dated_status'
    return updated, assess_gaps(request, Profile.model_validate(updated))


def write_new(path, value):
    with path.open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    assignment = commands.add_parser('assignment')
    apply = commands.add_parser('apply')
    for command in (assignment, apply):
        command.add_argument('--request', required=True, type=Path)
    assignment.add_argument('--gaps', required=True, type=Path)
    assignment.add_argument('--gap-id', required=True)
    assignment.add_argument('--out', required=True, type=Path)
    apply.add_argument('--profile', required=True, type=Path)
    apply.add_argument('--review', required=True, type=Path)
    apply.add_argument('--collection', required=True, type=Path)
    apply.add_argument('--out-dir', required=True, type=Path)
    args = parser.parse_args()
    request = ResearchRequest.model_validate(read_json(args.request))
    if args.action == 'assignment':
        result = make_assignment(request, read_json(args.gaps), args.gap_id)
        write_new(args.out, result.model_dump(mode='json'))
        print(f'ASSIGNMENT={args.out.resolve()}')
    else:
        review = TimingReview.model_validate(read_json(args.review))
        profile, gaps = apply_review(request, read_json(args.profile), review, args.collection)
        args.out_dir.mkdir(exist_ok=False)
        write_new(args.out_dir / 'merged_profile.json', profile)
        write_new(args.out_dir / 'gap_assessment.json', gaps)
        print(f'OPEN_GAPS={len(gaps["gaps"])}')


if __name__ == '__main__':
    main()
