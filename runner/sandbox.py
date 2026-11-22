"""Run one command with hard limits.

Inside Docker the container itself is the main wall (no internet, memory
and process caps, non-root user). This file adds a second layer: a time
limit, a CPU limit, a cap on file size, and a clean environment.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

try:
    import resource
except ImportError:  # Windows has no resource module; limits then come from Docker only.
    resource = None


@dataclass
class Proc:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def _limits(cpu_seconds: int):
    def apply():
        if resource is None:
            return
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (20 * 1024 * 1024, 20 * 1024 * 1024))
        os.setsid()  # own process group, so a timeout can kill everything the code started
    return apply


def _tmp(cwd: Path) -> Path:
    # A private temp folder inside the run folder. (Go refuses to use a go.mod
    # that sits directly in the temp root, so this can't be cwd itself.)
    t = cwd / ".tmp"
    t.mkdir(exist_ok=True)
    return t


def run_cmd(cmd: list[str], cwd: Path, timeout: float, extra_env: dict | None = None) -> Proc:
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(cwd),
        "LANG": "C.UTF-8",
        "TMPDIR": str(_tmp(cwd)),
    }
    for key in ("JAVA_HOME", "GOROOT"):
        if key in os.environ:
            env[key] = os.environ[key]
    if extra_env:
        env.update(extra_env)
    try:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, preexec_fn=_limits(int(timeout) + 2) if os.name == "posix" else None)
    except FileNotFoundError:
        return Proc(127, "", f"{cmd[0]} is not installed in the runner")
    try:
        out, err = p.communicate(timeout=timeout)
        return Proc(p.returncode, out, err)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, 9)
        except Exception:
            p.kill()
        out, err = p.communicate()
        return Proc(-9, out, err, timed_out=True)
