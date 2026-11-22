"""Prompts sent to Claude. Kept in one place so they're easy to read and tune."""

from runner.languages import LABELS, TEST_RULES

FENCE = {"python": "python", "javascript": "javascript", "sql": "sql", "c": "c",
         "cpp": "cpp", "java": "java", "go": "go", "r": "r"}

JSON_SHAPE = """Return ONLY a JSON object, no prose before or after it:
{
  "category": "code_bug" or "not_code",
  "summary": "1-2 plain-English sentences a non-engineer understands",
  "root_cause": "2-3 sentences naming the function and the exact cause",
  "next_steps": ["3 short steps"],
  "confidence": integer 0-100,
  "test_code": "the test, following the test rules exactly",
  "test_setup": "SQL only: tables and sample rows the file needs, else empty string",
  "fixed_source": "the FULL corrected file; smallest change that fixes it; keep everything else identical",
  "pr_title": "short title for the fix"
}
Use category "not_code" only when the code is correct and the problem is bad input data or the environment
(for example a broken sensor sending impossible values). Then leave test_code and fixed_source empty and
explain what should be checked instead in next_steps."""


def first_prompt(language: str, source: str, error: str, load_error: str | None) -> str:
    lab, fence = LABELS[language], FENCE[language]
    parts = [
        f"You are a debugging agent. Find and fix the bug in this {lab} file.",
        f"FILE:\n```{fence}\n{source}\n```",
        ("ERROR THE USER REPORTED:\n" + error.strip()) if error.strip() else
        "The user didn't paste an error. Find the most likely crash or wrong-result bug "
        "(empty input, missing field, null, divide by zero, index out of range, NULL in SQL math) and fix that one.",
    ]
    if load_error:
        parts.append("WHEN WE TRIED TO LOAD/COMPILE THE FILE AS-IS, IT FAILED WITH:\n" + load_error)
    parts.append("TEST RULES (your test is run automatically, so follow these exactly):\n" + TEST_RULES[language])
    parts.append("The test must FAIL on the current file because of this bug, and PASS on your fixed file. "
                 "Also include a check that normal input still works, so the fix doesn't break anything.")
    parts.append(JSON_SHAPE)
    return "\n\n".join(parts)


def retry_prompt(language: str, problem: str, before: dict | None, after: dict | None) -> str:
    def show(label, r):
        if not r:
            return ""
        if r.get("compile_error"):
            return f"{label}: did not compile/load: {r['compile_error'][:1500]}"
        rows = "\n".join(f"  {x['name']}: {'pass' if x['ok'] else 'FAIL ' + x['err'][:300]}" for x in r.get("results", []))
        return f"{label}:\n{rows}"

    return "\n\n".join(filter(None, [
        "We ran your answer in the sandbox and it isn't proven yet.",
        problem,
        show("Your test on the ORIGINAL file", before),
        show("Your test on your FIXED file", after),
        "Fix whatever is wrong (the test, the fix, or both) and answer again with the full JSON object. "
        "Remember the test rules:\n" + TEST_RULES[language],
    ]))
