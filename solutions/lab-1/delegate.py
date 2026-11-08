"""Lead agent that delegates each evidence record to an examiner subagent."""
import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from claude_agent_sdk import (
    AgentDefinition, AssistantMessage, ClaudeAgentOptions, ResultMessage,
    ToolUseBlock, query,
)

ROOT = Path(__file__).resolve().parent

EXAMINER = AgentDefinition(
    description='',  # TODO 5: say when the lead should use this subagent
    prompt=(
        'You examine one evidence record. Read the snapshot file named in its '
        'source_snapshot field. Check that the quote occurs in tool_response.result, '
        'that the passage states the role, and whether any date places the role in time. '
        'Reply with one JSON object: {"evidence_id": str, "quote_found": bool, '
        '"role_supported": bool, "dated_excerpt": str | null}. '
        'Use null when the snapshot gives no date. Never use outside knowledge.'
    ),
    tools=[],  # TODO 5: only the tools it needs to read snapshot files
    model='inherit',
    background=False,
)

LEAD_PROMPT = (
    'You lead the review of one evidence collection in the working directory. '
    'Read evidence.jsonl. For EACH record, delegate to the examiner subagent and pass '
    'the full record JSON. Do not open source snapshots yourself. '
    'Finish with a Markdown table: evidence_id, role, quote_found, dated_excerpt.'
)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection', type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(ROOT / '.env', override=False)
    options = ClaudeAgentOptions(
        model=os.getenv('CLAUDE_MODEL', 'sonnet'),
        system_prompt=LEAD_PROMPT,
        # TODO 6: register EXAMINER with agents={...} and add the delegation tool 'Agent'
        tools=['Read', 'Grep'],
        allowed_tools=['Read', 'Grep'],
        setting_sources=[], permission_mode='dontAsk',
        max_turns=20, max_budget_usd=0.50,
        cwd=args.collection.resolve(),
    )
    delegations, results, started = 0, [], time.monotonic()
    async for message in query(prompt='Review every record in evidence.jsonl.', options=options):
        if isinstance(message, AssistantMessage) and message.parent_tool_use_id is None:
            for block in message.content:
                if isinstance(block, ToolUseBlock) and block.name == 'Agent':
                    delegations += 1
                    print(f'[delegate] {block.input.get("description", "")}', flush=True)
        elif isinstance(message, ResultMessage):
            results.append(message)
    result = results[-1] if results else None
    print(result.result if result else 'No result.')
    print(json.dumps({
        'delegations': delegations,
        'num_turns': result.num_turns if result else None,
        'elapsed_seconds': round(time.monotonic() - started, 1),
        'estimated_cost_usd': result.total_cost_usd if result else None,
        'subtype': result.subtype if result else None,
    }, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
