"""Exercise stop decisions and partial reports with simulated API responses."""
import asyncio
import contextlib
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from controller import run_workflow
from evidence_archive import read_json
from init_run import init_run
from verify_planner import APPROVE, PLAN, response


async def exercise(root, scenario):
    anchor = 'https://example.org/'
    passage = 'Jordan Lee served as Director at Example Lab.'
    review = {'assessments': [{
        'evidence_id': 'E1', 'identity': 'matched', 'authority': 'primary',
        'role_support': 'supported',
        'role': {'title': 'Director', 'organization': 'Example Lab'},
        'role_excerpt': passage, 'timing': 'unknown', 'timing_excerpt': None,
        'reason': 'The fixture names the role but gives no dates.',
    }]}
    payloads = []
    calls = [PLAN, APPROVE, review, PLAN, APPROVE]
    if scenario == 'no_progress':
        calls.append(review)
    replies = iter(response(item) for item in calls)

    async def query(*, prompt, options):
        payloads.append(json.loads(prompt))
        yield next(replies)

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
        second = collector.directory.name.startswith('r02-')
        if scenario == 'execution_limit' and second:
            assert abs(max_budget_usd - 0.03) < 1e-9
        cost = 0.08 if scenario == 'execution_limit' and second else 0.02
        collector.result = response(None, cost=cost)

    request = root / 'request.json'
    request.write_text(json.dumps({
        'subject_name': 'Jordan Lee', 'identity_anchor_urls': [anchor],
        'scope': 'Identify roles and preserve uncertainty about dates.',
        'reference_date': '2026-10-31',
        'limits': {'max_rounds': 3, 'max_estimated_cost_usd':
                   0.10 if scenario == 'execution_limit' else 3.0},
    }), encoding='utf-8')
    state_path = init_run(request, root / 'artifacts')
    with (patch('stage_runner.query', query),
          patch('collector.Collector.run', collect),
          contextlib.redirect_stdout(io.StringIO())):
        checkpoint = await run_workflow(state_path, 'offline-fixture', until_stop=True)

    run_dir = state_path.parent
    report = read_json(run_dir / 'run_report.json')
    expected_citations = 1 if scenario == 'execution_limit' else 2
    assert checkpoint.research.phase == 'stopped'
    assert report['stop_reason'] == scenario
    assert report['assignments_started'] == 2
    assert len(report['claims']) == len(report['open_gaps']) == 1
    assert len(report['claims'][0]['citations']) == expected_citations
    assert payloads[3]['gaps'][0]['id'] == report['open_gaps'][0]['id']
    assert len(payloads) == (5 if scenario == 'execution_limit' else 6)
    return {'stop_reason': report['stop_reason'],
            'assignments_started': report['assignments_started'],
            'validated_claims': len(report['claims']),
            'citations_retained': len(report['claims'][0]['citations']),
            'open_gaps': len(report['open_gaps'])}


async def main():
    cases = []
    for scenario in ('execution_limit', 'no_progress'):
        with tempfile.TemporaryDirectory() as directory:
            cases.append(await exercise(Path(directory), scenario))
    print(json.dumps({'mode': 'offline_fixture', 'cases': cases}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
