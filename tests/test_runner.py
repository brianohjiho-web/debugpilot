"""Every language: the example test must fail on the buggy code and pass on the fix."""
import shutil

import pytest

from agent.examples import EXAMPLES
from runner.languages import run

TOOLS = {"python": "python3", "javascript": "node", "sql": "python3", "c": "gcc",
         "cpp": "g++", "java": "javac", "go": "go", "r": "Rscript"}


@pytest.mark.parametrize("lang", list(EXAMPLES))
def test_example_fails_then_passes(lang):
    if not shutil.which(TOOLS[lang]):
        pytest.skip(f"{TOOLS[lang]} not installed here (it is in the Docker image)")
    ex, ans = EXAMPLES[lang], EXAMPLES[lang]["answer"]
    before = run(lang, ex["source"], ans["test_code"], ans["test_setup"])
    after = run(lang, ans["fixed_source"], ans["test_code"], ans["test_setup"])
    assert before.any_failed, before.to_dict()
    assert after.all_passed, after.to_dict()


def test_infinite_loop_is_stopped():
    r = run("python", "while True:\n    pass\n", "def test_x():\n    pass\n", timeout=2)
    assert not r.all_passed and "time limit" in r.results[0]["err"]


def test_compile_error_is_reported():
    r = run("c", "int f(void) { return 1;", "int main(void) { return 0; }")
    assert r.compile_error


def test_unknown_language():
    assert run("cobol", "x", "y").compile_error
