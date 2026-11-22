"""The runner as its own small web service.

The agent never runs user code itself. It sends code here, and this
service runs it inside a locked-down container (see docker-compose.yml).
"""

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .languages import LABELS, TEST_RULES, run

app = FastAPI(title="DebugPilot sandbox runner")


class RunRequest(BaseModel):
    language: str
    source: str = Field(max_length=100_000)
    test: str = Field(max_length=100_000)
    setup: str = Field(default="", max_length=100_000)
    timeout: float = Field(default=10.0, ge=1, le=30)


@app.get("/health")
def health():
    return {"ok": True, "languages": list(TEST_RULES)}


@app.get("/languages")
def languages():
    return {k: {"label": LABELS[k], "test_rules": TEST_RULES[k]} for k in TEST_RULES}


@app.post("/run")
def run_code(req: RunRequest):
    return run(req.language, req.source, req.test, req.setup, req.timeout).to_dict()
