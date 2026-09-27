"""Model X onboarding backend: runs the Bob skills in skills/ as a pipeline.

Run:  uvicorn main:app --port 8000
      web app at http://127.0.0.1:8000/  (frontend/)    API docs at /docs

  GET  /api/health                              config check (Bob key set? which endpoint?)
  GET  /api/skills                              entry / auto / manual skills + config warnings
  POST /api/repos/clone                         {"repo_url", "branch"?, "depth"?, "confirm"}
                                                -> clone + repo-clone report + all auto skills
  GET  /api/jobs                                all jobs, newest first (summaries, no outputs)
  GET  /api/jobs/{job_id}                       job status and every step's result
  GET  /api/jobs/{job_id}/metrics               measured impact: time to onboard, files, findings, Bob usage
  GET  /api/metrics                             the same, totalled over all onboarded repos
  GET  /api/repos/{repo_id}/results             same, by repo
  POST /api/repos/{repo_id}/skills/{skill}/run  run a manual skill (or retry a failed auto one);
                                                codebase-qa body: {"role": "...", "question": "..."}
  GET  /api/repos/{repo_id}/files               files the skills wrote (onboarding/, README.md)
  GET  /api/repos/{repo_id}/files/{path}        one of those files
  GET  /api/repos/{repo_id}/pack.zip            all of them in one download
  POST /api/repos/{repo_id}/open-in-bob         open the clone in Bob IDE (only from this computer)
"""
import io
import re
import zipfile
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config
from bob_integration import find_bob_ide, make_bob_client, open_in_bob_ide
from orchestrator import (
    ConflictError, Job, Orchestrator, SkillRegistry, SkillTrigger, is_output_path, normalize_repo_url,
)


class RepoCloneRequest(BaseModel):
    repo_url: str
    branch: Optional[str] = None  # None = the repo's default branch (main, master, ...)
    depth: Optional[int] = Field(None, ge=1)  # None = full history
    confirm: bool = True


class SkillRunRequest(BaseModel):
    """Optional input for a manual skill run, e.g. codebase-qa."""
    role: Optional[str] = None
    question: Optional[str] = None


app = FastAPI(title="Model X Onboarding Backend (Bob skill orchestrator)")
app.add_middleware(
    CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"],
)

registry = SkillRegistry(config.SKILLS_DIR)
bob = make_bob_client()
orchestrator = Orchestrator(registry, bob)


def _job_for_repo(repo_id: str) -> Job:
    job = orchestrator.job_for_repo(repo_id)
    if not job:
        raise HTTPException(404, "No job for repo")
    return job


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "bob_client": bob.kind,
        "bob_configured": bob.configured,
        "bob_url": bob.url,
        "bob_ide": bool(find_bob_ide()),
        "workspace": str(config.WORKSPACE),
        "skills": len(registry.skills),
        "warnings": registry.warnings,
    }


@app.get("/api/skills")
def list_skills():
    def dump(trigger: SkillTrigger):
        return [s.model_dump() for s in registry.by_trigger(trigger)]

    return {
        "entry": dump(SkillTrigger.entry),
        "auto": dump(SkillTrigger.auto),
        "manual": dump(SkillTrigger.manual),
        "warnings": registry.warnings,
    }


@app.post("/api/repos/clone", status_code=202)
async def clone_repo_endpoint(req: RepoCloneRequest):
    if not req.confirm:
        raise HTTPException(400, "User must confirm clone")
    try:
        url, branch = normalize_repo_url(req.repo_url, req.branch)
    except ValueError as e:
        raise HTTPException(400, str(e))

    job = orchestrator.create_job(url, branch, req.depth)
    orchestrator.start_pipeline(job)
    return {
        "repo_id": job.repo_id,
        "job_id": job.id,
        "status": job.status,
        "pipeline": list(job.steps),
    }


@app.get("/api/jobs")
def list_jobs():
    """Every job, newest first, without step outputs (for the repo list and activity feed)."""
    jobs = sorted(orchestrator.jobs.values(), key=lambda j: j.created_at, reverse=True)
    return [
        {
            "id": j.id, "repo_id": j.repo_id, "status": j.status, "repo": j.repo,
            "created_at": j.created_at, "updated_at": j.updated_at,
            "steps": {
                name: {
                    "status": s.status, "started_at": s.started_at, "finished_at": s.finished_at,
                    "error": (s.error or "")[:300] or None, "runs": len(s.runs),
                }
                for name, s in j.steps.items()
            },
        }
        for j in jobs
    ]


@app.get("/api/metrics")
def workspace_metrics():
    """Totals over every onboarding in this backend's memory (for the overview and slides)."""
    ms = [orchestrator.metrics(j) for j in orchestrator.jobs.values() if j.repo.cloned]
    done = [m["time_to_onboard_seconds"] for m in ms if m["time_to_onboard_seconds"]]
    return {
        "repos_onboarded": len(done),
        "avg_time_to_onboard_seconds": round(sum(done) / len(done), 1) if done else None,
        "fastest_seconds": min(done) if done else None,
        "files_produced": sum(m["files_produced"] for m in ms),
        "secret_findings": sum(m["secret_findings"]["total"] for m in ms),
        "bob_runs": sum(m["bob"]["runs"] for m in ms),
        "bob_tool_calls": sum(m["bob"]["tool_calls"] for m in ms),
        "bob_cost": round(sum(m["bob"]["cost"] for m in ms), 4),
        "manual_baseline_minutes": config.MANUAL_BASELINE_MINUTES,
    }


@app.get("/api/jobs/{job_id}/metrics")
def job_metrics(job_id: str):
    job = orchestrator.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return orchestrator.metrics(job)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = orchestrator.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/api/repos/{repo_id}/results")
def get_results(repo_id: str):
    return {"repo_id": repo_id, "job": _job_for_repo(repo_id)}


@app.post("/api/repos/{repo_id}/skills/{skill_name}/run", status_code=202)
async def run_skill(repo_id: str, skill_name: str, req: Optional[SkillRunRequest] = None):
    """Optional JSON body, e.g. for codebase-qa: {"role": "Backend", "question": "..."}"""
    job = _job_for_repo(repo_id)
    skill = registry.get(skill_name)
    if not skill:
        raise HTTPException(404, "Skill not found")
    if skill.trigger == SkillTrigger.entry:
        raise HTTPException(400, "repo-clone runs through POST /api/repos/clone")

    try:
        orchestrator.start_skill(job, skill, req.model_dump(exclude_none=True) if req else None)
    except ConflictError as e:
        raise HTTPException(409, str(e))
    return {"job_id": job.id, "skill": skill_name, "status": "started"}


@app.get("/api/repos/{repo_id}/files")
def list_files(repo_id: str):
    _, files = _output_files(_job_for_repo(repo_id))
    return {"repo_id": repo_id, "files": files}


def _output_files(job: Job):
    root = orchestrator.repo_dir(job)
    files = [rel for p in (root / "onboarding").rglob("*") if p.is_file()
             and is_output_path(rel := p.relative_to(root).as_posix())]
    if any("README.md" in step.files_written for step in job.steps.values()):
        files.append("README.md")  # only once a skill has written it
    return root, sorted(files)


@app.get("/api/repos/{repo_id}/pack.zip")
def download_pack(repo_id: str):
    """The onboarding pack: every file the skills wrote, plus metrics, in one zip."""
    job = _job_for_repo(repo_id)
    root, files = _output_files(job)
    if not files:
        raise HTTPException(404, "No onboarding files yet")
    name = re.sub(r"[^A-Za-z0-9._-]", "-", job.repo.url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")) or "repo"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(root / rel, arcname=f"{name}-onboarding/{rel}")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}-onboarding.zip"'})


@app.get("/api/repos/{repo_id}/files/{file_path:path}", response_class=PlainTextResponse)
def get_file(repo_id: str, file_path: str):
    job = _job_for_repo(repo_id)
    if not is_output_path(file_path):
        raise HTTPException(400, "Only onboarding/ files and README.md can be read")
    target = orchestrator.repo_dir(job) / file_path
    if not target.is_file():
        raise HTTPException(404, "File not found")
    return target.read_text(encoding="utf-8", errors="replace")


LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


@app.post("/api/repos/{repo_id}/open-in-bob")
def open_in_bob(repo_id: str, request: Request):
    """Continue in Bob IDE: open the clone, which has our skills and the Codebase Q&A mode in .bob/."""
    job = _job_for_repo(repo_id)
    if not job.repo.cloned:
        raise HTTPException(409, "The repository isn't cloned yet (or the clone failed).")
    if not request.client or request.client.host not in LOCAL_HOSTS:
        raise HTTPException(403, "Bob IDE can only be opened from the computer running the backend.")
    folder = orchestrator.repo_dir(job)
    try:
        open_in_bob_ide(folder)
    except RuntimeError as e:
        raise HTTPException(404, str(e))
    return {"opened": str(folder.resolve())}


# The web app (frontend/), served at / . Mounted last so every /api route wins.
FRONTEND_DIR = config.BASE_DIR / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
