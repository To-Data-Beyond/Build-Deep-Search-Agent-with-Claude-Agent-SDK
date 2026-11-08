"""Replay one synthetic round locally through the real file handoffs."""
import asyncio
import contextlib
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from controller import run_first_round
from init_run import init_run
from verify_planner import APPROVE, PLAN, response


async def main():
    anchor = 'https://example.org/'
    passage = 'Jordan Lee served as Director at Example Lab.'
    validation = {'assessments': [{
        'evidence_id': 'E1', 'identity': 'matched', 'authority': 'primary',
        'role_support': 'supported',
        'role': {'title': 'Director', 'organization': 'Example Lab'},
        'role_excerpt': passage, 'timing': 'unknown', 'timing_excerpt': None,
        'reason': 'The fixture names the role but gives no dates.',
    }]}
    results = iter([response(PLAN), response(APPROVE), response(validation)])

    async def structured_query(**kwargs):
        yield next(results)

    async def collect(collector, model, *, max_budget_usd):
        payload = {'tool_name': 'WebFetch', 'tool_input': {'url': anchor},
                   'tool_response': {'code': 200, 'url': anchor, 'result': passage}}
        await collector.before_tool(payload, 'fetch-1', None)
        await collector.after_tool(payload, 'fetch-1', None)
        receipt = await collector.record_evidence({
            'claim_type': 'previous_role', 'claim': passage, 'source_url': anchor,
            'source_type': 'official_company_page', 'title': 'Fixture bio',
            'quote': passage, 'identity_signals': ['Jordan Lee'],
        })
        assert not receipt['isError'], receipt
        collector.result = response(None, cost=0.02)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        request = root / 'request.json'
        request.write_text(json.dumps({
            'subject_name': 'Jordan Lee', 'identity_anchor_urls': [anchor],
            'scope': 'Identify professional roles and preserve uncertainty about dates.',
            'reference_date': '2026-10-31',
        }), encoding='utf-8')
        state_path = init_run(request, root / 'artifacts')
        with (patch('stage_runner.query', structured_query),
              patch('collector.Collector.run', collect),
              contextlib.redirect_stdout(io.StringIO())):
            checkpoint = await run_first_round(state_path, 'offline-fixture')
        state = checkpoint.research
        profile = json.loads(Path(state.merged_profile_artifact).read_text(encoding='utf-8'))
        gaps = json.loads(Path(state.gap_assessment_artifact).read_text(encoding='utf-8'))
        assert checkpoint.started_at is not None
        assert state.phase == 'assessing' and state.stop_reason is None
        assert state.round_index == state.assignments_started == len(state.collections) == 1
        assert abs(state.estimated_cost_usd - 0.05) < 1e-9
        assert len(profile['claims']) == len(gaps['gaps']) == 1
        citation = profile['claims'][0]['citations'][0]
        assert citation['evidence'] == {'collection_id': 'r01-official_roles', 'evidence_id': 'E1'}
        assert gaps['gaps'][0]['evidence_refs'] == [citation['evidence']]
        persisted = type(checkpoint).model_validate_json(state_path.read_text(encoding='utf-8'))
        assert persisted == checkpoint
        print(json.dumps({
            'mode': 'offline_fixture',
            'phase': state.phase,
            'round_index': state.round_index,
            'assignments_started': state.assignments_started,
            'merged_claims': len(profile['claims']),
            'open_gaps': len(gaps['gaps']),
            'stop_reason': state.stop_reason,
        }, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
