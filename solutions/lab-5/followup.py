"""Continue a research conversation with resume, then branch it with fork_session."""
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

ROOT = Path(__file__).resolve().parent


def options(**session):
    return ClaudeAgentOptions(
        model=os.getenv('CLAUDE_MODEL', 'sonnet'),
        system_prompt='You plan follow-up research. Answer briefly and concretely.',
        tools=[], mcp_servers={}, strict_mcp_config=True,
        setting_sources=[], permission_mode='dontAsk',
        max_turns=1, max_budget_usd=0.10, cwd=ROOT / 'workspace',
        **session,
    )


async def ask(prompt, **session):
    result = None
    async for message in query(prompt=prompt, options=options(**session)):
        if isinstance(message, ResultMessage):
            result = message
    print(f'\n[{result.session_id}] turns={result.num_turns} '
          f'cost_usd={result.total_cost_usd:.5f}\n{result.result}', flush=True)
    return result.session_id


async def main():
    load_dotenv(ROOT / '.env', override=False)
    (ROOT / 'workspace').mkdir(exist_ok=True)
    profile = json.loads((ROOT / 'examples' / 'merged_profile.json').read_text())
    roles = [f"{c['role']['title']} at {c['role']['organization']} (timing: {c['timing']})"
             for c in profile['claims']]
    briefing = ('Merged profile for Andrew Ng, reference date 2026-10-31:\n- '
                + '\n- '.join(roles)
                + '\nWhich roles still need dated evidence? Reply as a numbered list.')
    first = await ask(briefing)
    # Same session: the model still sees the briefing and its own list.
    resumed = await ask('For item 1, write the first web search query you would run.',
                        resume=first)
    # New branch from the same history: try another strategy without touching `first`.
    forked = await ask('Instead, rank the roles by how likely an official dated page exists.',
                       resume=first, fork_session=True)
    print(json.dumps({'first': first, 'resumed': resumed, 'forked': forked}, indent=2))
    ok = resumed == first and forked != first
    print('PASS: resumed the same session and forked a new one.' if ok
          else 'Not yet: resumed should equal first, and forked should differ.')


if __name__ == '__main__':
    asyncio.run(main())
