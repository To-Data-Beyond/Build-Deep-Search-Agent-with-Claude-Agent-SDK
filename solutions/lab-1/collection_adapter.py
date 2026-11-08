from pathlib import Path

from contracts import Assignment, CollectionRef, ResearchRequest


def assignment_to_task(request: ResearchRequest, assignment: Assignment):
    from collector import ResearchTask

    requirements = '\n'.join(
        f'- {item}' for item in assignment.evidence_requirements
    )
    return ResearchTask(
        subject_name=request.subject_name,
        identity_anchor_urls=request.identity_anchor_urls,
        sub_question_id=assignment.id,
        question=(
            f'Research scope: {request.scope}\n'
            f'Reference date: {request.reference_date.isoformat()}\n'
            f'Question: {assignment.question}\n'
            f'Evidence requirements:\n{requirements}'
        ),
        preferred_source_types=assignment.preferred_source_types,
        max_sources=request.limits.max_sources_per_assignment,
        max_evidence_records=request.limits.max_records_per_assignment,
    )


async def collect_assignment(
    request: ResearchRequest,
    assignment: Assignment,
    round_index: int,
    directory: Path,
    model: str,
    *,
    timeout_seconds: float = 240,
    max_budget_usd: float = 2.0,
) -> tuple[CollectionRef, dict]:
    from collector import run_collection

    task = assignment_to_task(request, assignment)
    manifest = await run_collection(
        task, directory, model, timeout_seconds=timeout_seconds,
        max_budget_usd=max_budget_usd,
    )
    reference = CollectionRef(
        collection_id=manifest['run_id'],
        assignment_id=assignment.id,
        round_index=round_index,
        artifact_directory=str(directory),
        status=manifest['status'],
        evidence_ids=manifest['evidence_ids'],
    )
    return reference, manifest
