import asyncio
import json
import os
import platform
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    query,
)
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)


async def probe(report):
    workspace = ROOT / "workspace"
    workspace.mkdir(exist_ok=True)
    options = ClaudeAgentOptions(
        model=report["model_requested"],
        system_prompt="Return exactly SDK_CONNECTION_OK. Use no tools.",
        tools=[],
        mcp_servers={},
        strict_mcp_config=True,
        setting_sources=[],
        permission_mode="dontAsk",
        max_turns=1,
        max_budget_usd=0.10,
        cwd=workspace,
    )
    final = None
    async for message in query(
        prompt="Run the connectivity check.",
        options=options,
    ):
        if isinstance(message, SystemMessage) and message.subtype == "init":
            report["model_resolved"] = message.data.get("model")
        elif isinstance(message, ResultMessage):
            final = message
            report.update(
                result_subtype=message.subtype,
                response=message.result,
                estimated_cost_usd=message.total_cost_usd,
                session_id=message.session_id,
            )
    if final is None or final.is_error or final.subtype != "success":
        raise RuntimeError("The SDK did not return a successful result.")
    if (final.result or "").strip() != "SDK_CONNECTION_OK":
        raise RuntimeError("The response did not match the probe marker.")

async def main():
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {
            package: version(package)
            for package in (
                "claude-agent-sdk",
                "python-dotenv",
                "pydantic",
            )
        },
        "model_requested": os.getenv("CLAUDE_MODEL", "sonnet"),
        "status": "error",
    }
    try:
        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not key or key == "replace_with_your_api_key":
            raise RuntimeError(
                "Set ANTHROPIC_API_KEY in .env or the environment."
            )
        await asyncio.wait_for(probe(report), timeout=90)
        report["status"] = "ok"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    output = ROOT / "artifacts" / "setup_check.json"
    output.parent.mkdir(exist_ok=True)
    text = json.dumps(report, indent=2)
    output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["status"] == "ok" else 1

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))