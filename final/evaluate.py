"""Grade a merged profile with deterministic checks; no model calls."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse


def norm(text):
    return ' '.join((text or '').split()).casefold()


def snapshot_text(collections, citation):
    collection = collections[citation['evidence']['collection_id']]
    snapshot = json.loads((collection / citation['source_snapshot']).read_text())
    return snapshot['tool_response'].get('result', '')


def citation_validity(profile, collections):
    citations = [c for claim in profile['claims'] for c in claim['citations']]
    valid = [c for c in citations
             if norm(c['role_excerpt']) in norm(snapshot_text(collections, c))]
    return len(valid) / len(citations) if citations else 0.0


def role_matches(claim, gold):
    return (norm(gold['organization']) in norm(claim['role']['organization'])
            and norm(gold['title']) in norm(claim['role']['title']))


def field_accuracy(profile, gold):
    found = [g for g in gold['roles'] if any(role_matches(c, g) for c in profile['claims'])]
    dated = [g for g in gold['roles'] if g['expected_timing']]
    timed = [g for g in dated
             if any(role_matches(c, g) and c['timing'] == g['expected_timing']
                    for c in profile['claims'])]
    return {'role_recall': len(found) / len(gold['roles']),
            'timing_correct': f'{len(timed)}/{len(dated)}'}


def source_quality(profile, gold):
    urls = {u for claim in profile['claims'] for u in claim['distinct_source_urls']}
    primary = [u for u in urls
               if any(urlparse(u).netloc.endswith(d) for d in gold['primary_domains'])]
    return len(primary) / len(urls) if urls else 0.0


def grade(profile, collections, gold, report=None):
    claims = profile['claims']
    return {
        'claims': len(claims),
        'citation_validity': citation_validity(profile, collections),
        'unsupported_claims': [c['role'] for c in claims if not c['citations']],
        'timing_resolved': sum(c['timing'] != 'unknown' for c in claims) / max(len(claims), 1),
        **field_accuracy(profile, gold),
        'primary_source_share': source_quality(profile, gold),
        'stop_reason': report.get('stop_reason') if report else None,
        'estimated_cost_usd': report.get('estimated_cost_usd') if report else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--collection', type=Path, action='append', required=True)
    parser.add_argument('--gold', type=Path, default=Path('gold.json'))
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text())
    collections = {}
    for path in args.collection:
        manifest = json.loads((path / 'manifest.json').read_text())
        collections[manifest['run_id']] = path
    report = json.loads(args.report.read_text()) if args.report else None
    scores = grade(profile, collections, json.loads(args.gold.read_text()), report)
    print(json.dumps(scores, indent=2, default=str))


if __name__ == '__main__':
    main()
