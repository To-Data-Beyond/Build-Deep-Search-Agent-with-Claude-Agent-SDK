"""Build a role profile from reviewed collections without calling a model."""
import argparse
import json
from datetime import date
from pathlib import Path
from typing import Literal

from contracts import Contract, ResearchRequest
from evidence_archive import contains_excerpt, load_collection, load_snapshot, read_json
from validation_contracts import Decision


class ValidationFile(Contract):
    collection_id: str
    reference_date: date
    review_basis: Literal['saved_webfetch_response']
    decisions: list[Decision]


def role_key(role):
    return tuple(' '.join(value.split()).casefold()
                 for value in (role.organization, role.title))


def merge_profile(request, inputs):
    groups, review_queue, exclusions = {}, [], []
    seen = set()
    for collection, validation_path in inputs:
        collection_id, rows, sources = load_collection(collection, request)
        review = ValidationFile.model_validate(read_json(validation_path))
        if review.collection_id != collection_id or review.reference_date != request.reference_date:
            raise ValueError('Validation collection or reference date does not match.')
        if collection_id in seen:
            raise ValueError(f'Provide one validation file per collection: {collection_id}')
        seen.add(collection_id)

        by_id = {decision.evidence.evidence_id: decision for decision in review.decisions}
        if len(by_id) != len(review.decisions) or set(by_id) != {row.id for row in rows}:
            raise ValueError('Expected exactly one decision per archived evidence record.')

        for row in rows:
            decision = by_id[row.id]
            if (decision.evidence.collection_id != collection_id
                    or decision.source_snapshot != row.source_snapshot):
                raise ValueError(f'Archive reference mismatch: {collection_id}/{row.id}')
            ref = decision.evidence.model_dump(mode='json')
            if decision.verdict != 'accepted':
                target = review_queue if decision.verdict == 'needs_review' else exclusions
                target.append({'evidence': ref, 'reasons': decision.reasons})
                continue

            role, assessment = decision.accepted_role, decision.assessment
            if (role is None or assessment is None or assessment.role != role
                    or assessment.evidence_id != row.id or assessment.identity != 'matched'
                    or assessment.authority != 'primary' or assessment.role_support != 'supported'):
                raise ValueError(f'Inconsistent acceptance: {collection_id}/{row.id}')
            snapshot = load_snapshot(collection, row, sources)
            if not contains_excerpt(snapshot['text'], assessment.role_excerpt):
                raise ValueError(f'Role passage missing: {collection_id}/{row.id}')
            if decision.timing != 'unknown' and (
                assessment.timing != decision.timing
                or not contains_excerpt(snapshot['text'], assessment.timing_excerpt)
            ):
                raise ValueError(f'Timing passage mismatch: {collection_id}/{row.id}')

            groups.setdefault(role_key(role), []).append({
                'role': role.model_dump(mode='json'),
                'evidence': ref,
                'source_snapshot': row.source_snapshot,
                'source_url': snapshot['resolved_url'],
                'retrieved_at': snapshot['retrieved_at'],
                'role_excerpt': assessment.role_excerpt,
                'validator_timing': decision.timing,
                'validator_timing_excerpt': assessment.timing_excerpt,
                'validation_reasons': decision.reasons,
            })

    def evidence_key(item):
        return item['evidence']['collection_id'], item['evidence']['evidence_id']

    claims = []
    for _, records in sorted(groups.items()):
        records.sort(key=evidence_key)
        role = records[0]['role']
        citations = [{key: value for key, value in record.items() if key != 'role'}
                     for record in records]
        signals = {citation['validator_timing'] for citation in citations} - {'unknown'}
        claims.append({
            'role': role,
            'timing': 'unknown',
            'timing_assessments_disagree': len(signals) > 1,
            'distinct_source_urls': sorted({citation['source_url'] for citation in citations}),
            'citations': citations,
        })

    return {
        'subject_name': request.subject_name,
        'reference_date': request.reference_date.isoformat(),
        'timing_policy': 'retain_validator_signals_without_promoting_to_dated_status',
        'claims': claims,
        'needs_review': sorted(review_queue, key=evidence_key),
        'rejected_evidence': sorted(exclusions, key=evidence_key),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--input', nargs=2, action='append', type=Path, required=True,
                        metavar=('COLLECTION_DIR', 'VALIDATION_JSON'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    request = ResearchRequest.model_validate(read_json(args.request))
    profile = merge_profile(request, args.input)
    with args.out.open('x', encoding='utf-8') as file:
        file.write(json.dumps(profile, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({
        'claims': len(profile['claims']),
        'timing_unknown': sum(claim['timing'] == 'unknown' for claim in profile['claims']),
        'timing_assessments_disagree': sum(claim['timing_assessments_disagree']
                                           for claim in profile['claims']),
        'needs_review': len(profile['needs_review']),
        'rejected_evidence': len(profile['rejected_evidence']),
    }, indent=2))
    print(f'MERGED_PROFILE={args.out.resolve()}')


if __name__ == '__main__':
    main()
