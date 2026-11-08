"""Controlled checks for follow-up selection and stopping."""
import unittest

from assess_gaps import Profile, assess_gaps, decide_next
from contracts import ResearchRequest, ResearchState


class GapChecks(unittest.TestCase):
    def setUp(self):
        self.request = ResearchRequest(
            subject_name='Jordan Lee', identity_anchor_urls=['https://example.org/'],
            scope='Identify roles and their temporal status.', reference_date='2026-10-31',
        )
        self.profile = Profile.model_validate({
            'subject_name': 'Jordan Lee', 'reference_date': '2026-10-31',
            'needs_review': [], 'claims': [
                {
                    'role': {'title': 'Director', 'organization': organization},
                    'timing': 'unknown', 'timing_assessments_disagree': False,
                    'citations': [{
                        'evidence': {'collection_id': 'collection-a', 'evidence_id': ref},
                        'source_url': 'https://example.org/', 'validator_timing': timing,
                    }],
                } for organization, ref, timing in [
                    ('A Lab', 'E1', 'unknown'),
                    ('B Lab', 'E2', 'historical_as_of_reference'),
                ]
            ],
        })
        self.state = ResearchState(
            run_id='example-run', request=self.request, phase='assessing', round_index=1,
            assignments_started=3,
        )
        self.assessment = assess_gaps(self.request, self.profile)

    def test_prioritizes_historical_lead_and_caps_batch_by_remaining_assignments(self):
        result = decide_next(self.state, self.assessment, elapsed_seconds=30)
        self.assertEqual(result['action'], 'replan')
        gap, = result['planner_gaps']
        self.assertEqual(gap['role']['organization'], 'B Lab')
        self.assertEqual(gap['evidence_refs'][0]['evidence_id'], 'E2')
        self.assertEqual(self.state.phase, 'assessing')

    def test_unchanged_gaps_stop_after_followup(self):
        previous_ids = {g['id'] for g in self.assessment['gaps']}
        result = decide_next(self.state, self.assessment, elapsed_seconds=30,
                             previous_gap_ids=previous_ids)
        self.assertEqual(result['stop_reason'], 'no_progress')

    def test_execution_limits(self):
        for changes, elapsed in [
            ({'round_index': 2}, 30), ({'assignments_started': 4}, 30), ({}, 300),
            ({'estimated_cost_usd': 3.0}, 30), ({'cost_accounting_complete': False}, 30),
        ]:
            with self.subTest(changes=changes, elapsed=elapsed):
                state = self.state.model_copy(update=changes)
                self.assertEqual(decide_next(state, self.assessment, elapsed_seconds=elapsed)
                                 ['stop_reason'], 'execution_limit')

    def test_empty_profile_does_not_satisfy_requirements(self):
        empty = self.profile.model_copy(update={'claims': []})
        result = assess_gaps(self.request, empty)
        self.assertEqual(result['gaps'][0]['kind'], 'coverage')

    def test_dated_status_closes_the_role_gap(self):
        for claim in self.profile.claims:
            claim.timing = 'historical_as_of_reference'
        result = assess_gaps(self.request, self.profile)
        self.assertEqual(decide_next(self.state, result, elapsed_seconds=30)
                         ['stop_reason'], 'requirements_satisfied')


if __name__ == '__main__':
    unittest.main(verbosity=2)
