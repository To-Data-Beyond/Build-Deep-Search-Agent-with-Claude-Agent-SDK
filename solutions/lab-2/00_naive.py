"""Baseline: ask the research question in one model call with no tools.

The answer comes only from the model's training data. Compare it with the
agent's run report: it has no sources, no retrieval dates, and no way to
separate current roles from historical ones as of the reference date.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from dotenv import load_dotenv

from contracts import ResearchRequest

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env', override=False)


async def main():
    request = ResearchRequest.model_validate_json(
        (ROOT / 'research_request.json').read_text(encoding='utf-8'))
    question = (f'List the professional roles of {request.subject_name}. '
                f'Mark each role as current or historical as of '
                f'{request.reference_date.isoformat()}, and give a source for each.')
    workspace = ROOT / 'workspace'
    workspace.mkdir(exist_ok=True)
    options = ClaudeAgentOptions(
        model=os.getenv('CLAUDE_MODEL', 'sonnet'),
        tools=[], mcp_servers={}, strict_mcp_config=True,
        setting_sources=[], permission_mode='dontAsk',
        max_turns=1, max_budget_usd=0.10, cwd=workspace,
    )
    result = None
    async for message in query(prompt=question, options=options):
        if isinstance(message, ResultMessage):
            result = message
    report = {
        'started_at_utc': datetime.now(timezone.utc).isoformat(),
        'question': question,
        'answer': result.result if result else None,
        'num_turns': result.num_turns if result else None,
        'duration_ms': result.duration_ms if result else None,
        'estimated_cost_usd': result.total_cost_usd if result else None,
        'tools_available': [],
    }
    out = ROOT / 'artifacts' / 'naive_baseline.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(report['answer'])
    print(json.dumps({k: v for k, v in report.items() if k != 'answer'}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
