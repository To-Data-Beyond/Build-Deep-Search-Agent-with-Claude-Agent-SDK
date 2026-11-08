from collections import Counter

from planning_contracts import ResearchPlan


SOURCE_TYPES = {
    'personal_website', 'official_company_page', 'university_profile',
    'publication_database', 'conference_page', 'news_interview', 'other',
}


def check_plan(plan: ResearchPlan, assignment_limit: int) -> list[str]:
    issues = []
    if len(plan.assignments) > assignment_limit:
        issues.append(f'Use at most {assignment_limit} assignments.')
    counts = Counter(item.id for item in plan.assignments)
    for key, count in counts.items():
        if count > 1:
            issues.append(f'Duplicate assignment ID: {key}.')
    questions = [
        ''.join(c for c in item.question.casefold() if c.isalnum())
        for item in plan.assignments
    ]
    if len(set(questions)) != len(questions):
        issues.append('Repeated question text; consolidate duplicate assignments.')
    for item in plan.assignments:
        if any(not value.strip() for value in item.evidence_requirements):
            issues.append(f'{item.id}: evidence requirements cannot be blank.')
        unknown = set(item.preferred_source_types) - SOURCE_TYPES
        if unknown:
            issues.append(f'{item.id}: unsupported source types {sorted(unknown)}.')
    return issues
