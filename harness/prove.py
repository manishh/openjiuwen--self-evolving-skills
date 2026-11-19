#!/usr/bin/env python3
"""Proof harness: JiuwenSwarm Skill Self-Evolution (workswarm 0.2.7).

One repeat, in a brand-new isolated JiuwenSwarm home:
  control  fresh session, skill as shipped: convert 1000 USD to JPY.
           The first convert.py call must fail (fractional yen), then the agent recovers.
  evolve   same session: `/evolve currency-converter <intent built from the trace>`;
           JiuwenSwarm raises an approval card, we answer allow_once.
  store    evolutions.json + Experience Index in SKILL.md are inspected on disk.
  after    NEW session, identical prompt. The first convert.py call must already
           pass --decimals 0 and succeed.

Run through ../prove. Exit 0 only if every check in every repeat passes.
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "vendor"))
from jiuwenswarm_sdk import Client  # noqa: E402  (official SDK, vendored from tag v0.2.7)

SKILL_NAME = "currency-converter"
SKILL_SRC = REPO / "skills" / SKILL_NAME
MODE = "agent.work.normal"  # /evolve is refused in fast/code modes
FAILURE_MARKER = "has a fractional part"
INDEX_START = "<!-- evolution-index-start -->"
PROMPT = f"Load the {SKILL_NAME} skill with skill_tool, then use it to convert 1000 USD to Japanese yen."

MODEL_ENV = {
    "MODEL_PROVIDER": os.environ.get("MODEL_PROVIDER", "OpenAI"),
    "ENDPOINT_PROFILE": os.environ.get("ENDPOINT_PROFILE", "openai"),
    "API_BASE": os.environ.get("API_BASE", "https://api.openai.com/v1"),
    "MODEL_NAME": os.environ.get("MODEL_NAME", "gpt-5.5"),
}


def api_key() -> str:
    return os.environ.get("API_KEY") or os.environ.get("OPENAI_API_KEY") or ""


def say(text: str = "") -> None:
    print(text, flush=True)


# ------------------------------------------------------------------ isolated home

def bin_dir() -> Path:
    return Path(sys.executable).parent


def base_env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
    env["JIUWENSWARM_HOME"] = str(home)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PATH"] = str(bin_dir()) + os.pathsep + env.get("PATH", "")
    return env


def agent_env(home: Path) -> dict[str, str]:
    env = base_env(home)
    env["API_KEY"] = api_key()  # only ever in the child's environment, never on disk
    return env


def set_yaml_scalar(text: str, key_path: list[str], value: str) -> str:
    """Set `a.b.c: value` in YAML by walking indentation, without reformatting."""
    lines = text.splitlines(keepends=True)
    depth, indent = 0, -1
    for i, line in enumerate(lines):
        stripped = line.lstrip(" ")
        cur = len(line) - len(stripped)
        if not stripped.strip() or stripped.startswith("#"):
            continue
        if depth > 0 and cur <= indent:
            break
        if (depth == 0 and cur == 0) or (depth > 0 and cur == indent + 2):
            if re.match(rf"{re.escape(key_path[depth])}\s*:", stripped):
                if depth == len(key_path) - 1:
                    lines[i] = f"{' ' * cur}{key_path[depth]}: {value}\n"
                    return "".join(lines)
                depth += 1
                indent = cur
    raise KeyError(".".join(key_path))


CONFIG_CHANGES = [
    (["preferred_language"], "en"),
    (["react", "evolution", "skill_evolution"], "true"),
    (["react", "evolution", "auto_save"], "false"),
    (["symphony", "skill_retrieval", "enabled"], "false"),
    (["progressive_tool_enabled"], "false"),
    (["setup_guide", "enabled"], "false"),
]


def prepare_home(home: Path) -> Path:
    home.mkdir(parents=True)
    init = subprocess.run([str(bin_dir() / "jiuwenswarm-init")], input="en\n", text=True,
                          capture_output=True, env=base_env(home), encoding="utf-8")
    data = home / ".jiuwenswarm"
    cfg_path = data / "config" / "config.yaml"
    if init.returncode != 0 or not cfg_path.exists():
        raise RuntimeError(f"jiuwenswarm-init failed:\n{init.stdout[-2000:]}\n{init.stderr[-2000:]}")
    cfg = cfg_path.read_text(encoding="utf-8")
    for path, value in CONFIG_CHANGES:
        cfg = set_yaml_scalar(cfg, path, value)
    cfg_path.write_text(cfg, encoding="utf-8")

    # jiuwenswarm load_dotenv(..., override=True)s this file, so an `API_KEY=` line
    # (even empty) would override the child env. Remove it; the key comes from env.
    env_path = data / "config" / ".env"
    lines = [l for l in env_path.read_text(encoding="utf-8").splitlines()
             if not re.match(r"^\s*API_KEY\s*=", l)]
    text = "\n".join(lines) + "\n"
    for key, value in MODEL_ENV.items():
        pat = rf"(?m)^{key}=.*$"
        line = f'{key}="{value}"'
        text = re.sub(pat, lambda _m: line, text) if re.search(pat, text) else text + line + "\n"
    env_path.write_text(text, encoding="utf-8")

    shutil.copytree(SKILL_SRC, skill_dir(home))
    return home


def skill_dir(home: Path) -> Path:
    return home / ".jiuwenswarm" / "agent" / "workspace" / "skills" / SKILL_NAME


# ------------------------------------------------------------------ running turns

@dataclass
class Turn:
    name: str
    prompt: str
    session_id: str | None = None
    status: str = ""
    output: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    interactions: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0
    attempts: int = 1
    retry_notes: list[str] = field(default_factory=list)


def pick_allow_once(question: dict[str, Any]) -> list[str]:
    for opt in question.get("options") or []:
        if isinstance(opt, dict) and opt.get("value") == "allow_once":
            return ["allow_once"]
    return []  # no allow_once option: answer nothing, the check will fail loudly


def convert_calls(turn: Turn) -> list[dict[str, Any]]:
    """Every shell execution of convert.py in the turn, in order, with outcome."""
    calls: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for ev in turn.events:
        kind = ev.get("event_type")
        p = ev.get("payload") or {}
        if kind == "chat.tool_call":
            call = p.get("tool_call") or {}
            try:
                args = json.loads(call.get("arguments") or "{}")
            except ValueError:
                args = {}
            command = str(args.get("command", "")) if isinstance(args, dict) else ""
            if "convert.py" in command:  # shell commands only, not read_file of the script
                cid = call.get("tool_call_id") or f"c{len(order)}"
                calls[cid] = {"command": command, "ok": None, "fractional_error": False, "result": ""}
                order.append(cid)
        elif kind == "chat.tool_result" and p.get("tool_call_id") in calls:
            result = str(p.get("result", ""))
            c = calls[p["tool_call_id"]]
            c["ok"] = result.startswith("success=True") and FAILURE_MARKER not in result
            c["fractional_error"] = FAILURE_MARKER in result
            c["result"] = result[:600]
    return [calls[c] for c in order]


def usage(turn: Turn) -> dict[str, int]:
    total = {"input_tokens": 0, "output_tokens": 0}
    for ev in turn.events:
        if ev.get("event_type") == "chat.usage_summary":
            u = (ev.get("payload") or {}).get("usage") or {}
            for k in total:
                total[k] += int(u.get(k) or 0)
    return total


def has_decimals0(command: str) -> bool:
    return bool(re.search(r"--decimals[ =]0\b", command))


def short_cmd(command: str) -> str:
    m = re.search(r"convert\.py[\"']?\s*(.*)", command)
    return f"python scripts/convert.py {m.group(1).strip()}" if m else command


async def run_turn(client: Client, turn: Turn, cwd: Path, log_path: Path) -> Turn:
    request: dict[str, Any] = {"input": turn.prompt, "mode": MODE,
                               "workspace": {"cwd": str(cwd)}, "timeout_seconds": 600}
    if turn.session_id:
        request["session_id"] = turn.session_id
    say(f"  you> {turn.prompt}")
    log = log_path.open("a", encoding="utf-8")

    async def on_event(event: dict[str, Any]) -> None:
        turn.events.append(event)
        log.write(json.dumps({"turn": turn.name, "event": event}, ensure_ascii=False) + "\n")
        log.flush()
        kind = event.get("event_type")
        p = event.get("payload") or {}
        if kind == "chat.tool_call":
            call = p.get("tool_call") or {}
            raw = str(call.get("arguments", ""))
            try:
                parsed = json.loads(raw)
                shown = str(parsed.get("command") or raw) if isinstance(parsed, dict) else raw
            except ValueError:
                shown = raw
            say(f"  agent> {call.get('name')}: {short_cmd(shown)[:160]}")
        elif kind == "chat.tool_result":
            r = str(p.get("result", ""))
            err = re.search(r"error: [^\n'\"\\]*", r)
            say("         -> ok" if r.startswith("success=True") and not err else
                f"         -> FAILED: {err.group(0) if err else r[:120]}")

    async def on_interaction(event: dict[str, Any]) -> list[dict[str, Any]]:
        inter = (event.get("payload") or {}).get("interaction") or {}
        answers = []
        for q in inter.get("questions") or []:
            a = {"question": q.get("question"), "selected_options": pick_allow_once(q), "custom_input": ""}
            if q.get("card_id"):
                a["card_id"] = q["card_id"]
            answers.append(a)
            say("  [approval card] " + str(q.get("question", ""))[:400].replace("\n", "\n                  "))
            say(f"  [harness answers] {a['selected_options']}")
        turn.interactions.append({"interaction": inter, "answers": answers})
        log.write(json.dumps({"turn": turn.name, "interaction": inter, "answers": answers}, ensure_ascii=False) + "\n")
        log.flush()
        return answers

    started = time.monotonic()
    try:
        result = await client.run(request, on_event=on_event, on_interaction=on_interaction,
                                  deadline_seconds=900)
    finally:
        log.close()
    turn.seconds = round(time.monotonic() - started, 1)
    turn.status = str(result.get("status", ""))
    turn.output = str(result.get("output", "") or "")
    turn.session_id = result.get("session_id") or turn.session_id
    say(f"  agent says> {turn.output.strip()[:500]}")
    return turn


async def run_skill_turn(client: Client, name: str, cwd: Path, log_path: Path) -> Turn:
    """Fresh session. Retry ONLY if the agent never ran convert.py (precondition)."""
    notes: list[str] = []
    for attempt in range(1, 4):
        turn = await run_turn(client, Turn(name, PROMPT), cwd, log_path)
        turn.attempts, turn.retry_notes = attempt, notes
        if convert_calls(turn):
            return turn
        note = f"attempt {attempt}: agent never ran convert.py (status={turn.status}); retried in a fresh session"
        notes.append(note)
        say(f"  ({name}) {note}")
    return turn


# ------------------------------------------------------------------ one repeat

def authored_text_untouched(before: str, after: str) -> bool:
    a = [l.rstrip() for l in before.splitlines()]
    b = [l.rstrip() for l in after.splitlines()]
    ops = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    return all(tag in ("equal", "insert") for tag, *_ in ops)


def build_intent(control: Turn) -> str | None:
    calls = convert_calls(control)
    err_line = None
    for c in calls:
        if c["fractional_error"]:
            m = re.search(r"error: [^\n\"\\]*?Re-run with --decimals 0\.", c["result"])
            err_line = m.group(0) if m else None
            break
    fix = next((c for c in calls if c["ok"]), None)
    if not err_line or not fix:
        return None
    # States the goal (next time, no failed first call) and the facts from the trace.
    # It does not state the rule; the reviewer has to derive it.
    return (f"/evolve {SKILL_NAME} Save a lesson in the skill description so that next time the "
            f"first convert.py call succeeds without this error. In this task the first "
            f"convert.py call failed with: \"{err_line}\" It then succeeded as: "
            f"`{short_cmd(fix['command'])}`")


async def one_repeat(n: int, out: Path) -> dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix=f"jws-proof-{n}-"))
    home = prepare_home(tmp / "home")
    workdir = tmp / "workdir"
    workdir.mkdir()
    client = Client([str(bin_dir() / "jiuwenswarm-process")], cwd=str(workdir), env=agent_env(home))
    log_path = out / "events.jsonl"
    sdir = skill_dir(home)
    skill_md_before = (SKILL_SRC / "SKILL.md").read_text(encoding="utf-8")
    checks: dict[str, bool] = {}
    turns: dict[str, Turn] = {}

    say("\n--- CONTROL: fresh session, skill as shipped (SKILL.md never mentions --decimals)")
    pre_store = (sdir / "evolutions.json").exists()
    pre_index = INDEX_START in (sdir / "SKILL.md").read_text(encoding="utf-8")
    turns["control"] = c = await run_skill_turn(client, "control", workdir, log_path)
    calls = convert_calls(c)
    checks["1 control: convert.py ran and the first call failed with the fractional-amount error"] = \
        bool(calls) and calls[0]["fractional_error"]
    checks["2 control: the first call did not pass --decimals 0"] = bool(calls) and not has_decimals0(calls[0]["command"])
    checks["3 control: no evolutions.json and no evolution index before /evolve"] = not pre_store and not pre_index
    checks["4 control: a later convert.py call succeeded (recovery evidence exists)"] = any(x["ok"] for x in calls[1:])

    say("\n--- EVOLVE: same session, /evolve with an intent copied from the trace")
    intent = build_intent(c)
    if intent is None:
        intent = f"/evolve {SKILL_NAME}"
        say("  (no error/recovery pair in the trace; sending bare /evolve; checks will show it)")
    turns["evolve"] = e = await run_turn(client, Turn("evolve", intent, session_id=c.session_id), workdir, log_path)
    answered = [a for it in e.interactions for a in it["answers"] if a["selected_options"] == ["allow_once"]]
    checks["5 evolve: JiuwenSwarm requested approval and the harness answered allow_once"] = bool(answered)

    store = sdir / "evolutions.json"
    entries = json.loads(store.read_text(encoding="utf-8")).get("entries", []) if store.exists() else []
    good = [x for x in entries if (x.get("change") or {}).get("target") == "description"
            and "--decimals" in json.dumps(x, ensure_ascii=False)]
    checks["6 store: evolutions.json has an entry targeting description that mentions --decimals"] = bool(good)
    skill_md_after = (sdir / "SKILL.md").read_text(encoding="utf-8")
    checks["7 store: SKILL.md gained the evolution index; authored text unchanged"] = \
        INDEX_START in skill_md_after and authored_text_untouched(skill_md_before, skill_md_after)
    for x in entries:
        say(f"  [evolutions.json] target={(x.get('change') or {}).get('target')} summary={x.get('summary')}")

    say("\n--- AFTER: NEW session, identical prompt")
    turns["after"] = a = await run_skill_turn(client, "after", workdir, log_path)
    acalls = convert_calls(a)
    checks["8 after: new session id (not the control session)"] = bool(a.session_id) and a.session_id != c.session_id
    checks["9 after: first convert.py call passes --decimals 0 and succeeds"] = \
        bool(acalls) and has_decimals0(acalls[0]["command"]) and acalls[0]["ok"] is True
    checks["10 after: no call hit the fractional-amount error"] = bool(acalls) and not any(x["fractional_error"] for x in acalls)

    # Evidence: the evolved skill directory, and a diff of SKILL.md.
    shutil.copytree(sdir, out / "skill-after", ignore=shutil.ignore_patterns("__pycache__"))
    (out / "SKILL.md.diff").write_text("".join(difflib.unified_diff(
        skill_md_before.splitlines(True), skill_md_after.splitlines(True),
        "SKILL.md (as shipped)", "SKILL.md (after /evolve)")), encoding="utf-8")

    key = api_key()
    leaks = [str(p) for root in (tmp, out) for p in root.rglob("*")
             if p.is_file() and key and key.encode() in p.read_bytes()]
    checks["11 secrets: API key found in no file (JiuwenSwarm home, proof output)"] = bool(key) and not leaks
    shutil.rmtree(tmp, ignore_errors=True)

    report = {
        "repeat": n, "passed": all(checks.values()), "checks": checks,
        "product": "workswarm==0.2.7", "model": MODEL_ENV["MODEL_NAME"], "mode": MODE,
        "config_changes": {".".join(k): v for k, v in CONFIG_CHANGES},
        "evolution_entries": entries,
        "turns": {k: {"prompt": t.prompt, "status": t.status, "session_id": t.session_id,
                      "seconds": t.seconds, "usage": usage(t), "attempts": t.attempts, "retry_notes": t.retry_notes,
                      "convert_calls": convert_calls(t), "interactions": t.interactions,
                      "output": t.output} for k, t in turns.items()},
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "transcript.md").write_text(transcript(report, turns), encoding="utf-8")
    return report


def transcript(report: dict[str, Any], turns: dict[str, Turn]) -> str:
    o = [f"# Repeat {report['repeat']}: {'PASSED' if report['passed'] else 'FAILED'}", "",
         f"Product `{report['product']}`, model `{report['model']}`, mode `{report['mode']}`.", "",
         "Config changes: " + ", ".join(f"`{k}: {v}`" for k, v in report["config_changes"].items()), "",
         "## Checks", ""]
    o += [f"- [{'x' if ok else ' '}] {name}" for name, ok in report["checks"].items()]
    for name, t in turns.items():
        o += ["", f"## {name}", "", f"Session `{t.session_id}`, status `{t.status}`, {t.seconds}s, tokens {usage(t)}.", "",
              "**Prompt:**", "", "```text", t.prompt, "```", ""]
        for note in t.retry_notes:
            o.append(f"_Retry: {note}_")
        o.append("**Tool calls:**")
        o.append("")
        for ev in t.events:
            kind, p = ev.get("event_type"), ev.get("payload") or {}
            if kind == "chat.tool_call":
                call = p.get("tool_call") or {}
                o.append(f"- `{call.get('name')}` `{str(call.get('arguments', ''))[:400]}`")
            elif kind == "chat.tool_result":
                r = str(p.get("result", ""))
                err = re.search(r"error: [^\n'\"\\]*", r)
                o.append(f"  - result: {'ok' if r.startswith('success=True') and not err else 'FAILED'}"
                         + (f" `{err.group(0)}`" if err else ""))
        for it in t.interactions:
            o += ["", "**Approval card raised by JiuwenSwarm:**", "", "```json",
                  json.dumps(it["interaction"], indent=2, ensure_ascii=False)[:4000], "```", "",
                  f"Harness answered: `{json.dumps(it['answers'], ensure_ascii=False)}`"]
        o += ["", "**Final answer:**", "", "> " + t.output.strip().replace("\n", "\n> ")]
    o += ["", "## evolutions.json entries", "", "```json",
          json.dumps(report["evolution_entries"], indent=2, ensure_ascii=False), "```", "",
          "See `SKILL.md.diff` and `skill-after/` for the files on disk.", ""]
    return "\n".join(o)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()
    if not api_key():
        sys.exit("OPENAI_API_KEY (or API_KEY) is not set in the environment.")
    out_root = Path(os.environ.get("PROOF_OUT", REPO / "proof-out")).resolve()
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True)
    say(f"Model: {MODEL_ENV['MODEL_PROVIDER']} / {MODEL_ENV['MODEL_NAME']}; evidence -> {out_root}")
    results = []
    for n in range(1, args.repeat + 1):
        out = out_root / f"repeat-{n}"
        out.mkdir()
        say(f"\n========== repeat {n}/{args.repeat}")
        try:
            report = asyncio.run(one_repeat(n, out))
        except Exception as exc:  # a crash is a failed repeat, never a pass
            report = {"repeat": n, "passed": False, "checks": {}, "error": repr(exc)}
            (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            (out / "transcript.md").write_text(f"# Repeat {n}: FAILED\n\nCrashed: `{exc!r}`\n", encoding="utf-8")
            say(f"  CRASH: {exc!r}")
        for name, ok in report["checks"].items():
            say(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        results.append(bool(report["passed"]) and bool(report["checks"]))
    summary = {"repeats": len(results), "passed": sum(results), "all_passed": all(results)}
    (out_root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    say(f"\n{sum(results)}/{len(results)} repeats passed. Evidence in {out_root}")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
