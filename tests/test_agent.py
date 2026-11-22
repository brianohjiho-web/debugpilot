"""The agent loop, with a fake Claude so it runs without an API key."""
import json

from agent.agent import DebugAgent
from agent.examples import EXAMPLES
from runner.languages import run


def runner(language, source, test, setup=""):
    return run(language, source, test, setup).to_dict()


def fake_llm(*answers):
    replies = iter(json.dumps(a) if isinstance(a, dict) else a for a in answers)
    calls = []
    def llm(messages):
        calls.append(list(messages))  # copy: the agent keeps adding to the same list
        return next(replies)
    llm.calls = calls
    return llm


def test_demo_mode_verifies_example():
    ex = EXAMPLES["python"]
    rep = DebugAgent(runner, llm=None).debug("python", ex["source"], ex["error"])
    assert rep.verified and rep.reproduced and rep.mode == "demo"


def test_demo_mode_refuses_unknown_code():
    rep = DebugAgent(runner, llm=None).debug("python", "def f():\n    return 1\n")
    assert rep.error and "ANTHROPIC_API_KEY" in rep.error


def test_claude_path_verifies():
    ex = EXAMPLES["javascript"]
    llm = fake_llm(dict(ex["answer"], category="code_bug"))
    rep = DebugAgent(runner, llm).debug("javascript", ex["source"], ex["error"])
    assert rep.verified and rep.attempts == 1


def test_retry_when_test_does_not_reproduce():
    ex = EXAMPLES["python"]
    weak = dict(ex["answer"], category="code_bug",
                test_code="def test_nothing():\n    assert average_order_value([{'amount': 2}]) == 2\n")
    llm = fake_llm(weak, dict(ex["answer"], category="code_bug"))
    rep = DebugAgent(runner, llm).debug("python", ex["source"], ex["error"])
    assert rep.verified and rep.attempts == 2
    assert "PASSED on the original" in llm.calls[1][-1]["content"]


def test_never_claims_verified_for_a_bad_fix():
    ex = EXAMPLES["go"]
    bad = dict(ex["answer"], category="code_bug", fixed_source=ex["source"])  # "fix" that changes nothing
    llm = fake_llm(bad, bad)
    rep = DebugAgent(runner, llm).debug("go", ex["source"], ex["error"])
    assert rep.reproduced and not rep.verified


def test_not_a_code_bug():
    llm = fake_llm({"category": "not_code", "summary": "Sensor is broken", "root_cause": "x",
                    "next_steps": ["Send a technician"], "confidence": 80})
    rep = DebugAgent(runner, llm).debug("python", "def f(v):\n    return v\n", "900 V reading")
    assert rep.answer["category"] == "not_code" and not rep.verified


def test_bad_json_then_good():
    ex = EXAMPLES["sql"]
    llm = fake_llm("sorry, here you go", dict(ex["answer"], category="code_bug"))
    rep = DebugAgent(runner, llm).debug("sql", ex["source"], ex["error"])
    assert rep.verified and rep.attempts == 2
