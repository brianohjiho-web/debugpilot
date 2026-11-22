"""How to build and test code in each language.

Every language follows the same idea: write the user's file and the test
into a fresh temp folder, compile if needed, run the test, and report each
test as passed or failed. The rules the AI must follow when it writes a
test live in TEST_RULES, so the prompt and the runner never drift apart.
"""

from __future__ import annotations

import json
import os
import re
import signal
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .sandbox import run_cmd

OUTPUT_LIMIT = 4000

TEST_RULES = {
    "python": (
        "Python. Write one or more functions named test_<something> that use plain assert. "
        "The file's functions are already imported (from solution import *), so call them directly."
    ),
    "javascript": (
        "JavaScript (Node). Write one or more functions declared as `function test_<something>() { ... }`. "
        "Use assert(condition, message), which is already defined. The file's functions are in scope; "
        "do not use import or require."
    ),
    "sql": (
        "SQLite. The rows returned by the file's LAST SELECT are saved into a table named result. "
        "The test is SQL ending in ONE SELECT that returns a single row with a column named ok "
        "that is 1 when correct and 0 or NULL when not. If the file only has a query and no tables, "
        "also return test_setup: SQL that creates the tables and a few sample rows."
    ),
    "c": (
        "C11. Write a complete test program with int main(void). The user's file is already included "
        "above your code (#include \"solution.c\"), so call its functions directly and don't redefine them. "
        "Return 0 when the test passes and 1 when it fails; print a short reason with printf before returning 1."
    ),
    "cpp": (
        "C++17. Write a complete test program with int main(). The user's file is already included above "
        "your code (#include \"solution.cpp\"), so call its functions directly and don't redefine them. "
        "Return 0 when the test passes and 1 when it fails; print a short reason before returning 1."
    ),
    "java": (
        "Java 17. Write a class named TestMain with public static void main(String[] args). "
        "The user's file is compiled next to it, so use its classes directly. "
        "Call System.exit(1) with a printed reason when the test fails; exit normally when it passes."
    ),
    "go": (
        "Go. Write a _test.go file using the testing package: one or more func TestXxx(t *testing.T). "
        "Use the same package name as the user's file. Use only the standard library."
    ),
    "r": (
        "R. Write one or more functions named test_<something> <- function() { ... } that use stopifnot(). "
        "The user's file is already sourced, so call its functions directly. Base R only."
    ),
}

LABELS = {
    "python": "Python", "javascript": "JavaScript", "sql": "SQL (SQLite)", "c": "C",
    "cpp": "C++", "java": "Java", "go": "Go", "r": "R",
}


@dataclass
class RunResult:
    compile_error: str | None = None
    results: list[dict] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0

    @property
    def all_passed(self) -> bool:
        return self.compile_error is None and bool(self.results) and all(r["ok"] for r in self.results)

    @property
    def any_failed(self) -> bool:
        return self.compile_error is not None or any(not r["ok"] for r in self.results)

    def to_dict(self) -> dict:
        return {
            "compile_error": self.compile_error,
            "results": self.results,
            "stdout": self.stdout[-OUTPUT_LIMIT:],
            "stderr": self.stderr[-OUTPUT_LIMIT:],
            "duration_ms": self.duration_ms,
            "all_passed": self.all_passed,
        }


def _clean(text: str) -> str:
    # Java prints this on every start when the env sets proxy options; it's noise for users.
    lines = [l for l in text.splitlines() if not l.startswith("Picked up JAVA_TOOL_OPTIONS")]
    return "\n".join(lines).strip()


def _signal_name(code: int) -> str:
    if code < 0:
        try:
            return f"crashed with {signal.Signals(-code).name}"
        except ValueError:
            return f"crashed with signal {-code}"
    return f"exit code {code}"


def _single(ok: bool, name: str, err: str) -> list[dict]:
    return [{"name": name, "ok": ok, "err": "" if ok else err[-600:]}]


# ---------- Python ----------
PY_HARNESS = r'''
import json, sys, traceback
sys.path.insert(0, ".")
out = []
try:
    import solution
    ns = {k: getattr(solution, k) for k in dir(solution) if not k.startswith("__")}
    test_src = open("test_solution.py").read()
    exec(compile(test_src, "test_solution.py", "exec"), ns)
except Exception as e:
    print("@@RESULT@@" + json.dumps({"load_error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()}))
    sys.exit(0)
for name in sorted(k for k in ns if k.startswith("test_") and callable(ns[k])):
    try:
        ns[name]()
        out.append({"name": name, "ok": True, "err": ""})
    except AssertionError as e:
        out.append({"name": name, "ok": False, "err": "AssertionError" + (f": {e}" if str(e) else "")})
    except Exception as e:
        out.append({"name": name, "ok": False, "err": f"{type(e).__name__}: {e}"})
print("@@RESULT@@" + json.dumps({"results": out}))
'''

# ---------- JavaScript ----------
JS_PRELUDE = (
    "const assert=(c,m)=>{if(!c){const e=new Error(m||'Assertion failed');e.name='AssertionError';throw e;}};\n"
)

# ---------- SQL (runs through Python's sqlite3 so the results match the browser demo) ----------
SQL_HARNESS = r'''
import json, sqlite3, sys

def split_sql(text):
    stmts, buf = [], ""
    for line in text.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                stmts.append(buf)
            buf = ""
    if buf.strip():
        stmts.append(buf)
    return stmts

def emit(obj):
    print("@@RESULT@@" + json.dumps(obj)); sys.exit(0)

db = sqlite3.connect(":memory:")
setup, src, test = (open(f).read() for f in ("setup.sql", "solution.sql", "test.sql"))
try:
    if setup.strip():
        db.executescript(setup)
except Exception as e:
    emit({"load_error": f"test setup: {e}"})
last_cols, last_rows = None, None
try:
    for st in split_sql(src):
        cur = db.execute(st)
        if cur.description:
            last_cols = [d[0] for d in cur.description]
            last_rows = cur.fetchall()
except Exception as e:
    emit({"results": [{"name": "sql_check", "ok": False, "err": f"Your SQL failed: {e}"}]})
if last_cols:
    cols = ",".join('"' + (c or f"col{i}").replace('"', '""') + '"' for i, c in enumerate(last_cols))
    db.execute("DROP TABLE IF EXISTS result")
    db.execute(f"CREATE TABLE result ({cols})")
    db.executemany(f"INSERT INTO result VALUES ({','.join('?' * len(last_cols))})", last_rows)
if not test.strip():
    emit({"results": [{"name": "sql_check", "ok": True, "err": ""}]})
try:
    row, cols = None, None
    for st in split_sql(test):
        cur = db.execute(st)
        if cur.description:
            cols = [d[0].lower() for d in cur.description]
            row = cur.fetchone()
except Exception as e:
    emit({"load_error": f"test: {e}"})
v = row[cols.index("ok")] if row and cols and "ok" in cols else None
ok = v == 1
emit({"results": [{"name": "sql_check", "ok": ok, "err": "" if ok else f"check returned {v!r} (expected 1)"}]})
'''

# ---------- R ----------
R_HARNESS = r'''
res <- tryCatch({ source("solution.R"); source("test.R"); NULL },
                error = function(e) paste("load error:", conditionMessage(e)))
if (!is.null(res)) { cat("@@LOAD@@", res, "\n", sep = ""); quit(status = 0) }
for (n in sort(ls(pattern = "^test_"))) {
  f <- get(n)
  if (!is.function(f)) next
  msg <- tryCatch({ f(); "" }, error = function(e) conditionMessage(e))
  cat("@@TEST@@", n, "\t", if (nchar(msg) == 0) "1" else "0", "\t", gsub("[\t\n]", " ", msg), "\n", sep = "")
}
'''


def _parse_marker(stdout: str) -> dict | None:
    for line in stdout.splitlines()[::-1]:
        if line.startswith("@@RESULT@@"):
            return json.loads(line[len("@@RESULT@@"):])
    return None


def run(language: str, source: str, test: str, setup: str = "", timeout: float = 10.0) -> RunResult:
    if language not in TEST_RULES:
        return RunResult(compile_error=f"Unsupported language: {language}")
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="gw-run-") as tmp:
        d = Path(tmp)
        rr = _dispatch(language, d, source, test, setup, timeout)
    rr.duration_ms = int((time.monotonic() - t0) * 1000)
    rr.stdout, rr.stderr = _clean(rr.stdout), _clean(rr.stderr)
    if rr.compile_error:
        rr.compile_error = _clean(rr.compile_error)[-1500:]
    return rr


def _dispatch(lang: str, d: Path, source: str, test: str, setup: str, timeout: float) -> RunResult:
    if lang == "python":
        (d / "solution.py").write_text(source)
        (d / "test_solution.py").write_text(test)
        (d / "harness.py").write_text(PY_HARNESS)
        p = run_cmd(["python3", "-I", "harness.py"], d, timeout)
        return _from_marker(p)

    if lang == "javascript":
        names = re.findall(r"function\s+(test_\w+)", test)
        clean = re.sub(r"^\s*export\s+(default\s+)?", "", source, flags=re.M)
        footer = (
            "\n;const __r=[];\n"
            + "".join(
                f"try{{{n}();__r.push({{name:{json.dumps(n)},ok:true,err:''}})}}"
                f"catch(e){{__r.push({{name:{json.dumps(n)},ok:false,err:(e&&e.name?e.name+': '+e.message:String(e))}})}}\n"
                for n in names
            )
            + "console.log('@@RESULT@@'+JSON.stringify({results:__r}));\n"
        )
        (d / "run.js").write_text(JS_PRELUDE + clean + "\n;\n" + test + footer)
        p = run_cmd(["node", "run.js"], d, timeout)
        rr = _from_marker(p)
        if rr.compile_error is None and not rr.results and p.returncode != 0:
            rr.compile_error = p.stderr or _signal_name(p.returncode)
        return rr

    if lang == "sql":
        (d / "setup.sql").write_text(setup or "")
        (d / "solution.sql").write_text(source)
        (d / "test.sql").write_text(test)
        (d / "harness.py").write_text(SQL_HARNESS)
        return _from_marker(run_cmd(["python3", "-I", "harness.py"], d, timeout))

    if lang in ("c", "cpp"):
        ext, cc, std = ("c", "gcc", "-std=c11") if lang == "c" else ("cpp", "g++", "-std=c++17")
        (d / f"solution.{ext}").write_text(source)
        (d / f"test.{ext}").write_text(f'#include "solution.{ext}"\n' + test)
        comp = run_cmd([cc, std, "-O0", "-g", "-o", "test_bin", f"test.{ext}", "-lm"], d, 30)
        if comp.returncode != 0:
            return RunResult(compile_error=comp.stderr or comp.stdout, stdout=comp.stdout, stderr=comp.stderr)
        p = run_cmd(["./test_bin"], d, timeout)
        ok = p.returncode == 0 and not p.timed_out
        err = "timed out" if p.timed_out else (p.stdout.strip() or p.stderr.strip() or "") + f" ({_signal_name(p.returncode)})"
        return RunResult(results=_single(ok, "main", err.strip()), stdout=p.stdout, stderr=p.stderr)

    if lang == "java":
        m = re.search(r"public\s+(?:final\s+|abstract\s+)*(?:class|interface|enum|record)\s+(\w+)", source)
        fname = (m.group(1) if m else "Solution") + ".java"
        if fname == "TestMain.java":
            fname = "Solution.java"
        (d / fname).write_text(source)
        (d / "TestMain.java").write_text(test)
        (d / "out").mkdir()
        comp = run_cmd(["javac", "-d", "out", fname, "TestMain.java"], d, 60)
        if comp.returncode != 0:
            return RunResult(compile_error=comp.stderr or comp.stdout, stdout=comp.stdout, stderr=comp.stderr)
        p = run_cmd(["java", "-ea", "-Xss8m", "-cp", "out", "TestMain"], d, timeout)
        ok = p.returncode == 0 and not p.timed_out
        err = "timed out" if p.timed_out else (_clean(p.stderr) or p.stdout.strip() or _signal_name(p.returncode))
        return RunResult(results=_single(ok, "TestMain", err), stdout=p.stdout, stderr=p.stderr)

    if lang == "go":
        pkg = re.search(r"^\s*package\s+(\w+)", source, re.M)
        pkg = pkg.group(1) if pkg else "main"
        test_fixed = re.sub(r"^\s*package\s+\w+", f"package {pkg}", test, count=1, flags=re.M)
        if not re.search(r"^\s*package\s+", test_fixed, re.M):
            test_fixed = f"package {pkg}\n\n" + test_fixed
        (d / "go.mod").write_text("module solution\n\ngo 1.21\n")
        (d / "solution.go").write_text(source)
        (d / "solution_test.go").write_text(test_fixed)
        env = {"GOFLAGS": "-mod=mod", "GOPROXY": "off", "GOTOOLCHAIN": "local",
               "GOCACHE": os.environ.get("GW_GOCACHE", "/tmp/gw-gocache"), "CGO_ENABLED": "0"}
        p = run_cmd(["go", "test", "-v", "-count=1", "."], d, max(timeout, 60), extra_env=env)
        out = p.stdout + "\n" + p.stderr
        results = []
        for status, name in re.findall(r"^\s*--- (PASS|FAIL): (\w+)", out, re.M):
            err = ""
            if status == "FAIL":
                # Collect the indented lines Go prints under a failing test.
                block = re.search(rf"--- FAIL: {name}.*?\n((?:\s{{4,}}.*\n?)*)", out)
                err = (block.group(1).strip() if block else "")
                if not err:
                    panic = re.search(r"panic: .*", out)
                    err = panic.group(0) if panic else "failed"
            results.append({"name": name, "ok": status == "PASS", "err": err[-600:]})
        if not results:
            panic = re.search(r"panic: .*", out)
            if p.returncode != 0 and ("[build failed]" in out or "[setup failed]" in out or not panic):
                return RunResult(compile_error=out.strip() or _signal_name(p.returncode), stdout=p.stdout, stderr=p.stderr)
            results = _single(False, "go test", panic.group(0) if panic else _signal_name(p.returncode))
        return RunResult(results=results, stdout=p.stdout, stderr=p.stderr)

    if lang == "r":
        (d / "solution.R").write_text(source)
        (d / "test.R").write_text(test)
        (d / "harness.R").write_text(R_HARNESS)
        p = run_cmd(["Rscript", "--vanilla", "harness.R"], d, max(timeout, 20))
        load = re.search(r"@@LOAD@@(.*)", p.stdout)
        if load:
            return RunResult(compile_error=load.group(1).strip(), stdout=p.stdout, stderr=p.stderr)
        results = [{"name": n, "ok": ok == "1", "err": "" if ok == "1" else msg}
                   for n, ok, msg in re.findall(r"@@TEST@@(\w+)\t([01])\t(.*)", p.stdout)]
        if not results:
            return RunResult(compile_error=p.stderr or "No test_ functions found", stdout=p.stdout, stderr=p.stderr)
        return RunResult(results=results, stdout=p.stdout, stderr=p.stderr)

    return RunResult(compile_error=f"Unsupported language: {lang}")


def _from_marker(p) -> RunResult:
    if p.timed_out:
        return RunResult(results=_single(False, "run", "Stopped after the time limit. The code may loop forever."),
                         stdout=p.stdout, stderr=p.stderr)
    data = _parse_marker(p.stdout)
    if data is None:
        return RunResult(compile_error=p.stderr or _signal_name(p.returncode), stdout=p.stdout, stderr=p.stderr)
    if "load_error" in data:
        return RunResult(compile_error=data["load_error"], stdout=p.stdout, stderr=p.stderr)
    return RunResult(results=data["results"], stdout=p.stdout, stderr=p.stderr)
