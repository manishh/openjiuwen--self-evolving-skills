# WorkSwarm Skill Self-Evolution: a proof you can re-run

This repo is a small, automated experiment. It shows that a [WorkSwarm](https://github.com/openJiuwen-ai/jiuwenswarm) (formerly JiuwenSwarm) agent can **learn from a mistake it made while using a skill**, and that the next, completely fresh session avoids the mistake. Nobody retrains the model and nobody hand-edits the associated skill.

It runs against the real product (`workswarm==0.2.7`) and a real model (OpenAI `gpt-5.5`). It doesn't use mocks. A Python harness drives the agent, records everything it does, and checks 11 pass/fail conditions.

## The idea in one paragraph

We give the agent a currency-converter skill with a deliberate trap in it (to simulate a skill bug/error). Converting `1000 USD` to `JPY` fails the first time because yen has no decimal places and the script refuses to print `158145.39 JPY`. The error message tells the agent to re-run with `--decimals 0`, so the agent recovers. We then run WorkSwarm's `/evolve` command, which reviews what happened and proposes a lesson. In one of our runs the saved lesson began: *"For target currencies with 0 decimal places under ISO 4217 (for example, JPY), call `scripts/convert.py` with `--decimals 0` from the start."* A human user, played here by the harness, approves it, and WorkSwarm saves it next to the skill. Finally a **new session** gets the same request and gets it right on the first try.

## Repository layout

| Path | What it is |
|---|---|
| `prove` | Entry point (bash). Loads `.env`, finds Python 3.11–3.13, creates `.venv`, installs `workswarm`, runs the harness. |
| `harness/prove.py` | The proof harness: sets up an isolated WorkSwarm, runs the four stages, checks the results, writes the evidence. |
| `skills/currency-converter/` | The skill under test: `SKILL.md` (instructions for the agent) and `scripts/convert.py`. |
| `vendor/jiuwenswarm_sdk/` | WorkSwarm's official Python process SDK (Apache-2.0, copied from tag `v0.2.7`). The harness uses it to talk to the headless `jiuwenswarm-process` runner. |
| `PROOF.md` | The formal contract: the claim, the pass bar, and every setup choice. |
| `requirements.txt` | Pins `workswarm==0.2.7`. The older `jiuwenswarm` PyPI name stops at 0.2.3. |
| `.env.example` | Template for your API key and optional overrides. |

## The skill and its deliberate trap

`scripts/convert.py <amount> <FROM> <TO> [--decimals N]` fetches a live rate from `open.er-api.com` (no API key needed). It rounds to `--decimals` places, 2 by default, and prints a JSON result. It also has a guard: if the target is a zero-decimal ISO 4217 currency (JPY, KRW, VND, ...) and the result still has a fractional part, it exits with:

```
error: 158145.39 JPY has a fractional part, but JPY has 0 decimal places under ISO 4217. Re-run with --decimals 0.
```

`SKILL.md` documents only `<amount> <FROM> <TO>` and never mentions `--decimals`. So an agent that follows the skill as written fails the first time, every time.

## What happens in one repeat

Each repeat starts from scratch in a temporary directory:

**0. Setup.** The harness runs `jiuwenswarm-init` into a fresh temporary `JIUWENSWARM_HOME`, so your real `~/.jiuwenswarm` is never touched. It then edits a few config values (see [Configuration changes](#configuration-changes)), writes the model settings into WorkSwarm's `.env` without the key, and copies the skill into the agent's workspace inside that temporary home.

**1. Control: the agent fails, then recovers.** A new session receives:

> Load the currency-converter skill with skill_tool, then use it to convert 1000 USD to Japanese yen.

As expected, the agent first runs `convert.py 1000 USD JPY`, which fails. It then runs `convert.py 1000 USD JPY --decimals 0`, which succeeds.

**2. Evolve: the lesson is proposed and approved.** In the same session the harness sends `/evolve currency-converter <intent>`. The harness builds the intent mechanically from the control run's tool calls. It contains the exact error line, the command that worked, and the goal "save a lesson in the skill description so that next time the first convert.py call succeeds without this error". It never spells out the rule itself, so WorkSwarm's reviewer has to work out the fix. WorkSwarm then raises an **approval card**, and the harness answers `allow_once`, standing in for a person clicking "Allow Once".

**3. Store: check what was saved on disk.** WorkSwarm doesn't rewrite the skill. Instead it:

- adds an entry to `evolutions.json` in the agent's copy of the skill (inside the temporary home, not the repo's `skills/` folder) with `target: description`, `action: append`, the lesson text, and a starting score of 0.60
- writes a readable copy to `evolution/instructions.md`
- appends a generated "Experience Index" table to the end of `SKILL.md`, between `<!-- evolution-index-start -->` and `<!-- evolution-index-end -->`. The original text above it is left exactly as it was.

The temporary home is deleted after each repeat, so the harness copies the evolved skill folder to `proof-out/repeat-N/skill-after/`. That is where you can open `evolutions.json` after a run.

**4. After: a new session gets it right first time.** A brand-new session, with a different session ID and a new process, gets the identical prompt. The agent loads the skill, reads `evolution/instructions.md`, and its **first** call is already `convert.py 1000 USD JPY --decimals 0`, which succeeds.

## How the harness checks the result

The checks look only at hard evidence: tool-call events from the SDK stream, files on disk, and the approval interaction. They never look at the wording of the model's reply. A repeat passes only if all 11 checks pass:

| # | Stage | Check |
|---|---|---|
| 1 | control | `convert.py` ran, and the first call failed with the fractional-amount error |
| 2 | control | That first call did not already pass `--decimals 0` |
| 3 | control | Before `/evolve`, there was no `evolutions.json` and no index in `SKILL.md` |
| 4 | control | A later call succeeded, so the intent has real evidence to work from |
| 5 | evolve | WorkSwarm asked for approval and the harness answered `allow_once` |
| 6 | store | `evolutions.json` has an entry that targets `description` and mentions `--decimals` |
| 7 | store | `SKILL.md` gained the index block, and the authored text is unchanged |
| 8 | after | The session ID differs from the control session's |
| 9 | after | The first `convert.py` call passes `--decimals 0` and succeeds |
| 10 | after | No call in the turn hit the fractional-amount error |
| 11 | secrets | The API key string appears in no file in the WorkSwarm home or the proof output |

**Retries.** If the agent in a control or after turn never runs `convert.py` at all, that turn tells us nothing about evolution. The harness retries it in a fresh session, up to 3 times, and records the retry in the transcript. The `/evolve` step and the checks are never retried. If a repeat crashes, it counts as failed.

## Running it

You need Linux or macOS with bash, Python 3.11–3.13, network access, and an OpenAI API key with access to `gpt-5.5`.

```bash
export OPENAI_API_KEY=sk-...        # or: cp .env.example .env and fill it in
./prove --repeat 3
```

The exit code is `0` only if every check passes in every repeat. One repeat takes about 2 minutes and uses about 365k input tokens (roughly 120k per turn, mostly WorkSwarm's own system prompt) and under 1.5k output tokens.

These optional environment variables override the defaults: 

- `MODEL_NAME` = `gpt-5.5`
- `API_BASE` = `https://api.openai.com/v1`
- `PROOF_OUT` = `./proof-out`
- `PROVE_VENV` = `.venv`

**How the key is handled.** The key goes to WorkSwarm only through the child process's environment. The harness deletes the `API_KEY=` line that `jiuwenswarm-init` writes to its `.env`, because WorkSwarm loads that file with `override=True`, so even an empty line would override the environment. As a final safeguard, check 11 searches every file in the temporary WorkSwarm home and in proof-out/ for the API key, and the repeat fails if the key turns up anywhere. The temporary WorkSwarm home is deleted at the end of each repeat.

## Reading the output

The console shows each prompt, each tool call with `ok`/`FAILED`, the approval card, the agent's answers, and a PASS/FAIL line for each check. The evidence is written to `proof-out/`, which is wiped at the start of every run:

- `summary.json`: repeats run, repeats passed, and `all_passed`
- `repeat-N/transcript.md`: a readable record with the checks, prompts, every tool
  call and result, the full approval card JSON, final answers, and the saved entries
- `repeat-N/report.json`: the same information as data, plus token usage, timings,
  and the parsed `convert.py` calls
- `repeat-N/SKILL.md.diff`: exactly what `/evolve` added to `SKILL.md`
- `repeat-N/skill-after/`: the evolved skill folder (`evolutions.json`, `evolution/*.md`)
- `repeat-N/events.jsonl`: the raw SDK event stream

## Configuration changes

The harness changes these settings in each fresh WorkSwarm home:

| Setting | Value | Why |
|---|---|---|
| `react.evolution.skill_evolution` | `true` | The self-evolution switch, which is off by default. In the web UI: **Settings → Agent → Skills → "Automatic evolution and retention of local skills"**. |
| `react.evolution.auto_save` | `false` (default) | Keeps the human approval step. |
| `symphony.skill_retrieval.enabled` | `false` | When on, skills are found only through a search index, and a freshly copied skill isn't in it. |
| `progressive_tool_enabled` | `false` | When on, `skill_tool` is hidden behind `tool_search` and the model tends to call the skill name as if it were a tool. |
| `setup_guide.enabled` | `false` | First-run onboarding steers a fresh install toward the skill marketplace. |
| `preferred_language` | `en` | `jiuwenswarm-init` defaults to Chinese when run headless. |

Other deliberate choices:

- **Mode `agent.work.normal`**, because `/evolve` is refused in fast and code modes.
- **The prompt names the skill and the loader**, which is the same as picking the
  skill with `/` in the web UI.
- **`/evolve` gets an intent built from the trace.** Each headless call runs in its own process, so the reviewer can see only the current call, not the earlier failure. The goal wording matters. Given only the facts, the reviewer saved a *reactive* lesson ("if you see the error, re-run with `--decimals 0`") in 2 of 3 tries, and the next session followed it literally: it failed once, then retried. Lessons saved in the skill's *description* are injected in full into the agent's prompt, while *body* lessons show up only as a short index line. That's why the intent asks for a description lesson.

## What this does not show

- **Evolution doesn't trigger automatically here.** In 0.2.7 the single agent has no error-count threshold (`signal_trigger=False`). The only other trigger is a periodic self-review inside a long-running server, which this headless harness doesn't use.
- **The skill text isn't rewritten,** and WorkSwarm doesn't make a CREATE/PATCH decision. It saves an experience, using one of the actions `append | merge | replace | skip` on one of the targets `description | body | script`, and indexes it in `SKILL.md`. Experiences have Effectiveness/Utilization/Freshness scores, but those only rank saved experiences for `/evolve_simplify`.
- **Only the same task is tested before and after** (1000 USD → JPY). The harness doesn't claim the lesson carries over to other zero-decimal currencies.
- **The web UI pages** (Skill Index, Skill Graph) weren't used.
