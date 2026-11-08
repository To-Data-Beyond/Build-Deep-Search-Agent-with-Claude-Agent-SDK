"""Controlled archive and review tests. No model requests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from contracts import ResearchRequest, ResearchState
from stage_runner import save_json
from validation_contracts import Assessment, ReviewBatch
from validator import validate_collection


class ValidatorChecks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.collection = self.root / 'collection'
        self.output = self.root / 'validation'
        (self.collection / 'sources').mkdir(parents=True)
        self.output.mkdir()
        self.anchor = 'https://example.org/'
        self.text = 'Jordan Lee is director at Example Lab.'
        self.snapshot = {
            'requested_url': self.anchor,
            'retrieved_at': '2025-04-01T12:00:00+00:00',
            'tool_response': {'code': 200, 'result': self.text, 'url': self.anchor},
        }
        save_json(self.collection / 'sources/old.json', self.snapshot)
        # The manifest points at a later capture. The evidence cites the old one.
        save_json(self.collection / 'sources/latest.json', {
            **self.snapshot, 'tool_response': {'code': 200, 'result': 'Updated page.'},
        })
        save_json(self.collection / 'manifest.json', {
            'run_id': 'controlled-collection', 'status': 'collected',
            'evidence_count': 1, 'evidence_ids': ['E1'],
            'sources': [{'id': 'S1', 'requested_url': self.anchor,
                         'snapshot': 'sources/latest.json'}],
        })
        save_json(self.collection / 'task.json', {
            'subject_name': 'Jordan Lee', 'identity_anchor_urls': [self.anchor],
            'sub_question_id': 'roles', 'question': 'What roles are supported?',
            'preferred_source_types': ['official_company_page'],
        })
        self.row = {
            'id': 'E1', 'sub_question_id': 'roles', 'claim_type': 'current_role',
            'claim': self.text, 'source_url': self.anchor,
            'source_type': 'official_company_page', 'title': 'Role directory',
            'quote': self.text, 'identity_signals': ['Jordan Lee'],
            'source_id': 'S1', 'source_snapshot': 'sources/old.json',
            'resolved_source_url': self.anchor, 'retrieval_date': '2025-04-01',
            'validation_status': 'pending',
        }
        request = ResearchRequest(
            subject_name='Jordan Lee', identity_anchor_urls=[self.anchor],
            scope='Professional roles', reference_date='2026-10-31',
        )
        self.state = ResearchState(run_id='controlled-validation', request=request)

    async def run_with(self, **changes):
        path = self.collection / 'evidence.jsonl'
        before = json.dumps(self.row) + '\n'
        path.write_text(before, encoding='utf-8')
        assessment = Assessment.model_validate({
            'evidence_id': 'E1', 'identity': 'matched', 'authority': 'primary',
            'role_support': 'supported',
            'role': {'title': 'director', 'organization': 'Example Lab'},
            'role_excerpt': self.text, 'timing': 'unknown', 'timing_excerpt': None,
            'reason': 'The saved passage names the role but does not date it.',
            **changes,
        })
        call = AsyncMock(return_value=ReviewBatch(assessments=[assessment]))
        with patch('validator.run_json_stage', call):
            decisions = await validate_collection(
                self.state, self.collection, self.output, 'offline-test',
                deadline=1,
            )
        self.assertEqual(path.read_text(encoding='utf-8'), before)
        return decisions[0], call

    async def test_exact_snapshot_and_unknown_timing(self):
        result, call = await self.run_with()
        self.assertEqual(result.verdict, 'accepted')
        self.assertEqual(result.timing, 'unknown')
        self.assertEqual(result.evidence.collection_id, 'controlled-collection')
        self.assertEqual(result.accepted_role.organization, 'Example Lab')
        self.assertEqual(call.await_args.args[6]['snapshots']['sources/old.json']['text'],
                         self.text)

    async def test_missing_snapshot_needs_review_without_call(self):
        (self.collection / 'sources/old.json').unlink()
        result, call = await self.run_with()
        self.assertEqual(result.verdict, 'needs_review')
        call.assert_not_awaited()

    async def test_path_escape_is_rejected_without_call(self):
        self.row['source_snapshot'] = '../outside.json'
        result, call = await self.run_with()
        self.assertEqual(result.verdict, 'rejected')
        call.assert_not_awaited()

    async def test_quote_mismatch_rejected_without_call(self):
        self.row['quote'] = 'A quotation that is absent.'
        result, call = await self.run_with()
        self.assertEqual(result.verdict, 'rejected')
        call.assert_not_awaited()

    async def test_identity_mismatch_rejected(self):
        result, _ = await self.run_with(identity='mismatch')
        self.assertEqual(result.verdict, 'rejected')
        self.assertIsNone(result.accepted_role)

    async def test_unobserved_role_passage_not_accepted(self):
        result, _ = await self.run_with(role_excerpt='A nonexistent support passage.')
        self.assertEqual(result.verdict, 'needs_review')

    async def test_unobserved_timing_passage_downgraded(self):
        result, _ = await self.run_with(
            timing='current_as_of_reference', timing_excerpt='As of October 31, 2026.'
        )
        self.assertEqual(result.verdict, 'accepted')
        self.assertEqual(result.timing, 'unknown')

    async def test_unexpected_assessment_id_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'exactly one assessment'):
            await self.run_with(evidence_id='E999')
        self.assertFalse((self.output / 'validation.json').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
