"""Model X onboarding backend: runs the Bob skills in skills/ as a pipeline.

Run:  uvicorn main:app --port 8000
      web app at http://127.0.0.1:8000/  (frontend/)    API docs at /docs

  GET  /api/health                              config check (Bob key set? which endpoint?)
  GET  /api/skills                              entry / auto / manual skills + config warnings
  POST /api/repos/clone                         {"repo_url", "branch"?, "depth"?, "confirm"}
                                                -> clone + repo-clone report + all auto skills
  GET  /api/jobs                                all jobs, newest first (summaries, no outputs)
  GET  /api/jobs/{job_id}                       job status and every step's result
  GET  /api/repos/{repo_id}/results             same, by repo
  POST /api/repos/{repo_id}/skills/{skill}/run  run a manual skill (or retry a failed auto one);
                                                codebase-qa body: {"role": "...", "question": "..."}
  GET  /api/repos/{repo_id}/files               files the skills wrote (onboarding/, README.md)
  GET  /api/repos/{repo_id}/files/{path}        one of those files
"""
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config
from bob_integration import make_bob_client
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
    job = _job_for_repo(repo_id)
    root = orchestrator.repo_dir(job)
    files = [rel for p in (root / "onboarding").rglob("*") if p.is_file()
             and is_output_path(rel := p.relative_to(root).as_posix())]
    if any("README.md" in step.files_written for step in job.steps.values()):
        files.append("README.md")  # only once a skill has written it
    return {"repo_id": repo_id, "files": sorted(files)}


@app.get("/api/repos/{repo_id}/files/{file_path:path}", response_class=PlainTextResponse)
def get_file(repo_id: str, file_path: str):
    job = _job_for_repo(repo_id)
    if not is_output_path(file_path):
        raise HTTPException(400, "Only onboarding/ files and README.md can be read")
    target = orchestrator.repo_dir(job) / file_path
    if not target.is_file():
        raise HTTPException(404, "File not found")
    return target.read_text(encoding="utf-8", errors="replace")


# The web app (frontend/), served at / . Mounted last so every /api route wins.
FRONTEND_DIR = config.BASE_DIR / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
