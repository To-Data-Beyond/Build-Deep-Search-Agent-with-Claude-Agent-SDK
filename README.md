# Build a Deep-Search Agent with the Claude Agent SDK

Workshop code for the live session on 8 November 2026 (To Data & Beyond, Youssef Hosni).

The project researches one case all evening: **which professional roles Andrew Ng holds, and which are historical, as of 31 October 2026**. A Python controller plans the research, runs an evidence collector built on the Claude Agent SDK, validates and merges the evidence, assesses what is still missing and decides whether to run another round. The labs add the pieces one at a time.

## Folders

| Folder | What it holds |
|---|---|
| `starter/` | The project with `TODO` markers for labs 1–5. Work here. |
| `solutions/lab-1` … `solutions/lab-5` | The full project after each lab. Copy a file from here if you fall behind. `lab-5` equals `final/`. |
| `final/` | The completed project. |
| `*/examples/` | Saved artifacts from the 31 Oct live runs: one collection (`collection-20261031`) and its validated, merged profile. Labs 1, 2 and 5 run offline against them. |

## Setup

Requires Python 3.10 or later (tested with 3.12) and an Anthropic API key.

```bash
git clone https://github.com/To-Data-Beyond/Build-Deep-Search-Agent-with-Claude-Agent-SDK.git
cd Build-Deep-Search-Agent-with-Claude-Agent-SDK
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
cp .env.example starter/.env         # then put your key in starter/.env
cd starter
python -u check_setup.py             # expect "status": "ok" and "SDK_CONNECTION_OK"
```

Each script reads `.env` from its own folder. If you switch to a `solutions/lab-N` folder, copy your `.env` there too.

`.env` holds two values:

```
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-haiku-5-5        # or sonnet
```

## Labs

| Lab | Chapter | Edit | TODOs | Done when | API calls |
|---|---|---|---|---|---|
| 1 | Collect evidence | `collector.py` | 1, 2 | `python verify_collector.py` prints `PASS: ...` | none |
| 2 | Plan and check | `assess_gaps.py` | 3a, 3b, 4 | `python verify_gap_assessment.py` prints `OK`; `python assess_gaps.py --request research_request.json --profile examples/merged_profile.json --out gaps.json` reports 6 gaps, Baidu first | none |
| 3 | Subagents | `delegate.py` | 5, 6 | `python -u delegate.py --collection examples/collection-20261031` shows 6 delegations | about $0.01 |
| 4 | Sessions | `followup.py` | 7, 8 | `python -u followup.py` prints `PASS: resumed the same session and forked a new one.` | under $0.01 |
| 5 | Evaluation | `evaluate.py` | 9, 10 | `python evaluate.py --profile examples/merged_profile.json --collection examples/collection-20261031` prints `citation_validity` 1.0, `role_recall` 0.857, `timing_correct` "0/2" | none |

`assess_gaps.py` refuses to overwrite its output file. Delete `gaps.json` before running it again.

## Running the full agent

These commands make paid model calls and fetch live web pages.

```bash
python -u 00_naive.py                                             # the plain-chatbot baseline
python -u collector.py --task research_task.json                  # one collection
STATE=$(python init_run.py --request research_request.json | grep -oE 'artifacts/[^ ]*state.json')
python -u controller.py --state "$STATE" --until-stop             # plan → collect → validate → merge → assess, until a stop rule
python -u controller.py --state "$STATE" --resume                 # continue from the last committed checkpoint
```

Each run writes to `artifacts/`. Read `manifest.json` (collector) or `run_report.json` (controller) first.

Run limits live in `research_request.json`: 2 rounds, 4 assignments, 300 seconds, $3.00 estimated cost, 3 sources and 6 records per assignment. Costs reported by the SDK are estimates, not billing.

## Offline checks

The `verify_*.py` scripts use stubbed SDK responses and make no API calls:

```bash
python verify_collector.py && python verify_planner.py && python verify_validator.py \
  && python verify_gap_assessment.py && python verify_controller.py && python verify_run_limits.py \
  && python verify_timing.py && python verify_resume.py
```

## File map

| Stage | File | Notes |
|---|---|---|
| Baseline | `00_naive.py` | One call, no tools |
| Setup | `check_setup.py` | Connectivity probe |
| Plan | `planner.py`, `planning_contracts.py`, `plan_checks.py`, `stage_runner.py` | Draft, review, revise; structured output only |
| Collect | `collector.py`, `collection_adapter.py` | WebSearch, WebFetch, `record_evidence`; hooks and caps |
| Validate | `validator.py`, `validation_contracts.py`, `evidence_archive.py` | Support and timing per record |
| Merge | `merger.py` | One claim per role, all citations kept |
| Assess | `assess_gaps.py` | Follow-up questions and stop rules |
| Run | `init_run.py`, `controller.py` | Checkpoints, budget, resume |
| Timing review | `resolve_timing.py` | Part 3: close a timing gap with a dated passage |
| Subagents | `delegate.py` | Lead agent + examiner `AgentDefinition` |
| Sessions | `followup.py` | `resume` and `fork_session` |
| Evaluate | `evaluate.py`, `gold.json` | Deterministic graders |

Versions used for the workshop: `claude-agent-sdk==0.2.175` (bundled Claude Code CLI 2.1.314), `pydantic==2.14.2`, `python-dotenv==1.2.4`.

`gold.json` is a small workshop gold set. It lists roles from the subject's official site and sets an expected timing only for the two widely documented past roles. Review it before reusing it for anything else.
