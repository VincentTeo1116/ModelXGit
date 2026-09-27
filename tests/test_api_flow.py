"""The whole flow over HTTP with a fake Bob: clone -> pipeline -> Q&A -> README -> files -> restart."""
import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from conftest import FakeBob, git
import main
import orchestrator as o


@pytest.fixture
def client(monkeypatch, make_repo):
    source = make_repo({"app.py": "def run():\n    return 1\n", ".env.example": "API_KEY=\n"})
    monkeypatch.setattr(o, "clone_repo", lambda url, branch, dest, depth=None: git(dest.parent, "clone", "-q", str(source), str(dest)))
    bob = FakeBob(fail={"setup-dependencies": 1})  # one temporary failure: retried
    monkeypatch.setattr(main, "bob", bob)
    monkeypatch.setattr(main.orchestrator, "bob", bob)
    with TestClient(main.app) as c:
        c.bob = bob
        yield c


def wait(c, job_id, step=None, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job_id}").json()
        s = j["steps"].get(step) if step else None
        if (step and s and s["status"] not in ("pending", "running")) or (not step and j["status"] not in ("queued", "running")):
            return j
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_full_flow(client):
    c = client
    assert c.get("/api/health").json()["skills"] == 8
    assert c.post("/api/repos/clone", json={"repo_url": "file:///x"}).status_code == 400
    assert c.post("/api/repos/clone", json={"repo_url": "https://github.com/a/b.git", "confirm": False}).status_code == 400

    r = c.post("/api/repos/clone", json={"repo_url": "https://github.com/example/demo.git"})
    assert r.status_code == 202
    job_id, repo_id = r.json()["job_id"], r.json()["repo_id"]
    early = c.post(f"/api/repos/{repo_id}/skills/readme-generator/run")
    assert early.status_code in (409, 202)  # refused until its dependencies finish (or the clone is done)
    j = wait(c, job_id)
    if early.status_code == 202:
        wait(c, job_id, "readme-generator")
    steps = j["steps"]
    assert all(steps[n]["status"] == "success" for n in main.orchestrator.pipeline_names)
    assert "retried" in " ".join(steps["setup-dependencies"]["output"].get("warnings", []))
    assert steps["architecture-diagram"]["started_at"] >= steps["tech-stack-detection"]["finished_at"] - 0.05
    assert j["bob_ide"] and len(j["bob_ide"]["skills"]) == 8

    assert c.post(f"/api/repos/{repo_id}/skills/codebase-qa/run", json={"role": "Backend", "question": "Q?"}).status_code == 202
    qa = wait(c, job_id, "codebase-qa")["steps"]["codebase-qa"]
    assert qa["output"]["answer"].startswith("Answer to Q?")
    assert c.post(f"/api/repos/{repo_id}/skills/readme-generator/run").status_code == 202
    assert wait(c, job_id, "readme-generator")["steps"]["readme-generator"]["status"] == "success"

    assert c.post(f"/api/repos/{repo_id}/skills/tech-stack-detection/run").status_code == 409
    assert c.post(f"/api/repos/{repo_id}/skills/repo-clone/run").status_code == 400
    assert c.post(f"/api/repos/{repo_id}/skills/nope/run").status_code == 404
    assert c.post(f"/api/repos/{repo_id}/open-in-bob").status_code == 403  # not from this computer

    files = c.get(f"/api/repos/{repo_id}/files").json()["files"]
    assert "README.md" in files and "onboarding/metrics.json" in files and not any(".context" in f for f in files)
    assert c.get(f"/api/repos/{repo_id}/files/app.py").status_code == 400
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/repos/{repo_id}/pack.zip").content))
    assert len(z.namelist()) == len(files)

    m = c.get(f"/api/jobs/{job_id}/metrics").json()
    assert m["pipeline_succeeded"] and m["time_to_onboard_seconds"] is not None
    assert m["outputs_checked"] == m["outputs_valid"] > 0 and m["manual_baseline"] is None
    assert c.get("/api/metrics").json()["repos_onboarded"] >= 1

    reloaded = o.Orchestrator(main.registry, c.bob)  # a backend restart
    assert reloaded.jobs[job_id].status == "success"
