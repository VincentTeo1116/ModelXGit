"""Shared test setup: no Bob key, no network. Settings are set before the app modules load."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_ws = tempfile.mkdtemp(prefix="modelx-tests-")
os.environ.update({
    "WORKSPACE_DIR": _ws,
    "BOB_CLIENT": "http",
    "BOB_API_KEY": "test-only",  # secret-scan: allow (not a key)
    "BOB_API_ENDPOINT": "http://127.0.0.1:9",
    "BOB_RETRY_DELAY": "0.01",
    "MANUAL_BASELINE_MINUTES": "",
})
for key in ("BOB_ACCEPT_LICENSE", "BOB_TEAM_ID", "JOBS_DIR"):
    os.environ.pop(key, None)

SCANNER = ROOT / "skills" / "secret-precommit-scanner" / "scripts"
HISTORY = ROOT / "skills" / "git-history-secret-audit" / "scripts" / "history_scan.py"


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "init.defaultBranch=main", *args],
                          cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")


@pytest.fixture
def make_repo(tmp_path):
    """make_repo({"path": "content", ...}) -> a git repo with one commit."""
    def _make(files, name="repo"):
        repo = tmp_path / name
        repo.mkdir()
        git(repo, "init", "-q")
        for rel, content in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(content, encoding="utf-8")
        git(repo, "add", "-f", ".")
        git(repo, "commit", "-qm", "initial")
        return repo
    return _make


class FakeBob:
    """Stands in for Bob: returns contract-shaped files for whatever the skill expects."""
    kind, reads_workspace, configured, url = "http", False, True, "fake://bob"

    def __init__(self, fail=None):
        self.calls, self.fail = [], dict(fail or {})

    async def run_skill(self, name, skill_md, context):
        self.calls.append(name)
        if self.fail.get(name):
            self.fail[name] -= 1
            raise RuntimeError("Bob API unreachable at fake://bob")
        import orchestrator as o
        blank = {str: "x", int: 0, dict: {}, list: []}
        files = {}
        for path in context["orchestrator"]["expected_files"]:
            if path.endswith(".json"):
                data = {k: blank[t] for k, t in o.OUTPUT_CONTRACTS.get(path, {}).items()}
                if path.endswith("architecture.json"):
                    data["nodes"] = [{"id": "api", "label": "API", "layer": "api"}]
                files[path] = data
            else:
                files[path] = f"# {name}\n"
        q = (context.get("user_input") or {}).get("question")
        return {"summary": f"{name} done", "files": files,
                "answer": f"Answer to {q}\n\n**You might ask next:**\n1. Next?" if q else None}
