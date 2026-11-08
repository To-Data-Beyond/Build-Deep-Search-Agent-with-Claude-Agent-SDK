"""Run or resume a local research checkpoint."""
import argparse
import asyncio
import fcntl
import math
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from assess_gaps import Profile, assess_gaps, decide_next
from collection_adapter import collect_assignment
from evidence_archive import read_json
from init_run import PendingCall, RunCheckpoint
from merger import merge_profile
from planner import build_plan
from stage_runner import ExecutionLimit, save_json
from validator import validate_collection


def save_checkpoint(path: Path, checkpoint: RunCheckpoint) -> None:
    temp = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        temp.write_text(checkpoint.model_dump_json(indent=2) + '\n', encoding='utf-8')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def available_for_call(state, deadline):
    limits = state.request.limits
    seconds = deadline - time.monotonic()
    cost = limits.max_estimated_cost_usd - state.estimated_cost_usd
    if seconds <= 0 or cost <= 0:
        raise ExecutionLimit('The run deadline or estimated spending threshold was reached.')
    if not state.cost_accounting_complete:
        raise ExecutionLimit('Previous call cost is unknown; stop before another call.')
    return seconds, cost


def record_collection_cost(state, cost):
    if (isinstance(cost, (int, float)) and not isinstance(cost, bool)
            and math.isfinite(cost) and cost >= 0):
        state.estimated_cost_usd += cost
    else:
        state.cost_accounting_complete = False


def validated_inputs(state):
    collections = {ref.collection_id: Path(ref.artifact_directory)
                   for ref in state.collections if ref.status == 'collected'}
    return [(collections[read_json(Path(path))['collection_id']], Path(path))
            for path in state.validation_artifacts]


def mark_pending(path, checkpoint, stage, round_number, assignment_id=None):
    if checkpoint.pending_call is not None:
        raise RuntimeError('Cannot dispatch while another call is pending.')
    checkpoint.pending_call = PendingCall(stage=stage, round_number=round_number,
                                         assignment_id=assignment_id)
    save_checkpoint(path, checkpoint)


def commit_call(path, checkpoint):
    checkpoint.pending_call = None
    save_checkpoint(path, checkpoint)


async def execute_round(state_path, checkpoint, model, deadline, round_dir, gaps):
    state, request = checkpoint.research, checkpoint.research.request
    if state.phase in ('created', 'assessing'):
        plan_dir = round_dir / 'planning'
        plan_dir.mkdir(exist_ok=True)
        available_for_call(state, deadline)
        mark_pending(state_path, checkpoint, 'planning', state.round_index + 1)
        await build_plan(state, plan_dir, model, gaps=gaps, deadline=deadline)
        commit_call(state_path, checkpoint)

    committed = {(ref.round_index, ref.assignment_id): ref for ref in state.collections}
    for assignment in state.plan:
        key = (state.round_index, assignment.id)
        reference = committed.get(key)
        collection_dir = round_dir / f'r{state.round_index:02d}-{assignment.id}'
        if reference is None:
            state.phase = 'collecting'
            seconds, cost = available_for_call(state, deadline)
            state.assignments_started += 1
            mark_pending(state_path, checkpoint, 'collecting', *key)
            try:
                reference, manifest = await collect_assignment(
                    request, assignment, state.round_index, collection_dir, model,
                    timeout_seconds=min(240, seconds), max_budget_usd=min(2.0, cost),
                )
            except Exception:
                state.cost_accounting_complete = False
                raise
            state.collections.append(reference)
            record_collection_cost(state, manifest.get('estimated_cost_usd'))
            commit_call(state_path, checkpoint)
        else:
            print(f'REUSE_COLLECTION={reference.artifact_directory}')
            manifest = read_json(collection_dir / 'manifest.json')

        if reference.status == 'no_evidence':
            continue
        if reference.status == 'incomplete':
            if (time.monotonic() >= deadline
                    or (manifest.get('error') or '').startswith('TimeoutError:')
                    or manifest.get('result_subtype') in ('error_max_turns', 'error_max_budget_usd')):
                raise ExecutionLimit(f'Collector reached a limit: {assignment.id}')
            raise RuntimeError(f'Collection incomplete: {assignment.id}; inspect its manifest.')

        validation_dir = round_dir / 'validation' / collection_dir.name
        decisions_path = validation_dir / 'validation.json'
        if str(decisions_path) in state.validation_artifacts:
            print(f'REUSE_VALIDATION={decisions_path}')
            continue
        state.phase = 'validating'
        available_for_call(state, deadline)
        validation_dir.mkdir(parents=True, exist_ok=True)
        mark_pending(state_path, checkpoint, 'validating', *key)
        await validate_collection(
            state, collection_dir, validation_dir, model, deadline=deadline,
        )
        state.validation_artifacts.append(str(decisions_path))
        commit_call(state_path, checkpoint)

    state.phase = 'merging'
    save_checkpoint(state_path, checkpoint)
    merged = merge_profile(request, validated_inputs(state))
    assessment = assess_gaps(request, Profile.model_validate(merged))
    merged_path = round_dir / 'merged_profile.json'
    gaps_path = round_dir / 'gap_assessment.json'
    save_json(merged_path, merged)
    save_json(gaps_path, assessment)
    state.merged_profile_artifact = str(merged_path)
    state.gap_assessment_artifact = str(gaps_path)
    state.phase = 'assessing'
    save_checkpoint(state_path, checkpoint)
    return merged, assessment


def write_run_report(run_dir, state, profile, assessment, *, detail=None, warning=None):
    save_json(run_dir / 'run_report.json', {
        'run_id': state.run_id,
        'subject_name': state.request.subject_name,
        'reference_date': state.request.reference_date.isoformat(),
        'stop_reason': state.stop_reason,
        'stop_detail': detail,
        'round_index': state.round_index,
        'assignments_started': state.assignments_started,
        'estimated_cost_usd': state.estimated_cost_usd,
        'cost_accounting_complete': state.cost_accounting_complete,
        'claims': profile['claims'] if profile is not None else [],
        'open_gaps': assessment['gaps'] if assessment is not None else None,
        'review_queue': assessment['review_queue'] if assessment is not None else None,
        'warning': warning,
    })


@contextmanager
def exclusive_run(run_dir):
    # Keep one stable lock file; the OS releases the lock when the process exits.
    with (run_dir / '.controller.lock').open('a', encoding='utf-8') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Another controller is using this run directory.') from error
        yield


def recover_results(state_path, checkpoint, profile=None, assessment=None):
    state, warning = checkpoint.research, None
    if state.validation_artifacts:
        try:
            profile_candidate = merge_profile(state.request, validated_inputs(state))
            assessment_candidate = assess_gaps(
                state.request, Profile.model_validate(profile_candidate),
            )
            merged_path = state_path.parent / 'partial_profile.json'
            gaps_path = state_path.parent / 'partial_gap_assessment.json'
            save_json(merged_path, profile_candidate)
            save_json(gaps_path, assessment_candidate)
            profile, assessment = profile_candidate, assessment_candidate
            state.merged_profile_artifact = str(merged_path)
            state.gap_assessment_artifact = str(gaps_path)
        except Exception as error:
            warning = f'Could not rebuild from saved validations: {type(error).__name__}.'
    return profile, assessment, warning


def finish(state_path, checkpoint, reason, profile, assessment, *, detail=None, warning=None):
    state = checkpoint.research
    state.phase, state.stop_reason = 'stopped', reason
    write_run_report(state_path.parent, state, profile, assessment, detail=detail, warning=warning)
    save_checkpoint(state_path, checkpoint)
    return checkpoint


async def run_workflow(
    state_path: Path, model: str, *, until_stop: bool, resume: bool = False,
) -> RunCheckpoint:
    state_path = state_path.resolve()
    with exclusive_run(state_path.parent):
        return await run_locked(state_path, model, until_stop=until_stop, resume=resume)


async def run_locked(state_path, model, *, until_stop, resume):
    checkpoint = RunCheckpoint.model_validate_json(state_path.read_text(encoding='utf-8'))
    state, run_dir = checkpoint.research, state_path.parent
    if resume:
        if checkpoint.started_at is None or checkpoint.execution_mode is None:
            raise ValueError('Resume requires a run started by this version of the controller.')
        until_stop = checkpoint.execution_mode == 'until_stop'
        if state.phase == 'stopped':
            return checkpoint
        if checkpoint.pending_call is not None:
            pending = checkpoint.pending_call
            state.cost_accounting_complete = False
            profile, assessment, warning = recover_results(state_path, checkpoint)
            return finish(state_path, checkpoint, 'interrupted_call', profile, assessment,
                          detail=f'Uncommitted {pending.stage} stage in round {pending.round_number}.',
                          warning=warning)
    else:
        if (state.phase != 'created' or state.round_index != 0
                or checkpoint.started_at is not None or (run_dir / 'round-01').exists()):
            raise ValueError('Pass --resume for a run started by this controller.')
        checkpoint.started_at = datetime.now(timezone.utc)
        checkpoint.execution_mode = 'until_stop' if until_stop else 'first_round'
        save_checkpoint(state_path, checkpoint)

    elapsed = (datetime.now(timezone.utc) - checkpoint.started_at).total_seconds()
    if elapsed < 0:
        raise ValueError('The current clock is earlier than the saved run start.')
    started = time.monotonic() - elapsed
    deadline = started + state.request.limits.max_elapsed_seconds
    profile, assessment = None, None
    try:
        while True:
            gaps = []
            if state.phase == 'assessing':
                profile = read_json(Path(state.merged_profile_artifact))
                assessment = read_json(Path(state.gap_assessment_artifact))
                if not until_stop:
                    return checkpoint
                previous_ids = None
                if state.round_index > 1:
                    previous = read_json(run_dir / f'round-{state.round_index - 1:02d}'
                                         / 'gap_assessment.json')
                    previous_ids = {gap['id'] for gap in previous['gaps']}
                decision = decide_next(
                    state, assessment, elapsed_seconds=time.monotonic() - started,
                    previous_gap_ids=previous_ids,
                )
                save_json(run_dir / f'round-{state.round_index:02d}' / 'controller_decision.json',
                          decision)
                if decision['action'] == 'stop':
                    return finish(state_path, checkpoint, decision['stop_reason'],
                                  profile, assessment)
                number, gaps = state.round_index + 1, decision['planner_gaps']
            elif state.phase == 'created':
                number = 1
            elif state.phase in ('collecting', 'validating', 'merging'):
                number = state.round_index
            else:
                raise ValueError(f'Cannot resume from phase {state.phase!r}.')

            round_dir = run_dir / f'round-{number:02d}'
            round_dir.mkdir(exist_ok=True)
            profile, assessment = await execute_round(
                state_path, checkpoint, model, deadline, round_dir, gaps,
            )
    except Exception as error:
        reason = 'execution_limit' if isinstance(error, ExecutionLimit) else 'failure'
        checkpoint.pending_call = None
        save_json(run_dir / 'error.json', {'type': type(error).__name__, 'message': str(error)})
        if not until_stop:
            state.phase, state.stop_reason = 'stopped', reason
            save_checkpoint(state_path, checkpoint)
            raise
        profile, assessment, warning = recover_results(state_path, checkpoint, profile, assessment)
        return finish(state_path, checkpoint, reason, profile, assessment,
                      detail=str(error), warning=warning)


async def run_first_round(state_path: Path, model: str) -> RunCheckpoint:
    return await run_workflow(state_path, model, until_stop=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--until-stop', action='store_true',
                        help='Continue through approved follow-ups and write a run report.')
    parser.add_argument('--resume', action='store_true',
                        help='Continue from the last committed checkpoint.')
    args = parser.parse_args()
    if args.resume and args.until_stop:
        parser.error('--resume uses the execution mode saved in the checkpoint.')
    load_dotenv(Path(__file__).with_name('.env'), override=False)
    if os.getenv('ANTHROPIC_API_KEY', '').strip() in ('', 'replace_with_your_api_key'):
        parser.error('Set ANTHROPIC_API_KEY in the environment or .env before running.')
    try:
        checkpoint = asyncio.run(run_workflow(
            args.state, os.getenv('CLAUDE_MODEL', 'sonnet'), until_stop=args.until_stop, resume=args.resume,
        ))
    except Exception as error:
        parser.exit(1, f'{type(error).__name__}: {error}\n')
    print(f'PHASE={checkpoint.research.phase}')
    print(f'MERGED_PROFILE={checkpoint.research.merged_profile_artifact}')
    print(f'GAP_ASSESSMENT={checkpoint.research.gap_assessment_artifact}')
    if checkpoint.research.stop_reason is not None:
        print(f'STOP_REASON={checkpoint.research.stop_reason}')
    report = args.state.resolve().parent / 'run_report.json'
    if report.is_file():
        print(f'RUN_REPORT={report}')
    return 1 if checkpoint.research.stop_reason in ('failure', 'interrupted_call') else 0


if __name__ == '__main__':
    raise SystemExit(main())
