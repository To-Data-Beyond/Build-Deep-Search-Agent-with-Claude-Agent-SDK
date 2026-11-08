import json
from datetime import datetime

from collector import ResearchTask, url_key
from validation_contracts import SavedEvidence


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def contains_excerpt(text, excerpt):
    normalized = ' '.join((excerpt or '').split())
    return bool(normalized) and normalized in ' '.join(text.split())


def load_collection(directory, request):
    manifest = read_json(directory / 'manifest.json')
    task = ResearchTask.model_validate(read_json(directory / 'task.json'))
    if manifest['status'] != 'collected':
        raise ValueError('This example expects a completed collection with evidence.')
    if task.subject_name.casefold() != request.subject_name.casefold():
        raise ValueError('Collection subject does not match the request.')
    if set(map(url_key, task.identity_anchor_urls)) != set(
        map(url_key, request.identity_anchor_urls)
    ):
        raise ValueError('Collection identity anchors do not match the request.')

    rows = [
        SavedEvidence.model_validate_json(line)
        for line in (directory / 'evidence.jsonl').read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    ids = [row.id for row in rows]
    if (not ids or len(ids) != len(set(ids))
            or ids != manifest['evidence_ids']
            or len(ids) != manifest['evidence_count']):
        raise ValueError('Evidence IDs or count do not match the collection manifest.')
    if any(row.sub_question_id != task.sub_question_id for row in rows):
        raise ValueError('Evidence belongs to a different subquestion.')

    sources = {source['id']: source for source in manifest['sources']}
    if len(sources) != len(manifest['sources']):
        raise ValueError('Duplicate source IDs in the collection manifest.')
    return manifest['run_id'], rows, sources


def load_snapshot(directory, row, sources):
    source = sources.get(row.source_id)
    requested = url_key(row.source_url)
    if source is None or url_key(source['requested_url']) != requested:
        raise ValueError('Evidence source ID does not match the requested URL.')

    # Read the version cited by the record, not the latest snapshot for the URL.
    root = directory.resolve()
    path = (root / row.source_snapshot).resolve()
    if not path.is_relative_to(root / 'sources') or path.suffix != '.json':
        raise ValueError('Snapshot reference must point inside the sources directory.')
    snapshot = read_json(path)
    if url_key(snapshot['requested_url']) != requested:
        raise ValueError('Snapshot requested URL does not match the evidence.')

    response = snapshot['tool_response']
    if (not isinstance(response, dict) or type(response.get('code')) is not int
            or not 200 <= response['code'] < 300
            or not isinstance(response.get('result'), str)):
        raise ValueError('Snapshot does not contain a successful retrieval response.')

    resolved = url_key(response.get('url') or requested)
    if resolved != url_key(row.resolved_source_url or requested):
        raise ValueError('Snapshot resolved URL does not match the evidence.')
    if datetime.fromisoformat(snapshot['retrieved_at']).date() != row.retrieval_date:
        raise ValueError('Snapshot retrieval date does not match the evidence.')
    if not contains_excerpt(response['result'], row.quote):
        raise ValueError('Recorded quotation is absent from the saved response.')

    return {
        'requested_url': requested,
        'resolved_url': resolved,
        'retrieved_at': snapshot['retrieved_at'],
        'text': response['result'],
    }
