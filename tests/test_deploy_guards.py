"""Public-deployment guards: access code, usage limits, repo size and job cleanup (access.py)."""
import asyncio
import functools
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import FakeBob, git
import access
import config
import main
import orchestrator as o
from test_api_flow import wait


@pytest.fixture
def app_client(monkeypatch, make_repo, tmp_path):
    source = make_repo({"app.py": "print(1)\n"})

    def slow_clone(url, branch, dest, depth=None):
        time.sleep(0.5)  # long enough to see an onboarding "running"
        git(dest.parent, "clone", "-q", str(source), str(dest))
    monkeypatch.setattr(o, "clone_repo", slow_clone)
    bob = FakeBob()
    monkeypatch.setattr(main, "bob", bob)
    monkeypatch.setattr(main.orchestrator, "bob", bob)
    monkeypatch.setattr(main, "usage", access.Usage(tmp_path / "usage.json"))
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.setattr(config, "ACCESS_CODE", "correct-horse-battery")  # secret-scan: allow (test value)
    monkeypatch.setattr(config, "SESSION_SECRET", "test-secret")  # secret-scan: allow (test value)


def test_no_gate_by_default(app_client):
    assert app_client.get("/api/auth/status").json() == {"required": False, "signed_in": True}
    assert app_client.get("/api/jobs").status_code == 200
    health = app_client.get("/api/health").json()
    assert health["hosted"] is False and health["workspace"]


def test_access_code_gate(app_client, gate):
    c = app_client
    assert c.get("/api/jobs").status_code == 401
    assert c.post("/api/repos/clone", json={"repo_url": "https://github.com/o/r"}).status_code == 401
    health = c.get("/api/health")  # health stays open, but hides the clone folder
    assert health.status_code == 200 and health.json()["hosted"] is True and health.json()["workspace"] is None
    assert "\\" not in health.json()["bob_url"] and "/" not in health.json()["bob_url"]  # no server paths
    assert c.get("/api/auth/status").json() == {"required": True, "signed_in": False}

    assert c.post("/api/auth/login", json={"code": "wrong"}).status_code == 401
    assert c.get("/api/jobs").status_code == 401

    r = c.post("/api/auth/login", json={"code": " correct-horse-battery "})
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()
    assert c.get("/api/auth/status").json()["signed_in"] is True
    assert c.get("/api/jobs").status_code == 200

    c.post("/api/auth/logout")
    assert c.get("/api/jobs").status_code == 401


def test_session_tokens(gate, monkeypatch):
    token = access.make_session()
    assert access.session_valid(token)
    expires, sig = token.split(".")
    assert not access.session_valid(f"{int(expires) + 999}.{sig}")        # edited expiry
    assert not access.session_valid(access.make_session(now=time.time() - 8 * 86400))  # expired
    assert not access.session_valid("garbage") and not access.session_valid(None)
    monkeypatch.setattr(config, "ACCESS_CODE", "a-new-code")  # secret-scan: allow (test value)
    assert not access.session_valid(token)  # changing the code signs everyone out


def test_daily_onboarding_limit(app_client, monkeypatch):
    monkeypatch.setattr(config, "MAX_ONBOARDINGS_PER_DAY", 1)
    first = app_client.post("/api/repos/clone", json={"repo_url": "https://github.com/o/r"})
    assert first.status_code == 202
    wait(app_client, first.json()["job_id"])
    second = app_client.post("/api/repos/clone", json={"repo_url": "https://github.com/o/r"})
    assert second.status_code == 429 and "limit of 1 onboardings" in second.json()["detail"]
    limits = app_client.get("/api/health").json()["limits"]
    assert limits["onboardings"] == {"used": 1, "limit": 1}


def test_one_onboarding_at_a_time(app_client, monkeypatch):
    monkeypatch.setattr(config, "MAX_ACTIVE_ONBOARDINGS", 1)
    first = app_client.post("/api/repos/clone", json={"repo_url": "https://github.com/o/r"})
    busy = app_client.post("/api/repos/clone", json={"repo_url": "https://github.com/o/r"})
    assert busy.status_code == 429 and "busy" in busy.json()["detail"]
    wait(app_client, first.json()["job_id"])
    later = app_client.post("/api/repos/clone", json={"repo_url": "https://github.com/o/r"})
    assert later.status_code == 202
    wait(app_client, later.json()["job_id"])


def test_daily_skill_run_limit(app_client, monkeypatch):
    monkeypatch.setattr(config, "MAX_SKILL_RUNS_PER_DAY", 1)
    job = app_client.post("/api/repos/clone", json={"repo_url": "https://github.com/o/r"}).json()
    wait(app_client, job["job_id"])
    ask = lambda: app_client.post(f"/api/repos/{job['repo_id']}/skills/codebase-qa/run",
                                  json={"role": "Backend", "question": "Where does it start?"})
    assert ask().status_code == 202
    wait(app_client, job["job_id"], "codebase-qa")
    second = ask()
    assert second.status_code == 429 and "questions" in second.json()["detail"]


def test_allowed_hosts(app_client, monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_GIT_HOSTS", ["github.com"])
    r = app_client.post("/api/repos/clone", json={"repo_url": "https://gitlab.com/o/r"})
    assert r.status_code == 400 and "github.com" in r.json()["detail"]
    assert access.repo_host("git@github.com:o/r.git") == "github.com"
    assert access.repo_host("ssh://git@GitHub.com:22/o/r") == "github.com"


def test_repo_size_checked_before_clone(monkeypatch):
    monkeypatch.setattr(config, "MAX_REPO_MB", 100)
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"size": 500 * 1024})  # the API reports KB
    monkeypatch.setattr(access.httpx, "AsyncClient",
                        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)))
    with pytest.raises(access.LimitError, match="about 500 MB"):
        asyncio.run(access.check_repo_size("https://github.com/big/repo.git"))
    assert seen == ["/repos/big/repo"]
    asyncio.run(access.check_repo_size("https://gitlab.com/big/repo.git"))  # not GitHub: checked after clone


def test_repo_size_checked_after_clone(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MAX_REPO_MB", 1)
    clone = tmp_path / "clone"
    (clone / "sub").mkdir(parents=True)
    (clone / "sub" / "big.bin").write_bytes(b"0" * (2 * 1024 * 1024))
    with pytest.raises(RuntimeError, match="accepts up to 1 MB"):
        o.check_clone_size(clone)
    assert not clone.exists()


def test_prune_keeps_newest_finished_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WORKSPACE", tmp_path / "ws")
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "ws" / "_jobs")
    orch = o.Orchestrator(main.registry, FakeBob())
    jobs = [orch.create_job(f"https://github.com/o/r{i}.git", None, None) for i in range(4)]
    for i, job in enumerate(jobs):
        job.created_at = 1000 + i
        orch.repo_dir(job).mkdir(parents=True)
        (orch.repo_dir(job) / "f.txt").write_text("x")
    jobs[0].status = o.JobStatus.running  # never delete a running onboarding
    for job in jobs[1:]:
        job.status = o.JobStatus.success

    removed = orch.prune_jobs(2)
    assert removed == [jobs[1].id, jobs[2].id]
    assert set(orch.jobs) == {jobs[0].id, jobs[3].id}
    assert not orch.repo_dir(jobs[1]).exists() and orch.repo_dir(jobs[3]).exists()
    assert not (config.JOBS_DIR / f"{jobs[1].id}.json").exists()
