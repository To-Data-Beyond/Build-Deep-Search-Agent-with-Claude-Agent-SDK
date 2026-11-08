"""Create an initial checkpoint for a local research run."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime

from contracts import Contract, ResearchRequest, ResearchState


class PendingCall(Contract):
    stage: Literal['planning', 'collecting', 'validating']
    round_number: int
    assignment_id: str | None = None


class RunCheckpoint(Contract):
    checkpoint_version: Literal['1'] = '1'
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    research: ResearchState
    execution_mode: Literal['first_round', 'until_stop'] | None = None
    pending_call: PendingCall | None = None


def init_run(request_path: Path, runs_dir: Path) -> Path:
    request = ResearchRequest.model_validate_json(
        request_path.read_text(encoding='utf-8')
    )
    run_id = f'research-{uuid4().hex[:12]}'
    checkpoint = RunCheckpoint(
        created_at=datetime.now(timezone.utc),
        research=ResearchState(run_id=run_id, request=request),
    )
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    state_path = run_dir / 'state.json'
    with state_path.open('x', encoding='utf-8') as output:
        output.write(checkpoint.model_dump_json(indent=2) + '\n')
    return state_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--runs-dir', type=Path, default=Path('artifacts'))
    args = parser.parse_args()
    print(init_run(args.request, args.runs_dir))


if __name__ == '__main__':
    main()
