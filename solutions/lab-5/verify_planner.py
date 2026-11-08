"""Controlled local checks. Stubbed SDK results are not research output."""
import asyncio
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_agent_sdk import ResultMessage

from contracts import ResearchRequest, ResearchState
from plan_checks import check_plan
from planner import build_plan
from planning_contracts import ResearchPlan
from stage_runner import ExecutionLimit, output_only, run_json_stage


PLAN = {
    'rationale': 'Inspect primary sources and preserve uncertainty.',
    'assignments': [{
        'id': 'official_roles',
        'question': 'Which professional roles do the official sources describe?',
        'evidence_requirements': ['Find source passages naming the role and organization.'],
        'preferred_source_types': ['personal_website', 'official_company_page'],
    }],
}
APPROVE = {'verdict': 'approve', 'issues': []}
REVISE = {'verdict': 'revise', 'issues': ['Make the temporal evidence requirements explicit.']}


def response(payload, cost=0.01, subtype='success'):
    return ResultMessage(
        subtype=subtype, duration_ms=1, duration_api_ms=1,
        is_error=subtype != 'success', num_turns=2,
        session_id='controlled-offline-session', total_cost_usd=cost,
        stop_reason='end_turn', terminal_reason='completed',
        usage={}, structured_output=payload,
    )


class PlannerChecks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        request = ResearchRequest.model_validate_json(
            Path(__file__).with_name('research_request.json').read_text()
        )
        self.state = ResearchState(run_id='controlled-local-check', request=request)
        self.calls = []

    def stub(self, responses):
        queued = iter(responses)

        async def query_stub(*, prompt, options):
            self.calls.append((json.loads(prompt), options))
            yield next(queued)
        return query_stub

    async def test_approved_plan_is_ready_but_collection_has_not_started(self):
        with patch('stage_runner.query', self.stub([response(PLAN), response(APPROVE)])):
            await build_plan(self.state, self.directory, 'offline-test-model')
        self.assertEqual(self.state.phase, 'collecting')
        self.assertEqual(self.state.round_index, 1)
        self.assertEqual(self.state.assignments_started, 0)
        self.assertAlmostEqual(self.state.estimated_cost_usd, 0.02)
        self.assertTrue((self.directory / 'plan.json').exists())
        options = self.calls[0][1]
        self.assertEqual(options.tools, [])
        self.assertTrue(options.strict_mcp_config)

    async def test_revision_receives_feedback_and_keeps_all_call_costs(self):
        replies = [response(PLAN), response(REVISE), response(PLAN), response(APPROVE)]
        with patch('stage_runner.query', self.stub(replies)):
            await build_plan(self.state, self.directory, 'offline-test-model')
        self.assertEqual(self.calls[2][0]['feedback'], REVISE['issues'])
        self.assertAlmostEqual(self.state.estimated_cost_usd, 0.04)
        self.assertEqual(len(self.calls), 4)

    async def test_duplicate_ids_skip_model_review_and_require_revision(self):
        duplicate = copy.deepcopy(PLAN)
        duplicate['assignments'] *= 2
        replies = [response(duplicate), response(PLAN), response(APPROVE)]
        with patch('stage_runner.query', self.stub(replies)):
            await build_plan(self.state, self.directory, 'offline-test-model')
        self.assertFalse((self.directory / 'review-1.call.json').exists())
        self.assertTrue(any('Duplicate assignment ID' in item
                            for item in self.calls[1][0]['feedback']))

    async def test_two_rejections_never_write_an_approved_plan(self):
        replies = [response(PLAN), response(REVISE), response(PLAN), response(REVISE)]
        with patch('stage_runner.query', self.stub(replies)):
            with self.assertRaisesRegex(RuntimeError, 'not approved'):
                await build_plan(self.state, self.directory, 'offline-test-model')
        self.assertEqual(self.state.plan, [])
        self.assertFalse((self.directory / 'plan.json').exists())

    async def test_missing_structured_output_is_failure_and_cost_is_retained(self):
        with patch('stage_runner.query', self.stub([response(None)])):
            with self.assertRaisesRegex(RuntimeError, 'structured result'):
                await build_plan(self.state, self.directory, 'offline-test-model')
        self.assertAlmostEqual(self.state.estimated_cost_usd, 0.01)
        self.assertEqual(len(self.calls), 1)

    async def test_unknown_cost_prevents_a_review_call(self):
        with patch('stage_runner.query', self.stub([response(PLAN, cost=None)])):
            with self.assertRaisesRegex(RuntimeError, 'cost is unknown'):
                await build_plan(self.state, self.directory, 'offline-test-model')
        self.assertFalse(self.state.cost_accounting_complete)
        self.assertEqual(len(self.calls), 1)

    async def test_spending_threshold_stops_before_another_call(self):
        self.state.request.limits.max_estimated_cost_usd = 0.005
        with patch('stage_runner.query', self.stub([response(PLAN)])):
            with self.assertRaises(ExecutionLimit):
                await build_plan(self.state, self.directory, 'offline-test-model')
        self.assertEqual(len(self.calls), 1)
        self.assertAlmostEqual(self.state.estimated_cost_usd, 0.01)

    async def test_expired_deadline_starts_no_sdk_call(self):
        with patch('stage_runner.query', self.stub([])):
            with self.assertRaises(ExecutionLimit):
                await build_plan(self.state, self.directory, 'offline-test-model',
                                 deadline=time.monotonic() - 1)
        self.assertEqual(self.calls, [])

    async def test_tool_gate_allows_output_and_denies_web_access(self):
        self.assertEqual(await output_only({'tool_name': 'StructuredOutput'}, None, None), {})
        denied = await output_only({'tool_name': 'WebFetch'}, None, None)
        self.assertEqual(denied['hookSpecificOutput']['permissionDecision'], 'deny')

    async def test_source_labels_and_blank_requirements_are_rejected(self):
        bad = copy.deepcopy(PLAN)
        bad['assignments'][0]['preferred_source_types'] = ['invented_source_type']
        bad['assignments'][0]['evidence_requirements'] = [' ']
        issues = check_plan(ResearchPlan.model_validate(bad), 2)
        self.assertTrue(any('unsupported source' in item for item in issues))
        self.assertTrue(any('cannot be blank' in item for item in issues))


if __name__ == '__main__':
    unittest.main(verbosity=2)
