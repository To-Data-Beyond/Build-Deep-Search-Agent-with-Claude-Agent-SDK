"""Run the timing policy against a fictional role and a saved fixture archive."""
import json
import tempfile
from datetime import date
from pathlib import Path

from assess_gaps import Profile, assess_gaps
from contracts import ResearchRequest
from merger import merge_profile
from resolve_timing import TimingReview, apply_review, make_assignment, write_new


def fixture(root, request, *, dated):
    folder = root / ('fixture-followup' if dated else 'fixture-initial')
    (folder / 'sources').mkdir(parents=True)
    url = 'https://example.org/'
    quote = ('Jordan Lee ended their term as Director at Example Lab on May 12, 2020.'
             if dated else 'Jordan Lee served as Director at Example Lab.')
    timing = 'historical_as_of_reference' if dated else 'unknown'
    snapshot = 'sources/notice.json'
    ref = {'collection_id': folder.name, 'evidence_id': 'E1'}
    role = {'title': 'Director', 'organization': 'Example Lab'}
    write_new(folder / 'task.json', {
        'subject_name': request.subject_name, 'identity_anchor_urls': [url],
        'sub_question_id': 'example_lab', 'question': 'When was the role held?',
        'preferred_source_types': ['official_company_page'],
    })
    write_new(folder / 'manifest.json', {
        'status': 'collected', 'run_id': folder.name,
        'evidence_ids': ['E1'], 'evidence_count': 1,
        'sources': [{'id': 'S1', 'requested_url': url, 'snapshot': snapshot}],
    })
    write_new(folder / snapshot, {
        'requested_url': url, 'retrieved_at': '2026-11-08T00:00:00Z',
        'tool_response': {'code': 200, 'result': quote, 'url': url},
    })
    row = {
        'id': 'E1', 'sub_question_id': 'example_lab', 'claim_type': 'previous_role',
        'claim': quote, 'source_url': url, 'source_type': 'official_company_page',
        'title': 'Synthetic company notice', 'quote': quote, 'identity_signals': ['Jordan Lee'],
        'source_id': 'S1', 'source_snapshot': snapshot, 'resolved_source_url': url,
        'retrieval_date': '2026-11-08', 'validation_status': 'pending',
    }
    (folder / 'evidence.jsonl').write_text(json.dumps(row) + '\n', encoding='utf-8')
    decisions = folder / 'validation.json'
    write_new(decisions, {
        'collection_id': folder.name, 'reference_date': request.reference_date.isoformat(),
        'review_basis': 'saved_webfetch_response',
        'decisions': [{
            'evidence': ref, 'source_snapshot': snapshot, 'verdict': 'accepted',
            'reasons': ['Synthetic review.'], 'accepted_role': role,
            'timing': timing, 'assessment': {
                'evidence_id': 'E1', 'identity': 'matched', 'authority': 'primary',
                'role_support': 'supported', 'role': role, 'role_excerpt': quote,
                'timing': timing, 'timing_excerpt': quote if dated else None,
                'reason': 'Synthetic role review.',
            },
        }],
    })
    if not dated:
        return folder, decisions, None
    review = TimingReview.model_validate({
        'subject_name': request.subject_name, 'reference_date': request.reference_date,
        'role': role, 'evidence': ref, 'fact': 'ended_on', 'date': '2020-05-12',
        'date_text': 'May 12, 2020', 'quote': quote,
        'reviewer_note': 'This fictional notice gives the end date of the same appointment.',
    })
    return folder, decisions, review


def main():
    request = ResearchRequest(
        subject_name='Jordan Lee', identity_anchor_urls=['https://example.org/'],
        scope='Identify professional roles and distinguish current and historical.',
        reference_date=date(2026, 10, 31),
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        original_dir, original_decisions, _ = fixture(root, request, dated=False)
        initial = merge_profile(request, [(original_dir, original_decisions)])
        before = assess_gaps(request, Profile.model_validate(initial))
        folder, decisions, review = fixture(root, request, dated=True)
        profile = merge_profile(request, [(original_dir, original_decisions), (folder, decisions)])
        assignment = make_assignment(request, before, before['gaps'][0]['id'])
        assert assignment.id == before['gaps'][0]['id']

        updated, after = apply_review(request, profile, review, folder)
        assert len(before['gaps']) == 1 and not after['gaps']
        assert updated['claims'][0]['timing'] == 'historical_as_of_reference'
        assert updated['claims'][0]['timing_review']['evidence'] == review.evidence.model_dump()
        assert len(updated['claims'][0]['citations']) == 2
        assert updated['claims'][0]['citations'] == profile['claims'][0]['citations']
        assert profile['claims'][0]['timing'] == 'unknown'

        dated_lead = review.model_copy(update={'fact': 'held_on'})
        unchanged, still_open = apply_review(request, profile, dated_lead, folder)
        assert unchanged == profile and len(still_open['gaps']) == 1

        broken = review.model_copy(update={'date_text': 'May 12, 2019'})
        try:
            apply_review(request, profile, broken, folder)
        except ValueError as error:
            assert 'match the cited snapshot' in str(error)
        else:
            raise AssertionError('A date absent from the quote was accepted.')

        print(json.dumps({
            'mode': 'offline_fixture',
            'open_timing_gaps_before_review': len(before['gaps']),
            'open_timing_gaps_after_review': len(after['gaps']),
            'timing': updated['claims'][0]['timing'],
            'citations_retained': len(updated['claims'][0]['citations']),
        }, indent=2))


if __name__ == '__main__':
    main()
