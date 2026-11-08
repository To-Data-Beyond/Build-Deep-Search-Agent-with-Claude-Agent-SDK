import asyncio
import json
import math
import time
from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions, HookMatcher, ResultMessage, SystemMessage, query,
)


class ExecutionLimit(RuntimeError):
    pass


def save_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + '\n', encoding='utf-8')


async def output_only(data, tool_use_id, context):
    if data['tool_name'] == 'StructuredOutput':
        return {}
    return {'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': 'Planning permits structured output only.',
    }}


async def run_json_stage(state, directory, label, model, output_type,
                         system_prompt, payload, deadline):
    remaining_time = deadline - time.monotonic()
    remaining_cost = (state.request.limits.max_estimated_cost_usd
                      - state.estimated_cost_usd)
    if remaining_time <= 0 or remaining_cost <= 0:
        raise ExecutionLimit('Planning deadline or estimated spending threshold reached.')
    if not state.cost_accounting_complete:
        raise RuntimeError('A previous call has unknown cost; stop before further calls.')
    workspace = directory / f'{label}-workspace'
    workspace.mkdir()
    save_json(directory / f'{label}.input.json', {
        'system_prompt': system_prompt, 'payload': payload,
    })
    options = ClaudeAgentOptions(
        model=model, tools=[], allowed_tools=['StructuredOutput'],
        mcp_servers={}, strict_mcp_config=True,
        setting_sources=[], permission_mode='dontAsk',
        max_turns=5, max_budget_usd=min(0.25, remaining_cost),
        output_format={'type': 'json_schema', 'schema': output_type.model_json_schema()},
        hooks={'PreToolUse': [HookMatcher(hooks=[output_only])]},
        system_prompt=system_prompt, cwd=workspace,
    )
    result, resolved_model, failure = None, None, None
    timed_out = False
    started = time.monotonic()

    async def receive():
        nonlocal result, resolved_model
        async for message in query(prompt=json.dumps(payload), options=options):
            if isinstance(message, SystemMessage) and message.subtype == 'init':
                resolved_model = message.data.get('model')
            elif isinstance(message, ResultMessage):
                result = message

    try:
        await asyncio.wait_for(receive(), timeout=max(0.001, deadline - time.monotonic()))
    except asyncio.TimeoutError as error:
        timed_out = True
        failure = f'TimeoutError: planning deadline reached.'
    except Exception as error:
        failure = f'{type(error).__name__}: {error}'
    cost = result.total_cost_usd if result else None
    known_cost = (isinstance(cost, (int, float)) and not isinstance(cost, bool)
                  and math.isfinite(cost) and cost >= 0)
    if known_cost:
        state.estimated_cost_usd += cost
    else:
        state.cost_accounting_complete = False
    save_json(directory / f'{label}.call.json', {
        'model_requested': model, 'model_resolved': resolved_model,
        'elapsed_seconds': round(time.monotonic() - started, 3),
        'num_turns': result.num_turns if result else None,
        'duration_ms': result.duration_ms if result else None,
        'result_subtype': result.subtype if result else None,
        'is_error': result.is_error if result else None,
        'stop_reason': result.stop_reason if result else None,
        'terminal_reason': result.terminal_reason if result else None,
        'session_id': result.session_id if result else None,
        'estimated_cost_usd': cost, 'cost_known': known_cost,
        'usage': result.usage if result else None,
        'structured_output': result.structured_output if result else None,
        'sdk_errors': result.errors if result else None,
        'error': failure,
    })
    print(f'[{label}] subtype={result.subtype if result else "none"} cost_usd={cost}',
          flush=True)
    if failure:
        if timed_out:
            raise ExecutionLimit(f'{label}: {failure}')
        raise RuntimeError(f'{label}: {failure}')
    if result is not None and result.subtype in ('error_max_turns', 'error_max_budget_usd'):
        raise ExecutionLimit(f'{label}: SDK limit reached ({result.subtype}).')
    if (result is None or result.subtype != 'success' or result.is_error
            or result.stop_reason == 'refusal'
            or result.terminal_reason in ('api_error', 'refusal')
            or result.structured_output is None):
        raise RuntimeError(f'{label}: no successful structured result.')
    if not known_cost:
        raise RuntimeError(f'{label}: cost is unknown; stop before further calls.')
    if time.monotonic() >= deadline or remaining_cost <= cost:
        raise ExecutionLimit(f'{label}: deadline or estimated spending threshold reached.')
    value = output_type.model_validate(result.structured_output)
    save_json(directory / f'{label}.json', value.model_dump(mode='json'))
    return value
