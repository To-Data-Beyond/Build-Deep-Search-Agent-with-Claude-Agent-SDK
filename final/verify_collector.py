"""Controlled local checks. No model calls or live web retrieval."""
import asyncio
import tempfile
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool
from collector import Collector, EvidenceInput, ResearchTask, RECORD_TOOL


async def main():
    task = ResearchTask(
        subject_name='Fixture person', identity_anchor_urls=['https://example.org/bio'],
        sub_question_id='roles', question='Find professional role evidence.',
        preferred_source_types=['personal_website'], max_sources=3, max_evidence_records=2,
    )
    with tempfile.TemporaryDirectory() as temporary:
        collector = Collector(task, Path(temporary) / 'run')
        item = dict(claim_type='current_role', claim='Fixture claim.',
                    source_url='https://example.org/bio', source_type='personal_website',
                    title='Controlled fixture', quote='Public biography excerpt.',
                    identity_signals=['Name in controlled fixture'])
        assert (await collector.record_evidence(item))['isError']
        for i, url in enumerate(('https://example.org/bio', 'https://example.org/other', 'https://example.org/fail')):
            assert not await collector.before_tool({'tool_name':'WebFetch','tool_input':{'url':url}}, str(i), None)
        denied = await collector.before_tool({'tool_name':'WebFetch','tool_input':{'url':'https://example.org/fourth'}}, '4', None)
        assert denied['hookSpecificOutput']['permissionDecision'] == 'deny'
        denied = await collector.before_tool({'tool_name':'Bash','tool_input':{}}, 'b', None)
        assert denied['hookSpecificOutput']['permissionDecision'] == 'deny'
        await collector.after_tool({'tool_name':'WebFetch','tool_input':{'url':str(item['source_url'])},
            'tool_response':{'code':200,'result':'Public biography excerpt.','url':str(item['source_url'])}}, '1', None)
        await collector.after_tool({'tool_name':'WebFetch','tool_input':{'url':'https://example.org/fail'},
            'tool_response':{'code':403,'result':'Forbidden'}}, 'f', None)
        assert 'https://example.org/fail' not in collector.sources
        assert (await collector.record_evidence({**item,'quote':'Invented quotation.'}))['isError']
        assert not (await collector.record_evidence(item))['isError']
        assert not (await collector.record_evidence(item))['isError']
        assert len(collector.records) == 1
        responses = await asyncio.gather(*(collector.record_evidence({**item,'claim':f'Claim {i}'}) for i in range(4)))
        assert len(collector.records) == 2
        assert sum(not response['isError'] for response in responses) == 1
        record = tool('record_evidence', 'Record evidence.', EvidenceInput.model_json_schema())(collector.record_evidence)
        server = create_sdk_mcp_server(name='research', tools=[record])
        assert server['type'] == 'sdk'
        print('PASS: missing retrieval, source cap, tool gate, failed fetch, quote check, duplicate handling, concurrent record cap, MCP registration.')


if __name__ == '__main__':
    asyncio.run(main())
