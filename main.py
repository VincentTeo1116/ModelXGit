import os
import re
import uuid
import time
import asyncio
import yaml
import httpx
from pathlib import Path
from typing import Dict, List, Optional, Any
from enum import Enum

from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel


# -------------------------------------------------------------------
# Environment
# -------------------------------------------------------------------
load_dotenv()  # reads backend/.env

BOB_API_KEY = os.getenv("BOB_API_KEY")
BOB_API_ENDPOINT = os.getenv("BOB_API_ENDPOINT", "https://api.us-east.bob.ibm.com")

if not BOB_API_KEY:
    raise RuntimeError(
        "BOB_API_KEY is not set. Add it to backend/.env before starting the server."
    )


# -------------------------------------------------------------------
# Models
# -------------------------------------------------------------------
class SkillTrigger(str, Enum):
    auto = "auto"
    manual = "manual"


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"


class SkillInfo(BaseModel):
    name: str
    description: str
    trigger: SkillTrigger
    after: Optional[str] = None
    depends_on: List[str] = []
    parallel_group: Optional[str] = None
    path: str


class RepoCloneRequest(BaseModel):
    repo_url: str
    branch: Optional[str] = "main"
    confirm: bool = True


class JobStep(BaseModel):
    skill: str
    status: JobStatus
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


class Job(BaseModel):
    id: str
    repo_id: str
    status: JobStatus
    steps: Dict[str, JobStep] = {}
    created_at: float
    updated_at: float


# -------------------------------------------------------------------
# Skill Loader
# -------------------------------------------------------------------
SKILLS_DIR = Path(__file__).parent / "skills"


def parse_skill_md(path: Path) -> SkillInfo:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        raise ValueError(f"No frontmatter in {path}")
    meta = yaml.safe_load(m.group(1)) or {}

    # Support both flat fields and x-orchestrator namespace
    orch = meta.get("x-orchestrator", meta)

    return SkillInfo(
        name=meta.get("name", path.parent.name),
        description=meta.get("description", ""),
        trigger=SkillTrigger(orch.get("trigger", "manual")),
        after=orch.get("after"),
        depends_on=orch.get("depends_on", []) or [],
        parallel_group=orch.get("parallel_group"),
        path=str(path),
    )


class SkillRegistry:
    def __init__(self):
        self.skills: Dict[str, SkillInfo] = {}
        self._load()

    def _load(self):
        for skill_md in SKILLS_DIR.glob("*/SKILL.md"):
            info = parse_skill_md(skill_md)
            self.skills[info.name] = info

    def get_auto_skills_after(self, event: str) -> List[SkillInfo]:
        return [
            s
            for s in self.skills.values()
            if s.trigger == SkillTrigger.auto and s.after == event
        ]

    def get_manual_skills(self) -> List[SkillInfo]:
        return [s for s in self.skills.values() if s.trigger == SkillTrigger.manual]

    def get(self, name: str) -> SkillInfo:
        return self.skills[name]


# -------------------------------------------------------------------
# Bob 2.0 Client
# -------------------------------------------------------------------
class BobClient:
    """
    Thin async client around the IBM Bob 2.0 API.

    Reads BOB_API_KEY and BOB_API_ENDPOINT from environment.
    Sends the SKILL.md content plus a JSON context to Bob and
    returns Bob's JSON response.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_endpoint: Optional[str] = None,
        timeout: float = 300.0,
    ):
        self.api_key = api_key or BOB_API_KEY
        self.api_endpoint = (api_endpoint or BOB_API_ENDPOINT).rstrip("/")
        self.timeout = timeout

        if not self.api_key:
            raise ValueError("BOB_API_KEY missing")

    async def run_skill(
        self, skill: SkillInfo, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute a SKILL.md through Bob 2.0.

        Adjust the URL path and payload shape to match the Bob 2.0
        API spec you were given at the hackathon.
        """
        skill_path = Path(skill.path)
        skill_content = skill_path.read_text(encoding="utf-8")

        payload = {
            "skill": skill_content,
            "skill_name": skill.name,
            "context": context,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Bob's edge may require a known User-Agent
            "User-Agent": "ibm-bob-openwiki-provider",
        }

        url = f"{self.api_endpoint}/inference/v1/skills/run"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                body = e.response.text[:1000] if e.response is not None else ""
                raise RuntimeError(
                    f"Bob API error {e.response.status_code}: {body}"
                ) from e
            except httpx.RequestError as e:
                raise RuntimeError(f"Bob API unreachable: {e}") from e


# -------------------------------------------------------------------
# Repo Manager
# -------------------------------------------------------------------
WORKSPACE = Path("/tmp/hackathon_workspaces")
WORKSPACE.mkdir(parents=True, exist_ok=True)


async def clone_repo(repo_url: str, branch: str = "main") -> str:
    repo_id = str(uuid.uuid4())[:8]
    dest = WORKSPACE / repo_id
    dest.mkdir(parents=True, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        "git",
        "clone",
        "--branch",
        branch,
        "--depth",
        "1",
        repo_url,
        str(dest),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"Git clone failed: {stderr.decode()}")

    return repo_id


# -------------------------------------------------------------------
# Job Manager
# -------------------------------------------------------------------
class JobManager:
    def __init__(self):
        self.jobs: Dict[str, Job] = {}

    def create_job(self, repo_id: str) -> Job:
        job_id = str(uuid.uuid4())[:8]
        job = Job(
            id=job_id,
            repo_id=repo_id,
            status=JobStatus.pending,
            created_at=time.time(),
            updated_at=time.time(),
        )
        self.jobs[job_id] = job
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def update_step(
        self,
        job_id: str,
        skill: str,
        status: JobStatus,
        output: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ):
        job = self.jobs[job_id]
        step = job.steps.get(skill) or JobStep(skill=skill, status=status)

        step.status = status
        if output:
            step.output = output
        if error:
            step.error = error
        if status == JobStatus.running and not step.started_at:
            step.started_at = time.time()
        if status in (JobStatus.success, JobStatus.failed):
            step.finished_at = time.time()

        job.steps[skill] = step
        job.updated_at = time.time()

        # Recompute overall job status
        statuses = [s.status for s in job.steps.values()]
        if statuses and all(s == JobStatus.success for s in statuses):
            job.status = JobStatus.success
        elif any(s == JobStatus.failed for s in statuses):
            job.status = JobStatus.failed
        elif any(s == JobStatus.running for s in statuses):
            job.status = JobStatus.running
        else:
            job.status = JobStatus.pending


# -------------------------------------------------------------------
# Skill Executor
# -------------------------------------------------------------------
class SkillExecutor:
    def __init__(self, registry: SkillRegistry, bob: BobClient, jobs: JobManager):
        self.registry = registry
        self.bob = bob
        self.jobs = jobs

    async def run_auto_skills(self, job_id: str, repo_id: str, event: str):
        skills = self.registry.get_auto_skills_after(event)
        ordered = self._topo_sort(skills)

        # Group by parallel_group; run groups sequentially, items within a
        # group concurrently.
        groups: Dict[str, List[SkillInfo]] = {}
        for s in ordered:
            key = s.parallel_group or f"__solo_{s.name}"
            groups.setdefault(key, []).append(s)

        for group_skills in groups.values():
            await asyncio.gather(
                *[self._run_skill(job_id, repo_id, s) for s in group_skills]
            )

    async def run_manual_skill(self, job_id: str, repo_id: str, skill_name: str):
        skill = self.registry.get(skill_name)
        await self._run_skill(job_id, repo_id, skill)

    async def _run_skill(self, job_id: str, repo_id: str, skill: SkillInfo):
        self.jobs.update_step(job_id, skill.name, JobStatus.running)
        try:
            context = {
                "repo_id": repo_id,
                "repo_path": str(WORKSPACE / repo_id),
                "job_id": job_id,
                "skill_name": skill.name,
            }
            output = await self.bob.run_skill(skill, context)
            self.jobs.update_step(job_id, skill.name, JobStatus.success, output=output)
        except Exception as e:
            self.jobs.update_step(job_id, skill.name, JobStatus.failed, error=str(e))

    def _topo_sort(self, skills: List[SkillInfo]) -> List[SkillInfo]:
        result: List[SkillInfo] = []
        visited = set()

        def visit(s: SkillInfo):
            if s.name in visited:
                return
            for dep_name in s.depends_on:
                dep = next((x for x in skills if x.name == dep_name), None)
                if dep:
                    visit(dep)
            visited.add(s.name)
            result.append(s)

        for s in skills:
            visit(s)
        return result


# -------------------------------------------------------------------
# FastAPI App
# -------------------------------------------------------------------
app = FastAPI(title="Bob 2.0 Skill Orchestrator")

registry = SkillRegistry()
bob = BobClient()
jobs = JobManager()
executor = SkillExecutor(registry, bob, jobs)


@app.get("/api/skills")
def list_skills():
    return {
        "auto": [
            s.dict()
            for s in registry.skills.values()
            if s.trigger == SkillTrigger.auto
        ],
        "manual": [
            s.dict()
            for s in registry.skills.values()
            if s.trigger == SkillTrigger.manual
        ],
    }


@app.post("/api/repos/clone")
async def clone_repo_endpoint(
    req: RepoCloneRequest, background_tasks: BackgroundTasks
):
    if not req.confirm:
        raise HTTPException(400, "User must confirm clone")

    try:
        repo_id = await clone_repo(req.repo_url, req.branch)
    except Exception as e:
        raise HTTPException(500, f"Clone failed: {e}")

    job = jobs.create_job(repo_id)
    background_tasks.add_task(
        executor.run_auto_skills, job.id, repo_id, "repo-clone"
    )

    return {
        "repo_id": repo_id,
        "job_id": job.id,
        "status": "cloning_and_running_auto_skills",
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.post("/api/repos/{repo_id}/skills/{skill_name}/run")
async def run_manual_skill(
    repo_id: str, skill_name: str, background_tasks: BackgroundTasks
):
    job = next((j for j in jobs.jobs.values() if j.repo_id == repo_id), None)
    if not job:
        raise HTTPException(404, "No job for repo")

    if skill_name not in registry.skills:
        raise HTTPException(404, "Skill not found")

    if registry.skills[skill_name].trigger != SkillTrigger.manual:
        raise HTTPException(400, "Skill is not manual")

    background_tasks.add_task(
        executor.run_manual_skill, job.id, repo_id, skill_name
    )
    return {"job_id": job.id, "skill": skill_name, "status": "started"}


@app.get("/api/repos/{repo_id}/results")
def get_results(repo_id: str):
    job = next((j for j in jobs.jobs.values() if j.repo_id == repo_id), None)
    if not job:
        raise HTTPException(404, "No job for repo")
    return {"repo_id": repo_id, "job": job}