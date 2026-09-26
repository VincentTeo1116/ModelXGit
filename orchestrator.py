"""Skill orchestration: skill registry, repo workspace, Bob context, jobs and the pipeline.

Pipeline (driven by each SKILL.md's frontmatter):
  1. entry skill (trigger: entry, i.e. repo-clone) runs when the user confirms the clone:
     the backend clones the repo, then Bob writes the clone report.
  2. every auto skill with `after: repo-clone` then starts. Each one waits for the
     skills in its `depends_on`; everything else runs in parallel (at most
     MAX_PARALLEL_SKILLS Bob calls at a time). `parallel_group` is a display label.
  3. manual skills (codebase-qa, readme-generator) run when the user selects them.
"""
import asyncio
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, Field

import config
from bob_integration import BobClient

CLONE_EVENT = "repo-clone"


# -------------------------------------------------------------------
# Models
# -------------------------------------------------------------------
class SkillTrigger(str, Enum):
    entry = "entry"    # runs when the user confirms the clone
    auto = "auto"      # runs automatically after an event (`after`)
    manual = "manual"  # runs when the user selects it


class StepStatus(str, Enum):
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"
    skipped = "skipped"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    success = "success"
    partial = "partial"  # finished, but some skills failed
    failed = "failed"    # the clone failed


class SkillInfo(BaseModel):
    name: str
    description: str
    trigger: SkillTrigger
    after: Optional[str] = None
    depends_on: List[str] = []
    parallel_group: Optional[str] = None
    produces: List[str] = []
    local_tools: List[str] = []
    path: str


class StepRun(BaseModel):
    input: Optional[Dict[str, Any]] = None
    status: StepStatus
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    files_written: List[str] = []
    started_at: float
    finished_at: Optional[float] = None


class JobStep(BaseModel):
    skill: str
    status: StepStatus = StepStatus.pending
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    files_written: List[str] = []
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    runs: List[StepRun] = []  # every run, e.g. each codebase-qa question


class RepoInfo(BaseModel):
    url: str
    branch: Optional[str] = None
    depth: Optional[int] = None
    cloned: bool = False
    commit: Optional[Dict[str, str]] = None
    commits: Optional[int] = None


class Job(BaseModel):
    id: str
    repo_id: str
    status: JobStatus = JobStatus.queued
    repo: RepoInfo
    steps: Dict[str, JobStep] = Field(default_factory=dict)
    created_at: float
    updated_at: float


class ConflictError(Exception):
    """The request is valid but can't run in the job's current state."""


# -------------------------------------------------------------------
# Skill registry
# -------------------------------------------------------------------
FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else [str(v) for v in value]


def parse_skill_md(path: Path) -> SkillInfo:
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER.match(text)
    if not m:
        raise ValueError(f"No frontmatter in {path}")
    meta = yaml.safe_load(m.group(1)) or {}
    orch = meta.get("x-orchestrator", meta)  # flat fields or x-orchestrator namespace
    return SkillInfo(
        name=meta.get("name", path.parent.name),
        description=meta.get("description", ""),
        trigger=SkillTrigger(orch.get("trigger", "manual")),
        after=orch.get("after"),
        depends_on=_as_list(orch.get("depends_on")),
        parallel_group=orch.get("parallel_group"),
        produces=_as_list(orch.get("produces")),
        local_tools=_as_list(orch.get("local_tools")),
        path=str(path),
    )


class SkillRegistry:
    def __init__(self, skills_dir: Path):
        self.skills: Dict[str, SkillInfo] = {}
        self.warnings: List[str] = []
        for md in sorted(skills_dir.glob("*/SKILL.md")):
            info = parse_skill_md(md)
            if info.name in self.skills:
                raise ValueError(f"Duplicate skill name '{info.name}' in {md}")
            self.skills[info.name] = info
        self._validate()

    def _validate(self):
        entries = self.by_trigger(SkillTrigger.entry)
        if [s.name for s in entries] != [CLONE_EVENT]:
            self.warnings.append(
                f"Expected exactly one entry skill named '{CLONE_EVENT}', found {[s.name for s in entries]}"
            )
        for s in self.skills.values():
            for dep in s.depends_on:
                if dep not in self.skills:
                    self.warnings.append(f"{s.name}: depends_on unknown skill '{dep}' (ignored)")
            if s.trigger == SkillTrigger.auto and s.after != CLONE_EVENT:
                self.warnings.append(
                    f"{s.name}: auto skill with after='{s.after}' never runs (only '{CLONE_EVENT}' exists)"
                )
            for spec in s.local_tools:
                script = Path(s.path).parent / shlex.split(spec)[0]
                if not script.is_file():
                    self.warnings.append(f"{s.name}: local tool not found: {spec}")

        # Fail fast on dependency cycles: they would make the pipeline hang.
        state: Dict[str, int] = {}

        def visit(name: str, chain: List[str]):
            if state.get(name) == 1:
                raise ValueError("Skill dependency cycle: " + " -> ".join(chain + [name]))
            if state.get(name) == 2:
                return
            state[name] = 1
            for dep in self.skills[name].depends_on:
                if dep in self.skills:
                    visit(dep, chain + [name])
            state[name] = 2

        for name in self.skills:
            visit(name, [])

    def get(self, name: str) -> Optional[SkillInfo]:
        return self.skills.get(name)

    def by_trigger(self, trigger: SkillTrigger) -> List[SkillInfo]:
        return [s for s in self.skills.values() if s.trigger == trigger]

    def auto_after(self, event: str) -> List[SkillInfo]:
        return [s for s in self.by_trigger(SkillTrigger.auto) if s.after == event]

    @property
    def entry(self) -> Optional[SkillInfo]:
        return self.skills.get(CLONE_EVENT)


# -------------------------------------------------------------------
# Repo workspace: URL checks, clone, facts
# -------------------------------------------------------------------
WEB_URL = re.compile(  # github/gitlab page links -> clonable URL + branch
    r"^(https://(?:github\.com|gitlab\.com)/[^/\s]+/[^/\s]+?)(?:\.git)?/(?:-/)?(?:tree|blob)/([^/\s]+)"
)
HTTPS_URL = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d+)?/[A-Za-z0-9._~%+\-/]+$")
SCP_URL = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[A-Za-z0-9._~\-/]+$")
SSH_URL = re.compile(r"^ssh://[A-Za-z0-9._-]+@[A-Za-z0-9.-]+(?::\d+)?/[A-Za-z0-9._~\-/]+$")
BRANCH_NAME = re.compile(r"^[A-Za-z0-9._/\-]+$")


def normalize_repo_url(url: str, branch: Optional[str]) -> Tuple[str, Optional[str]]:
    """Allow only remote https/ssh git URLs (no local paths, file://, options or credentials)."""
    url = url.strip()
    m = WEB_URL.match(url)
    if m:
        url, branch = m.group(1) + ".git", branch or m.group(2)
    if not (HTTPS_URL.match(url) or SCP_URL.match(url) or SSH_URL.match(url)):
        raise ValueError(
            "Only remote git URLs are allowed: https://host/owner/repo(.git), "
            "ssh://git@host/owner/repo or git@host:owner/repo (no credentials in the URL)."
        )
    branch = (branch or "").strip() or None  # None = the repo's default branch
    if branch and (branch.startswith("-") or ".." in branch or not BRANCH_NAME.match(branch)):
        raise ValueError(f"Invalid branch name: {branch!r}")
    return url, branch


def _git(args: List[str], cwd: Optional[Path] = None, timeout: int = 60) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # fail instead of hanging on a password prompt
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def clone_repo(url: str, branch: Optional[str], dest: Path, depth: Optional[int] = None):
    """Full clone by default: the history audit and "why" questions need history."""
    args = [
        # Only network transports: blocks file:// and ext:: (also for submodules).
        "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
        "-c", "protocol.ssh.allow=always",
        # Symlinks become plain text files, so a repo can't point outside itself.
        "-c", "core.symlinks=false", "clone",
    ]
    if branch:
        args += ["--branch", branch]
    if depth:
        args += ["--depth", str(depth)]
    args += ["--", url, str(dest)]  # "--": the URL can never be read as an option
    try:
        result = _git(args, timeout=config.CLONE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git clone timed out after {config.CLONE_TIMEOUT}s")
    if result.returncode != 0:
        err = result.stderr.strip()
        if any(s in err for s in ("Cannot prompt", "could not read Username", "Repository not found")):
            raise RuntimeError("git clone failed: repository not found, or it is private (not supported).")
        raise RuntimeError(f"git clone failed: {err[-1500:]}")


def repo_facts(dest: Path) -> Dict[str, Any]:
    head = _git(["log", "-1", "--format=%H%n%aI%n%s"], cwd=dest).stdout.splitlines()
    count = _git(["rev-list", "--count", "HEAD"], cwd=dest).stdout.strip()
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=dest).stdout.strip()
    return {
        "commit": {"hash": head[0], "date": head[1], "message": head[2] if len(head) > 2 else ""}
        if len(head) >= 2 else None,
        "commits": int(count) if count.isdigit() else None,
        "branch": branch or None,
    }


# -------------------------------------------------------------------
# What Bob receives: repo contents (Bob can't read this server's disk)
# -------------------------------------------------------------------
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build",
    ".next", ".nuxt", ".vite", ".cache", "coverage", "target",
    "onboarding",  # skill outputs: sent separately as previous_results
}
SKIP_FILES = [
    # lockfiles: huge and low value
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Pipfile.lock", "uv.lock", "Cargo.lock", "composer.lock", "Gemfile.lock", "go.sum",
    # secrets: never send them to an external API
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx",
    "id_rsa", "id_ed25519", "credentials.json", "*serviceAccount*.json",
]
SAFE_ENV_SUFFIXES = (".example", ".sample", ".template", ".dist")
PRIORITY_FILES = {
    "readme.md", "package.json", "requirements.txt", "pyproject.toml", "pom.xml",
    "build.gradle", "go.mod", "cargo.toml", "dockerfile", "docker-compose.yml", ".env.example",
}


def _skip_file(name: str) -> bool:
    if name.endswith(SAFE_ENV_SUFFIXES):
        return False
    return any(fnmatch.fnmatch(name, pat) for pat in SKIP_FILES)


def build_repo_context(repo_path: Path) -> Dict[str, Any]:
    """File tree plus text file contents, within the size budget."""
    candidates = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            full = Path(root) / name
            if full.is_symlink():  # never follow links out of the repo
                continue
            candidates.append((full.relative_to(repo_path).as_posix(), full))

    file_tree = [rel for rel, _ in candidates]
    included: List[Dict[str, Any]] = []
    omitted: List[Dict[str, str]] = []
    total = 0

    # Manifests and READMEs first, so they survive the size budget.
    candidates.sort(key=lambda c: (c[0].rsplit("/", 1)[-1].lower() not in PRIORITY_FILES, c[0]))
    for rel, full in candidates:
        if _skip_file(rel.rsplit("/", 1)[-1]):
            omitted.append({"path": rel, "reason": "skipped (secret or lockfile)"})
            continue
        try:
            raw = full.read_bytes()
        except OSError as e:
            omitted.append({"path": rel, "reason": f"unreadable: {e}"})
            continue
        if b"\0" in raw[:8192]:
            omitted.append({"path": rel, "reason": "binary"})
            continue
        text = raw.decode("utf-8", errors="replace")
        truncated = len(text) > config.REPO_FILE_MAX_CHARS
        if truncated:
            text = text[: config.REPO_FILE_MAX_CHARS]
        if total + len(text) > config.REPO_CONTEXT_MAX_CHARS:
            omitted.append({"path": rel, "reason": "context size budget reached"})
            continue
        total += len(text)
        included.append({"path": rel, "content": text, "truncated": truncated})

    return {"file_tree": file_tree, "files": included, "omitted": omitted, "total_chars": total}


def run_local_tools(skill: SkillInfo, repo_dir: Path) -> Dict[str, Any]:
    """Run the skill's own scripts (e.g. secret scanners) on the clone.
    Bob can't execute anything, so their (masked) results are sent to Bob instead."""
    skill_dir = Path(skill.path).parent.resolve()
    results: Dict[str, Any] = {}
    for spec in skill.local_tools:
        parts = shlex.split(spec)
        script = (skill_dir / parts[0]).resolve()
        if skill_dir not in script.parents or not script.is_file():
            results[spec] = {"error": "script not found in the skill folder"}
            continue
        try:
            r = subprocess.run(
                [sys.executable, str(script), *parts[1:]], cwd=repo_dir, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=config.LOCAL_TOOL_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            results[spec] = {"error": f"timed out after {config.LOCAL_TOOL_TIMEOUT}s"}
            continue
        try:
            output: Any = json.loads(r.stdout)
        except ValueError:
            output = r.stdout[-20000:]
        results[spec] = {"exit_code": r.returncode, "output": output, "stderr": r.stderr[-2000:]}
    return results


# -------------------------------------------------------------------
# What Bob returns: files are written into the clone's onboarding/ folder
# -------------------------------------------------------------------
OUTPUT_PATH = re.compile(r"^(onboarding/[A-Za-z0-9._\-/]+|README\.md)$")


def is_output_path(rel: str) -> bool:
    return bool(OUTPUT_PATH.match(rel)) and ".." not in rel.split("/")


def write_outputs(repo_dir: Path, files: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    written, rejected = [], []
    root = repo_dir.resolve()
    for rel, content in files.items():
        rel = str(rel).replace("\\", "/").removeprefix("./")
        target = (root / rel).resolve()
        if not is_output_path(rel) or root not in target.parents:
            rejected.append(rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        text = content if isinstance(content, str) else json.dumps(content, indent=2, ensure_ascii=False)
        target.write_text(text, encoding="utf-8")
        written.append(rel)
    return written, rejected


RULES = [
    "You are called by a backend service, not an interactive chat: you cannot run commands, "
    "read or write files, or ask the user questions.",
    "The repository is in context.repo (file tree + text file contents); files in repo.omitted were not sent.",
    "Git facts from the clone are in context.clone. Results of the scripts this skill mentions are "
    "already in context.local_tool_results: use them instead of running the scripts.",
    "Earlier skills' results are in context.previous_results: reuse them.",
    "Where the skill says to ask the user, install, execute, rewrite history, push or open pages, "
    "don't: list it in 'actions_for_user'.",
    "Never include secret values: mask them.",
    "Reply with JSON only, shaped like context.orchestrator.response_format. Put each file the skill "
    "would save (context.orchestrator.expected_files) in 'files'.",
]
RESPONSE_FORMAT = {
    "summary": "3-5 line plain-text summary for the dashboard",
    "files": {"<path from expected_files>": "file content: a string, or a JSON object for .json files"},
    "actions_for_user": ["anything that needs the developer's approval or action"],
    "answer": "markdown answer to user_input.question (codebase-qa only)",
}


# -------------------------------------------------------------------
# Orchestrator: jobs + pipeline
# -------------------------------------------------------------------
ACTIVE = (StepStatus.pending, StepStatus.running)


class Orchestrator:
    def __init__(self, registry: SkillRegistry, bob: BobClient):
        self.registry = registry
        self.bob = bob
        self.jobs: Dict[str, Job] = {}         # job_id -> job (in memory)
        self.repo_jobs: Dict[str, str] = {}    # repo_id -> job_id
        self._contexts: Dict[str, asyncio.Future] = {}
        self._tasks: set = set()
        self._bob_slots = asyncio.Semaphore(config.MAX_PARALLEL_SKILLS)
        config.WORKSPACE.mkdir(parents=True, exist_ok=True)

    # ---- jobs -------------------------------------------------------
    def create_job(self, url: str, branch: Optional[str], depth: Optional[int]) -> Job:
        now = time.time()
        job = Job(
            id=uuid.uuid4().hex[:8], repo_id=uuid.uuid4().hex[:8],
            repo=RepoInfo(url=url, branch=branch, depth=depth), created_at=now, updated_at=now,
        )
        # Register the whole pipeline up front, so the status is right from the start.
        for skill in [self.registry.entry, *self.registry.auto_after(CLONE_EVENT)]:
            if skill:
                job.steps[skill.name] = JobStep(skill=skill.name)
        if not self.registry.entry:
            job.steps[CLONE_EVENT] = JobStep(skill=CLONE_EVENT)
        self.jobs[job.id] = job
        self.repo_jobs[job.repo_id] = job.id
        return job

    def job_for_repo(self, repo_id: str) -> Optional[Job]:
        job_id = self.repo_jobs.get(repo_id)
        return self.jobs.get(job_id) if job_id else None

    def repo_dir(self, job: Job) -> Path:
        return config.WORKSPACE / job.repo_id

    def _update(self, job: Job, name: str, **fields):
        step = job.steps.setdefault(name, JobStep(skill=name))
        for key, value in fields.items():
            setattr(step, key, value)
        job.updated_at = time.time()
        statuses = [s.status for s in job.steps.values()]
        if all(s == StepStatus.pending for s in statuses):
            job.status = JobStatus.queued
        elif any(s in ACTIVE for s in statuses):
            job.status = JobStatus.running
        elif not job.repo.cloned:
            job.status = JobStatus.failed
        elif all(s == StepStatus.success for s in statuses):
            job.status = JobStatus.success
        else:
            job.status = JobStatus.partial

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)  # keep a reference so it isn't garbage-collected
        task.add_done_callback(self._tasks.discard)

    # ---- pipeline ---------------------------------------------------
    def start_pipeline(self, job: Job):
        self._spawn(self._pipeline(job))

    async def _pipeline(self, job: Job):
        entry = self.registry.entry
        clone_step = entry.name if entry else CLONE_EVENT
        dest = self.repo_dir(job)
        self._update(job, clone_step, status=StepStatus.running, started_at=time.time())
        try:
            await asyncio.to_thread(clone_repo, job.repo.url, job.repo.branch, dest, job.repo.depth)
            facts = await asyncio.to_thread(repo_facts, dest)
            job.repo.cloned = True
            job.repo.commit, job.repo.commits = facts["commit"], facts["commits"]
            job.repo.branch = job.repo.branch or facts["branch"]
        except Exception as e:
            self._update(job, clone_step, status=StepStatus.failed, error=str(e), finished_at=time.time())
            for name, step in job.steps.items():
                if step.status == StepStatus.pending:
                    self._update(job, name, status=StepStatus.skipped, error="repository clone failed")
            return

        # Clone report by Bob (the repo-clone skill). Auto skills run even if it fails.
        if entry:
            await self._run_skill(job, entry, "auto")
        else:
            self._update(job, clone_step, status=StepStatus.success, finished_at=time.time())

        # Auto skills: each waits for its depends_on, the rest run in parallel.
        skills = self.registry.auto_after(CLONE_EVENT)
        done = {s.name: asyncio.Event() for s in skills}

        async def run_one(skill: SkillInfo):
            try:
                for dep in skill.depends_on:
                    if dep in done:
                        await done[dep].wait()
                await self._run_skill(job, skill, "auto")
            finally:
                done[skill.name].set()

        await asyncio.gather(*(run_one(s) for s in skills))

    def start_skill(self, job: Job, skill: SkillInfo, user_input: Optional[Dict[str, Any]]):
        """Run a manual skill, or retry a failed auto skill, on request."""
        if not job.repo.cloned:
            raise ConflictError("The repository isn't cloned yet (or the clone failed).")
        step = job.steps.get(skill.name)
        if step and step.status == StepStatus.running:
            raise ConflictError(f"{skill.name} is already running.")
        if skill.trigger != SkillTrigger.manual and step and step.status in ACTIVE:
            raise ConflictError(f"{skill.name} runs automatically and hasn't finished yet.")
        if skill.trigger != SkillTrigger.manual and step and step.status == StepStatus.success:
            raise ConflictError(f"{skill.name} runs automatically and already succeeded.")
        waiting = [
            d for d in skill.depends_on
            if d in job.steps and job.steps[d].status in ACTIVE
        ]
        if waiting:
            raise ConflictError(f"{skill.name} needs these to finish first: {', '.join(waiting)}")
        # Mark as running now, so a second click can't start it twice.
        self._update(job, skill.name, status=StepStatus.running, started_at=time.time())
        self._spawn(self._run_skill(job, skill, skill.trigger.value, user_input))

    async def _run_skill(
        self, job: Job, skill: SkillInfo, mode: str, user_input: Optional[Dict[str, Any]] = None
    ):
        started = time.time()
        self._update(job, skill.name, status=StepStatus.running, started_at=started, error=None)
        run = StepRun(input=user_input, status=StepStatus.running, started_at=started)
        written: List[str] = []
        try:
            context = await self._context(job, skill, mode, user_input)
            skill_md = Path(skill.path).read_text(encoding="utf-8")
            async with self._bob_slots:
                output = await self.bob.run_skill(skill.name, skill_md, context)
            files = output.get("files")
            if isinstance(files, dict) and files:
                written, rejected = await asyncio.to_thread(write_outputs, self.repo_dir(job), files)
                if rejected:
                    output.setdefault("warnings", []).append(
                        f"Ignored files outside onboarding/ and README.md: {rejected}"
                    )
            run.status, run.output = StepStatus.success, output
        except Exception as e:
            error = str(e)
            if skill.name == CLONE_EVENT:
                error = f"Repository cloned, but the clone report failed: {error}"
            run.status, run.error = StepStatus.failed, error
        run.files_written, run.finished_at = written, time.time()

        step = job.steps[skill.name]
        step.runs.append(run)
        self._update(
            job, skill.name, status=run.status, output=run.output, error=run.error,
            files_written=sorted(set(step.files_written) | set(written)), finished_at=run.finished_at,
        )

    async def _context(
        self, job: Job, skill: SkillInfo, mode: str, user_input: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        context: Dict[str, Any] = {
            "orchestrator": {
                "mode": mode,
                "rules": RULES,
                "response_format": RESPONSE_FORMAT,
                "expected_files": skill.produces,
            },
            "repo_id": job.repo_id,
            "job_id": job.id,
            "skill_name": skill.name,
            "clone": job.repo.model_dump(),
            "repo": await self._repo_context(job),
            "previous_results": {
                name: step.output for name, step in job.steps.items()
                if name != skill.name and step.status == StepStatus.success and step.output
            },
        }
        if skill.local_tools:
            context["local_tool_results"] = await asyncio.to_thread(
                run_local_tools, skill, self.repo_dir(job)
            )
        step = job.steps.get(skill.name)
        if step and step.runs:  # earlier questions/answers, for follow-ups
            context["history"] = [
                {"input": r.input, "answer": (r.output or {}).get("answer") or (r.output or {}).get("summary")}
                for r in step.runs[-5:] if r.status == StepStatus.success
            ]
        if user_input:
            context["user_input"] = user_input
        return context

    async def _repo_context(self, job: Job) -> Dict[str, Any]:
        """Built once per repo, shared by all skills."""
        future = self._contexts.get(job.repo_id)
        if future is None:
            future = asyncio.ensure_future(asyncio.to_thread(build_repo_context, self.repo_dir(job)))
            self._contexts[job.repo_id] = future
        try:
            return await future
        except Exception:
            self._contexts.pop(job.repo_id, None)  # allow a retry
            raise
