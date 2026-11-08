import json
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from contracts import Assignment, ResearchRequest, ResearchState


ROOT = Path(__file__).resolve().parent


def main():
    request = ResearchRequest.model_validate_json(
        (ROOT / 'research_request.json').read_text(encoding='utf-8')
    )
    state = ResearchState(run_id=f'research-{uuid4().hex[:12]}', request=request)
    directory = ROOT / 'artifacts' / state.run_id
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'state.json').write_text(
        state.model_dump_json(indent=2) + '\n', encoding='utf-8'
    )

    invalid_assignment = {
        'id': 'roles',
        'question': 'Which professional roles are supported by primary sources?',
        'evidence_requirements': [],
        'preferred_source_types': ['personal_website', 'official_company_page'],
    }
    try:
        Assignment.model_validate(invalid_assignment)
    except ValidationError as error:
        rejected = {
            'field': '.'.join(str(part) for part in error.errors()[0]['loc']),
            'error_type': error.errors()[0]['type'],
        }
    else:
        raise RuntimeError('The intentionally invalid assignment was accepted.')

    print(json.dumps({
        'phase': state.phase,
        'round_index': state.round_index,
        'subject_name': request.subject_name,
        'reference_date': request.reference_date.isoformat(),
        'limits': request.limits.model_dump(),
        'invalid_assignment_rejected': rejected,
    }, indent=2))
    print(f'STATE_FILE={directory / "state.json"}')


if __name__ == '__main__':
    main()
