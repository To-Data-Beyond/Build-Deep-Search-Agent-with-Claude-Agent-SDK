"""Simulate controller interruptions with offline SDK fixtures."""
import asyncio
import contextlib
import io
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import controller
from evidence_archive import read_json
from init_run import RunCheckpoint, init_run
from verify_planner import APPROVE, PLAN, response


class SimulatedExit(BaseException):
    """Bypass normal error handling, as an abrupt process exit would."""


async def exercise(root, scenario):
    anchor = 'https://example.org/'
    passage = 'Jordan Lee served as Director at Example Lab.'
    review = {'assessments': [{
        'evidence_id': 'E1', 'identity': 'matched', 'authority': 'primary',
        'role_support': 'supported', 'role': {'title': 'Director', 'organization': 'Example Lab'},
        'role_excerpt': passage, 'timing': 'unknown', 'timing_excerpt': None,
        'reason': 'The fixture names the role without dates.',
    }]}
    plan = {**PLAN, 'assignments': [
        PLAN['assignments'][0],
        {**PLAN['assignments'][0], 'id': 'prior_roles',
         'question': 'What roles does the organization describe as historical?'},
    ]}
    replies = iter([response(plan), response(APPROVE), response(review), response(review)])
    calls = []

    async def query(**kwargs):
        calls.append('structured')
        yield next(replies)

    async def collect(collector, model, *, max_budget_usd):
        assignment = collector.task.sub_question_id
        calls.append(f'collect:{assignment}')
        if scenario == 'interrupted_call' and assignment == 'prior_roles':
            raise SimulatedExit()
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

    request = root / 'request.json'
    request.write_text(json.dumps({
        'subject_name': 'Jordan Lee', 'identity_anchor_urls': [anchor],
        'scope': 'Identify roles and preserve uncertainty about dates.',
        'reference_date': '2026-10-31', 'limits': {'max_rounds': 1},
    }), encoding='utf-8')
    path = init_run(request, root / 'artifacts')
    save = controller.save_checkpoint
    interrupt = scenario != 'interrupted_call'

    def save_and_interrupt(path, checkpoint):
        nonlocal interrupt
        save(path, checkpoint)
        if (interrupt and checkpoint.pending_call is None
                and len(checkpoint.research.validation_artifacts) == 1):
            interrupt = False
            raise SimulatedExit()

    with (patch('stage_runner.query', query),
          patch('collector.Collector.run', collect),
          patch('controller.save_checkpoint', save_and_interrupt),
          contextlib.redirect_stdout(io.StringIO())):
        try:
            await controller.run_workflow(path, 'offline-fixture', until_stop=True)
        except SimulatedExit:
            pass
        else:
            raise AssertionError('The fixture did not interrupt the run.')

        saved = RunCheckpoint.model_validate_json(path.read_text(encoding='utf-8'))
        assert len(saved.research.validation_artifacts) == 1
        assert (saved.pending_call is not None) == (scenario == 'interrupted_call')
        if scenario == 'expired':
            saved.started_at -= timedelta(seconds=301)
            save(path, saved)

        start_time = saved.started_at
        before = len(calls)
        result = await controller.run_workflow(path, 'offline-fixture',
                                               until_stop=False, resume=True)

    report = read_json(path.parent / 'run_report.json')
    extra = len(calls) - before
    assert result.started_at == start_time
    assert report['stop_reason'] == ('interrupted_call' if scenario == 'interrupted_call'
                                     else 'execution_limit')
    assert report['cost_accounting_complete'] == (scenario != 'interrupted_call')
    assert calls.count('collect:official_roles') == 1
    assert extra == (2 if scenario == 'committed' else 0)
    assert len(report['claims'][0]['citations']) == (2 if scenario == 'committed' else 1)
    return {
        'scenario': scenario,
        'new_model_calls_after_resume': extra,
        'citations_retained': len(report['claims'][0]['citations']),
        'stop_reason': report['stop_reason'],
        'cost_accounting_complete': report['cost_accounting_complete'],
    }


async def main():
    cases = []
    for scenario in ('committed', 'interrupted_call', 'expired'):
        with tempfile.TemporaryDirectory() as directory:
            cases.append(await exercise(Path(directory), scenario))
    print(json.dumps({'mode': 'offline_fixture', 'cases': cases}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
