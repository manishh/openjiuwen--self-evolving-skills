# Demo contract

## Product and version

- WorkSwarm (formerly JiuwenSwarm), installed from PyPI as `workswarm==0.2.7` (the latest release on
  2026-10-06; the old `jiuwenswarm` PyPI name stops at 0.2.3). It provides the
  `jiuwenswarm-init` / `jiuwenswarm-process` commands and the `openjiuwen` core.
- Driven headlessly with the official process SDK `jiuwenswarm-process-sdk` 0.1.0,
  vendored from `sdks/python` at git tag `v0.2.7` (Apache-2.0).
- Model: OpenAI `gpt-5.5` (`MODEL_PROVIDER=OpenAI`, `ENDPOINT_PROFILE=openai`).

## Claim

After a skill fails at runtime, WorkSwarm's `/evolve` command turns that failure
into a saved, human-approved experience attached to the skill (in
`evolutions.json`, indexed in `SKILL.md`), and a brand-new session running the same
task uses that experience and succeeds on its first try, with no model retraining
and no hand edit of the skill.

## Control

A fresh session with the skill as shipped. `SKILL.md` never mentions
`convert.py --decimals`, and `convert.py` refuses to print a fractional yen amount.
Prompt: "Load the currency-converter skill with skill_tool, then use it to convert
1000 USD to Japanese yen." Expected: the agent's **first** `convert.py` call fails
with `has a fractional part ... Re-run with --decimals 0`, then the agent recovers.
Before evolution the skill has no `evolutions.json` and no Experience Index.

## Action

In the same session, send `/evolve currency-converter <intent>`. The intent is
built mechanically by the harness from the control trajectory: the exact error
line the script printed and the command that later succeeded (each headless call
is its own process, and the reviewer only sees the current call's trajectory). The
reviewer proposes an experience; WorkSwarm shows an approval card; the harness
answers `allow_once` (the human approval step, `auto_save: false`).

## Observable after

- An approval interaction was raised by WorkSwarm and answered.
- `skills/currency-converter/evolutions.json` exists with at least one entry whose
  `change.target` is `description` and whose content mentions `--decimals`.
- `SKILL.md` now contains the generated `<!-- evolution-index-start -->` block;
  the authored text above it is unchanged (the diff is saved per repeat).
- A **new** session (different session id, new process) given the identical prompt
  makes a first `convert.py` call that already passes `--decimals 0` and succeeds,
  and no call in that turn hits the fractional-amount error.

Observed through tool-call events from the SDK stream, files on disk, and the
approval interaction, not the wording of the model's answer.

## Pass bar (every repeat)

1. control: `convert.py` was run; the first call fails with the fractional-amount error.
2. control: the first call did not pass `--decimals 0`.
3. control: before evolution, no `evolutions.json` and no evolution index in `SKILL.md`.
4. control: a later call succeeded (the evidence the intent is built from exists).
5. evolve: WorkSwarm requested approval and the harness answered `allow_once`.
6. store: `evolutions.json` has >= 1 entry; at least one targets `description` and mentions `--decimals`.
7. store: `SKILL.md` contains the evolution index block, and the authored part is byte-identical to the original.
8. after: a different session id than the control.
9. after: `convert.py` was run; the first call includes `--decimals 0` and succeeds.
10. after: no call in the turn hit the fractional-amount error.
11. secrets: the API key string appears in no file under the WorkSwarm home or the proof output.

`./prove --repeat N` exits 0 only if all N repeats pass all checks.

## Setup choices (must be disclosed in the article)

| Choice | Value | Why |
|---|---|---|
| `react.evolution.skill_evolution` | `true` | The self-evolution switch; off by default (web UI: Settings → Agent → Skills → "Automatic evolution and retention of local skills"). |
| `react.evolution.auto_save` | `false` (default) | Keeps the human approval card. |
| `symphony.skill_retrieval.enabled` | `false` | With retrieval on, skills are reachable only via a search index a newly copied skill is not in. |
| `progressive_tool_enabled` | `false` | Otherwise `skill_tool` hides behind `tool_search` and models call the skill name as a tool. |
| `setup_guide.enabled` | `false` | First-run onboarding steers a fresh install to the skill marketplace. |
| `preferred_language` | `en` | `jiuwenswarm-init` defaults to `zh` headless. |
| Mode | `agent.work.normal` | `/evolve` is refused in fast/code modes. |
| Prompt names skill and loader | "Load the currency-converter skill with skill_tool, then ..." | Equivalent to picking the skill with `/` in the web UI; without it the agent sometimes browses the marketplace. |
| `/evolve` intent | "Save a lesson in the skill description so that next time the first convert.py call succeeds without this error. In this task the first convert.py call failed with: "<error line>" It then succeeded as: `<command>`" (error line and command copied mechanically from the trace) | The reviewer cannot see a previous process's trace. Description experiences are injected in full into the system prompt; body ones only as a ~90-char index line. Stating the goal matters: without "so that next time the first call succeeds" the reviewer saved a reactive lesson ("when it reports the error, rerun with --decimals 0") in 2 of 3 repeats, the new session followed it literally (fail, then retry) and those repeats failed check 9. The intent never states the rule itself. |
| Approval answer | `allow_once` | Scripted stand-in for the human clicking "Allow Once". |
| Retry | A skill turn where the agent never runs `convert.py` is retried in a fresh session (max 3), recorded in the transcript. | Such a turn says nothing about evolution. Evolve and checks are never retried. |
| Same task before and after | 1000 USD → JPY both times | Clean before/after on one task. Transfer to other currencies is not claimed. |

## Content plan vs. product (verified in `workswarm==0.2.7` source)

The approved plan describes mechanisms that do not exist as written. The article
must describe the real ones:

- **No CREATE/PATCH decision.** Patch actions are `append | merge | replace | skip`
  (`openjiuwen/agent_evolving/protocols.py`, `VALID_PATCH_ACTIONS`); targets are
  `description | body | script`. "Create" exists only as a skill-authoring mode.
- **Effectiveness/Utilization/Freshness scoring exists but does not pick a branch
  or promote a definition.** `openjiuwen/agent_evolving/experience/scorer.py`
  (weights 0.5/0.3/0.2) scores saved experiences for `/evolve_simplify`.
- **No error-count threshold on the single agent.** `interface_deep.py::_build_skill_evolution_rail`
  hard-codes `signal_trigger=False, review_trigger=True`; the passive error/
  dissatisfaction signal path (`openjiuwen/agent_evolving/signal/`) is not used
  there. Evolution is triggered by `/evolve` or the agent's periodic self-review.
- **The skill definition is not rewritten.** Authored `SKILL.md` text is untouched;
  the lesson is stored in `evolutions.json` + `evolution/*.md`, and a generated
  Experience Index block is appended to `SKILL.md`.
- Web UI pages (Skill Index / Skill Graph) were not exercised; this proof is headless.
