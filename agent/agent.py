"""The debugging agent.

The loop, in plain words:
  1. Try to load the file as-is (catches files that don't even compile).
  2. Ask Claude for: summary, root cause, next steps, a test, and a fixed file.
  3. Prove it: the test must FAIL on the original and PASS on the fix.
  4. If it isn't proven, show Claude exactly what happened and let it try again.
  5. Never mark anything "verified" that the sandbox didn't actually pass.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Protocol

from runner.languages import TEST_RULES

from .examples import EXAMPLES
from .prompts import first_prompt, retry_prompt

LOAD_CHECKS = {
    "python": "def test__loads():\n    pass\n",
    "javascript": "function test__loads() {}\n",
    "c": "int main(void) { return 0; }\n",
    "cpp": "int main() { return 0; }\n",
    "java": "public class TestMain { public static void main(String[] a) {} }\n",
    "go": 'package x\n\nimport "testing"\n\nfunc TestLoads(t *testing.T) {}\n',
    "r": "test__loads <- function() { invisible(TRUE) }\n",
    # SQL has no load check: a query-only file needs the agent's sample tables first.
}


class Runner(Protocol):
    def __call__(self, language: str, source: str, test: str, setup: str = "") -> dict: ...


@dataclass
class Report:
    language: str
    mode: str                      # "claude" or "demo"
    steps: list[dict] = field(default_factory=list)
    answer: dict | None = None
    before: dict | None = None
    after: dict | None = None
    reproduced: bool = False
    verified: bool = False
    attempts: int = 0
    error: str | None = None

    def step(self, status: str, label: str, detail: str = ""):
        self.steps.append({"status": status, "label": label, "detail": detail})

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def parse_json(text: str) -> dict:
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("Claude didn't return JSON")
    data = json.loads(text[a:b + 1])
    for key in ("summary", "root_cause", "test_code", "fixed_source", "pr_title", "test_setup", "category"):
        data[key] = str(data.get(key) or "")
    data["next_steps"] = [str(s) for s in (data.get("next_steps") or [])][:6]
    try:
        data["confidence"] = max(0, min(100, int(data.get("confidence", 0))))
    except (TypeError, ValueError):
        data["confidence"] = 0
    return data


class ClaudeLLM:
    """Thin wrapper so the agent can be tested with a fake LLM."""

    def __init__(self, model: str | None = None):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")

    def __call__(self, messages: list[dict]) -> str:
        msg = self.client.messages.create(model=self.model, max_tokens=8000, messages=messages)
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _demo_answer(language: str, source: str) -> dict | None:
    ex = EXAMPLES.get(language)
    if ex and source.strip() == ex["source"].strip():
        return dict(ex["answer"], category="code_bug")
    return None


class DebugAgent:
    def __init__(self, runner: Runner, llm: Callable[[list[dict]], str] | None, max_attempts: int = 2):
        self.run = runner
        self.llm = llm
        self.max_attempts = max_attempts

    def debug(self, language: str, source: str, error: str = "", demo_answer: dict | None = None) -> Report:
        rep = Report(language=language, mode="claude" if self.llm else "demo")
        if language not in TEST_RULES:
            rep.error = f"Unsupported language: {language}"
            return rep
        if not source.strip():
            rep.error = "Paste some code first."
            return rep

        # 1. Does the file even load?
        load_error = None
        if language in LOAD_CHECKS:
            r = self.run(language, source, LOAD_CHECKS[language])
            load_error = r.get("compile_error")
            rep.step("done" if not load_error else "fail", "Load the file as-is",
                     "OK" if not load_error else load_error[:300])

        # 2. Ask for an analysis + test + fix.
        if not self.llm:
            ans = demo_answer or _demo_answer(language, source)
            if not ans:
                rep.error = ("No ANTHROPIC_API_KEY is set, so only the built-in examples and the demo site work. "
                             "Add your key to .env to debug your own code.")
                return rep
            rep.step("done", "Analyze the bug", "Demo mode: prepared answer for the built-in example")
            answers = iter([ans])
        else:
            answers = None

        messages = [{"role": "user", "content": first_prompt(language, source, error, load_error)}]
        for attempt in range(1, self.max_attempts + 1):
            rep.attempts = attempt
            try:
                if answers is not None:
                    ans = next(answers)
                else:
                    raw = self.llm(messages)
                    messages.append({"role": "assistant", "content": raw})
                    ans = parse_json(raw)
                    rep.step("done", f"Claude's analysis (attempt {attempt})", ans.get("pr_title", ""))
            except StopIteration:
                break
            except Exception as e:  # bad JSON or API error
                rep.step("fail", f"Claude's analysis (attempt {attempt})", str(e)[:300])
                if attempt == self.max_attempts:
                    rep.error = "Couldn't get a usable answer from Claude. Try again."
                    return rep
                messages.append({"role": "user", "content": "That wasn't valid JSON. Reply with only the JSON object."})
                continue
            rep.answer = ans

            if ans.get("category") == "not_code":
                rep.step("done", "Decide: code bug or something else", "Not a code bug, so no code change")
                return rep

            # 3. Prove it.
            before = self.run(language, source, ans["test_code"], ans.get("test_setup", ""))
            rep.before = before
            rep.reproduced = _failed(before)
            rep.step("done" if rep.reproduced else "fail", "Run the new test on the original (should fail)",
                     _summ(before))
            after = None
            if rep.reproduced:
                after = self.run(language, ans["fixed_source"], ans["test_code"], ans.get("test_setup", ""))
                rep.after = after
                rep.verified = bool(after.get("all_passed"))
                rep.step("done" if rep.verified else "fail", "Run the test on the fix (should pass)", _summ(after))
            if rep.verified:
                return rep

            # 4. Not proven: give Claude the real results and try again.
            if answers is not None or attempt == self.max_attempts:
                break
            problem = ("Your test PASSED on the original file, so it doesn't catch the bug."
                       if not rep.reproduced else "Your fix did not pass every test.")
            messages.append({"role": "user", "content": retry_prompt(language, problem, before, after)})
            rep.step("run", "Not proven yet, sending the real results back to Claude", problem)
            rep.after = None
        return rep


def _failed(r: dict) -> bool:
    return bool(r.get("compile_error")) or any(not x["ok"] for x in r.get("results", []))


def _summ(r: dict) -> str:
    if r.get("compile_error"):
        return "Didn't compile/load: " + r["compile_error"].splitlines()[0][:200]
    res = r.get("results", [])
    passed = sum(1 for x in res if x["ok"])
    fails = [f"{x['name']}: {x['err'].splitlines()[0][:120] if x['err'] else 'failed'}" for x in res if not x["ok"]]
    return f"{passed}/{len(res)} passing" + (" · " + "; ".join(fails) if fails else "")
