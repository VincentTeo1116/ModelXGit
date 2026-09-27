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
import shutil
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
from bob_integration import BobShellClient, HttpBobClient

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
    facts: Optional[Dict[str, Any]] = None  # tracked file count, license, README, CI... (file_facts)
    clone_seconds: Optional[float] = None


class Job(BaseModel):
    id: str
    repo_id: str
    status: JobStatus = JobStatus.queued
    repo: RepoInfo
    bob_ide: Optional[Dict[str, Any]] = None  # what was installed in the clone's .bob/ for Bob IDE
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


NETWORK_ERRORS = ("could not resolve host", "failed to connect", "connection timed out", "connection reset",
                  "operation timed out", "early eof", "rpc failed", "timed out after")


def clone_with_retry(url: str, branch: Optional[str], dest: Path, depth: Optional[int] = None,
                     attempts: int = 3, pause: float = 3.0):
    """Clone, retrying network blips (e.g. 'Could not resolve host'); never retry 'not found'."""
    for attempt in range(1, attempts + 1):
        try:
            return clone_repo(url, branch, dest, depth)
        except RuntimeError as e:
            network = any(s in str(e).lower() for s in NETWORK_ERRORS)
            if not network or attempt == attempts:
                if network:
                    raise RuntimeError(f"Couldn't reach the git host after {attempts} tries: check your internet "
                                       f"connection and try again. ({str(e).splitlines()[-1][:200]})") from e
                raise
            shutil.rmtree(dest, ignore_errors=True)  # a half-finished clone blocks the next try
            time.sleep(pause * attempt)


def dir_size_mb(path: Path) -> float:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total / (1024 * 1024)


def remove_tree(path: Path):
    """Delete a folder, including git's read-only object files (Windows refuses those)."""
    def retry(func, target, _exc):
        os.chmod(target, 0o700)
        func(target)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def check_clone_size(dest: Path):
    """MAX_REPO_MB, checked on disk after the clone (the pre-clone check needs the GitHub API)."""
    if config.MAX_REPO_MB:
        size = dir_size_mb(dest)
        if size > config.MAX_REPO_MB:
            remove_tree(dest)
            raise RuntimeError(f"The repository is {size:,.0f} MB on disk. This server accepts up to "
                               f"{config.MAX_REPO_MB} MB; run ModelXGit on your own computer for bigger repos.")


def repo_facts(dest: Path) -> Dict[str, Any]:
    head = _git(["log", "-1", "--format=%H%n%aI%n%s"], cwd=dest).stdout.splitlines()
    count = _git(["rev-list", "--count", "HEAD"], cwd=dest).stdout.strip()
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=dest).stdout.strip()
    return {
        "commit": {"hash": head[0], "date": head[1], "message": head[2] if len(head) > 2 else ""}
        if len(head) >= 2 else None,
        "commits": int(count) if count.isdigit() else None,
        "branch": branch or None,
        "facts": file_facts(dest),
    }


TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$")


def file_facts(dest: Path) -> Dict[str, Any]:
    """Exact basics from the tracked files, so the repo-clone skill doesn't have to count."""
    files = [f for f in _git(["ls-files", "-z"], cwd=dest).stdout.split("\0") if f]
    root = [f for f in files if "/" not in f]
    names = {f.rsplit("/", 1)[-1].lower() for f in files}
    gitattributes = dest / ".gitattributes"
    return {
        "files": len(files),
        "top_level": sorted({f.split("/", 1)[0] + ("/" if "/" in f else "") for f in files}),
        "license": next((f for f in root if re.match(r"(?i)^(licen[cs]e|copying)(\..*)?$", f)), None),
        "readme": next((f for f in root if f.lower().startswith("readme")), None),
        "env_example": sorted(f for f in files if f.rsplit("/", 1)[-1] in (".env.example", ".env.sample", ".env.template")),
        "docker": sorted(f for f in files if f.rsplit("/", 1)[-1].lower() in ("dockerfile", "docker-compose.yml",
                                                                                "docker-compose.yaml", "compose.yml", "compose.yaml")),
        "ci": sorted({f.split("/")[0] + "/" + f.split("/")[1] if f.startswith(".github/workflows/") else f
                      for f in files if f.startswith((".github/workflows/", ".circleci/"))
                      or f in (".gitlab-ci.yml", "azure-pipelines.yml", "Jenkinsfile")}),
        "test_files": sum(1 for f in files if TEST_FILE.search(f)),
        "submodules": ".gitmodules" in names,
        "git_lfs": gitattributes.is_file() and "filter=lfs" in gitattributes.read_text(errors="ignore"),
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


def build_repo_context(repo_path: Path, exclude: frozenset = frozenset()) -> Dict[str, Any]:
    """File tree plus text file contents, within the size budget.
    `exclude`: files the backend added itself (the Bob IDE hand-off), not part of the repo."""
    candidates = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            full = Path(root) / name
            if full.is_symlink():  # never follow links out of the repo
                continue
            rel = full.relative_to(repo_path).as_posix()
            if rel not in exclude:
                candidates.append((rel, full))

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


CONTEXT_DIR = "onboarding/.context"  # Bob Shell's per-skill instructions + context (not an output)


def is_output_path(rel: str) -> bool:
    return (bool(OUTPUT_PATH.match(rel)) and ".." not in rel.split("/")
            and not rel.startswith(CONTEXT_DIR + "/"))


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


# ---- Bob Shell: Bob works inside the clone ----------------------------------------
SHELL_PROMPT = """You are running the onboarding skill "{name}" for a backend service. Nobody can answer questions.
1. Read and follow the skill instructions in {ctx}/{name}.SKILL.md.
2. Read {ctx}/{name}.context.json: clone facts, results of the scripts the skill mentions (already run: use them, do not run scripts), earlier skills' results, and the developer's input.
3. Only create or edit files under onboarding/{readme}. Never change any other file and never touch {ctx}/.
4. Never write secret values: mask them. Where the skill says to ask the user, install, run, rewrite history, push or open pages, do not do it: note it for the developer instead.
5. Save these files: {files}.
{finish}"""
FINISH_SUMMARY = "6. Finish with a short plain-text summary (3-5 lines) of what you found and anything the developer must act on."
FINISH_ANSWER = "6. Finish with your full answer to the developer's question (Markdown), ending with \"Read next:\" and 1-2 files."


def write_skill_bundle(repo_dir: Path, skill: "SkillInfo", skill_md: str, context: Dict[str, Any]):
    """Instructions + context as files in the clone, so the prompt stays short."""
    ctx_dir = repo_dir / CONTEXT_DIR
    ctx_dir.mkdir(parents=True, exist_ok=True)
    (ctx_dir / f"{skill.name}.SKILL.md").write_text(skill_md, encoding="utf-8")
    (ctx_dir / f"{skill.name}.context.json").write_text(
        json.dumps(context, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def shell_prompt(skill: "SkillInfo", has_question: bool) -> str:
    return SHELL_PROMPT.format(
        name=skill.name, ctx=CONTEXT_DIR,
        readme=" and README.md" if "README.md" in skill.produces else "",
        files=", ".join(skill.produces) or "none",
        finish=FINISH_ANSWER if has_question else FINISH_SUMMARY,
    )


def contain_workspace(repo_dir: Path, keep: frozenset = frozenset()) -> List[str]:
    """Undo every change outside onboarding/ and README.md (Bob may only write there).
    Includes git-ignored files (e.g. a new .env): a fresh clone has none, so any are Bob's.
    `keep`: files the backend itself added (the Bob IDE hand-off in .bob/)."""
    out = _git(["status", "--porcelain", "-uall", "--ignored=matching", "-z"], cwd=repo_dir).stdout
    root = repo_dir.resolve()
    entries, reverted, i = out.split("\0"), [], 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        paths = [path]
        if code[0] in "RC" and i < len(entries):  # rename/copy: the next entry is the source
            paths.append(entries[i])
            i += 1
        for p in paths:
            if p == "README.md" or p.startswith("onboarding/") or p in keep:
                continue
            target = (root / p).resolve()
            if root not in target.parents or ".git" in Path(p).parts:
                continue  # never touch anything outside the clone or inside .git
            if code != "!!" and _git(["ls-files", "--error-unmatch", p], cwd=repo_dir).returncode == 0:
                _git(["checkout", "HEAD", "--", p], cwd=repo_dir)  # restore a tracked file
            elif target.is_dir():
                shutil.rmtree(target, ignore_errors=True)  # a new (ignored) folder
            elif target.is_file():
                target.unlink()  # a new file, ignored or not
            reverted.append(p.rstrip("/"))
    return sorted(set(reverted))


# ---- Bob IDE hand-off: the clone opens in Bob IDE with our skills and a Q&A mode ------
MODES_FILE = ".bob/custom_modes.yaml"
CODEBASE_QA_MODE = """customModes:
  - slug: codebase-qa
    name: Codebase Q&A
    description: Role-based Q&A about this repo, starting from the onboarding results.
    roleDefinition: |
      You are a senior engineer onboarding a new developer to this repository.
      You tailor everything to the developer's role (Frontend, Backend, Full Stack,
      Database, AI/ML, QA/Testing, DevOps).
    customInstructions: |
      - Start from the onboarding results in onboarding/ if they exist (CODEBASE_MAP.md,
        tech_stack.md, architecture.md, setup_report.md, the secrets reports), then open
        the real files. Base every answer on the real code, not only on those notes.
      - If the developer hasn't said their role, ask for it in one short question.
      - With a role and a question, answer the question from that role's point of view.
        Give a full role briefing only when asked for one.
      - Start with a short, direct answer, then the details, citing file paths and line ranges.
      - If something isn't in the code, say "not found in this repo". Never guess or invent.
      - Never reveal secret values.
      - End with "Read next:" (1-2 files) and 2-3 suggested follow-up questions.
      - Do not modify any files.
    groups:
      - read
"""


BOBIGNORE = """# Added by the Model X onboarding backend: Bob must never read secret files.
# The local secret scanners still check them; only their masked results reach Bob.
.env
.env.*
!.env.example
!.env.sample
!.env.template
*.env
*.pem
*.key
*.p12
*.pfx
*.jks
*.keystore
id_rsa*
id_ed25519*
credentials.json
*serviceAccount*.json
*service-account*.json
.npmrc
.pypirc
.netrc
"""


def install_bob_ide_files(repo_dir: Path, registry: "SkillRegistry") -> Dict[str, Any]:
    """Copy our skills and the Codebase Q&A mode into the clone's .bob/, so the developer can
    continue in Bob IDE with them. Never overwrites .bob/ files the repository itself tracks.
    Safe to call again: it restores the files if anything changed them."""
    tracked = set(_git(["ls-files", ".bob"], cwd=repo_dir).stdout.splitlines())
    installed, kept = [], []
    for skill in sorted(registry.skills.values(), key=lambda s: s.name):
        src_dir = Path(skill.path).parent
        for src in sorted(src_dir.rglob("*")):
            if not src.is_file() or "__pycache__" in src.parts:
                continue
            rel = f".bob/skills/{skill.name}/{src.relative_to(src_dir).as_posix()}"
            if rel in tracked:
                kept.append(rel)
                continue
            dst = repo_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            installed.append(rel)
    if MODES_FILE in tracked:
        kept.append(MODES_FILE)
    else:
        (repo_dir / MODES_FILE).write_text(CODEBASE_QA_MODE, encoding="utf-8")
        installed.append(MODES_FILE)
    # Hide secret files from Bob. A repo's own .bobignore is kept and ours is appended.
    own = (_git(["show", "HEAD:.bobignore"], cwd=repo_dir).stdout
           if _git(["ls-files", "--error-unmatch", ".bobignore"], cwd=repo_dir).returncode == 0 else "")
    (repo_dir / ".bobignore").write_text((own.rstrip("\n") + "\n\n" if own else "") + BOBIGNORE, encoding="utf-8")
    installed.append(".bobignore")
    return {
        "workspace_path": str(repo_dir.resolve()),
        "skills": sorted({p.split("/")[2] for p in installed + kept if p.startswith(".bob/skills/")}),
        "modes": ["codebase-qa"],
        "installed": installed,
        "kept_repo_files": kept,
    }


# ---- Output contract: the JSON files the dashboard relies on ------------------------
OUTPUT_CONTRACTS: Dict[str, Dict[str, type]] = {
    "onboarding/clone_report.json": {"repo": str, "url": str, "branch": str, "commit": dict, "files": int,
                                     "top_level": list, "license": str, "has": dict, "warnings": list},
    "onboarding/setup.json": {"toolchain": list, "env": dict, "steps": list, "run_command": str, "tests": str,
                              "issues": list},
    "onboarding/tech_stack.json": {"summary": str, "categories": dict, "absent": list},
    "onboarding/architecture.json": {"summary": str, "layers": list, "nodes": list, "edges": list},
    "onboarding/secrets_history.json": {"summary": dict, "findings": list},
    "onboarding/secrets_precommit.json": {"summary": dict, "gitignore_status": dict, "findings": list, "hook": dict},
}
# Keys that may be null when there is honestly nothing to put there (e.g. a repo with nothing to run).
NULLABLE_KEYS = {("onboarding/setup.json", "run_command")}
_patterns_module = None


def _patterns():
    """Our own scanner rules (skills/secret-precommit-scanner/scripts/secret_patterns.py)."""
    global _patterns_module
    if _patterns_module is None:
        import importlib.util
        path = config.SKILLS_DIR / "secret-precommit-scanner" / "scripts" / "secret_patterns.py"
        spec = importlib.util.spec_from_file_location("modelx_secret_patterns", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _patterns_module = module
    return _patterns_module


def mask_secrets(text: str) -> Tuple[str, int]:
    """Mask every secret-looking value (first 5 characters + ...[masked]); placeholders stay."""
    mod, count = _patterns(), 0
    for name, rx in mod.PATTERNS:
        def repl(m, name=name):
            nonlocal count
            val = m.group(m.lastindex) if (name == "Hardcoded secret assignment" and m.lastindex) else m.group(0)
            if mod.is_placeholder(val) or "[masked]" in m.group(0):
                return m.group(0)
            count += 1
            return m.group(0).replace(val, mod.mask(val))
        text = rx.sub(repl, text)
    return text, count


def mask_output_files(repo_dir: Path, files: List[str]) -> Dict[str, int]:
    """Enforce 'no secrets in outputs': mask values in the files Bob wrote, in place."""
    masked = {}
    for rel in files:
        path = repo_dir / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        new, n = mask_secrets(text)
        if n:
            path.write_text(new, encoding="utf-8")
            masked[rel] = n
    return masked


def validate_output(rel: str, text: str) -> List[str]:
    """Problems with one output file ([] = valid). Checks JSON shape and unmasked secrets."""
    problems: List[str] = []
    try:
        scan = _patterns().scan_line
        leaks = {kind for line in text.splitlines() for kind, _ in scan(line)}
        if leaks:
            problems.append(f"possible unmasked secret ({', '.join(sorted(leaks))})")
    except Exception:
        pass  # the secret check is extra safety; never block on it
    contract = OUTPUT_CONTRACTS.get(rel)
    if not contract:
        return problems
    try:
        data = json.loads(text)
    except ValueError as e:
        return problems + [f"not valid JSON ({e.msg} at line {e.lineno})"]
    if not isinstance(data, dict):
        return problems + ["top level is not a JSON object"]
    for key, typ in contract.items():
        if key not in data:
            problems.append(f"missing key '{key}'")
        elif data[key] is None and (rel, key) in NULLABLE_KEYS:
            continue
        elif not isinstance(data[key], typ) or (typ is int and isinstance(data[key], bool)):
            problems.append(f"'{key}' should be {typ.__name__}, got {type(data[key]).__name__}")
    if rel.endswith("architecture.json") and isinstance(data.get("nodes"), list) and isinstance(data.get("edges"), list):
        ids = {n.get("id") for n in data["nodes"] if isinstance(n, dict)}
        bad_nodes = [i for i, n in enumerate(data["nodes"]) if not isinstance(n, dict) or not all(k in n for k in ("id", "label", "layer"))]
        if bad_nodes:
            problems.append(f"{len(bad_nodes)} node(s) missing id/label/layer")
        dangling = [f"{e.get('from')}->{e.get('to')}" for e in data["edges"]
                    if isinstance(e, dict) and (e.get("from") not in ids or e.get("to") not in ids)]
        if dangling:
            problems.append(f"edges point to unknown nodes: {', '.join(dangling[:5])}")
    return problems


def validate_outputs(repo_dir: Path, files: List[str]) -> Dict[str, List[str]]:
    """Check the files a skill wrote; only JSON contracts and the secret check apply."""
    report = {}
    for rel in files:
        path = repo_dir / rel
        if path.is_file() and (rel in OUTPUT_CONTRACTS or rel.endswith((".json", ".md", ".mmd"))):
            report[rel] = validate_output(rel, path.read_text(encoding="utf-8", errors="replace"))
    return report


# ---- Impact metrics: measured by the backend, never written by Bob -------------------
def summarize_tools(tools: Dict[str, Any]) -> Dict[str, Any]:
    """Counts only (no values) from the local scanner scripts."""
    out = {}
    for spec, r in tools.items():
        name = spec.split()[0].rsplit("/", 1)[-1]
        data = r.get("output") if isinstance(r.get("output"), dict) else {}
        findings = data.get("findings", [])
        by_type: Dict[str, int] = {}
        for f in findings:
            by_type[f.get("type", "?")] = by_type.get(f.get("type", "?"), 0) + 1
        out[name] = {
            "exit_code": r.get("exit_code"), "error": r.get("error"),
            "findings": len(findings), "by_type": by_type,
            "gitignore_problems": len(data.get("gitignore_problems", [])),
            "commits_scanned": data.get("commits_scanned"),
        }
    return out


def compute_metrics(job: "Job", pipeline: List[str]) -> Dict[str, Any]:
    steps = job.steps
    pipe = [steps[n] for n in pipeline if n in steps]
    finished = [s for s in pipe if s.finished_at and s.started_at]
    elapsed = (round(max(s.finished_at for s in finished) - min(s.started_at for s in finished), 1)
               if pipe and len(finished) == len(pipe) else None)
    pipeline_ok = bool(pipe) and all(s.status == StepStatus.success for s in pipe)
    onboard_seconds = elapsed if pipeline_ok else None  # only a fully successful onboarding counts

    def last_stats(step: "JobStep") -> Dict[str, Any]:
        return ((step.runs[-1].output or {}).get("stats") or {}) if step.runs else {}

    all_stats = [(r.output or {}).get("stats") or {} for s in steps.values() for r in s.runs]
    scanners = {name: (steps[name].output or {}).get("scanner") or {}
                for name in ("git-history-secret-audit", "secret-precommit-scanner") if name in steps}
    history = sum(v.get("findings", 0) for v in scanners.get("git-history-secret-audit", {}).values())
    current = sum(v.get("findings", 0) for v in scanners.get("secret-precommit-scanner", {}).values())
    files = sorted({f for s in steps.values() for f in s.files_written})
    checks: Dict[str, List[str]] = {}
    for s in steps.values():
        checks.update((s.output or {}).get("validation") or {})

    comparison = None
    baseline = config.MANUAL_BASELINE_MINUTES
    if baseline and onboard_seconds:
        auto_min = onboard_seconds / 60
        comparison = {
            "manual_minutes": baseline, "automated_minutes": round(auto_min, 1),
            "minutes_saved": round(baseline - auto_min, 1),
            "faster_by_percent": round(100 * (1 - auto_min / baseline)),
            "source": "MANUAL_BASELINE_MINUTES (the team's own measurement)",
        }
    return {
        "generated_by": "Model X backend: measured, not written by Bob",
        "measured_at": time.time(),
        "repo": job.repo.url, "branch": job.repo.branch,
        "commit": (job.repo.commit or {}).get("hash"),
        "clone_seconds": job.repo.clone_seconds,
        "time_to_onboard_seconds": onboard_seconds,
        "pipeline_seconds": elapsed,
        "pipeline_succeeded": pipeline_ok,
        "skills": {
            "total": len(steps),
            "succeeded": sum(s.status == StepStatus.success for s in steps.values()),
            "failed": sum(s.status == StepStatus.failed for s in steps.values()),
            "running": sum(s.status in ACTIVE for s in steps.values()),
        },
        "files_produced": len(files),
        "files": files,
        "values_masked_in_outputs": sum(sum(((s.output or {}).get("auto_masked") or {}).values()) for s in steps.values()),
        "outputs_checked": len(checks),
        "outputs_valid": sum(1 for p in checks.values() if not p),
        "output_problems": {f: p for f, p in checks.items() if p},
        "secret_findings": {
            "git_history": history, "current_files": current, "total": history + current,
            "note": "Raw scanner findings, values masked; Bob's reports say which are false positives.",
        },
        "bob": {
            "runs": sum(len(s.runs) for s in steps.values()),
            "tool_calls": sum(st.get("tool_calls") or 0 for st in all_stats),
            "seconds": round(sum(st.get("duration_ms") or 0 for st in all_stats) / 1000, 1),
            "cost": round(sum(st.get("session_costs") or 0 for st in all_stats), 4),
            "task_ids": [st["task_id"] for st in all_stats if st.get("task_id")],
        },
        "steps": [
            {"skill": name, "status": s.status,
             "seconds": round(s.finished_at - s.started_at, 1) if s.finished_at and s.started_at else None,
             "tool_calls": last_stats(s).get("tool_calls"), "cost": last_stats(s).get("session_costs")}
            for name, s in steps.items()
        ],
        "manual_baseline": comparison,
    }


def write_metrics(repo_dir: Path, metrics: Dict[str, Any]) -> None:
    target = repo_dir / "onboarding" / "metrics.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, target)  # atomic: parallel skills may finish at the same time


# ---- One readable report per onboarding (people read this; the JSON is for the app) ----
REPORT_FILE = "onboarding/ONBOARDING_REPORT.md"
SKILL_TITLES = {
    "repo-clone": "Clone & repo report", "setup-dependencies": "Setup plan",
    "tech-stack-detection": "Tech stack", "architecture-diagram": "Architecture",
    "git-history-secret-audit": "Secret history audit", "secret-precommit-scanner": "Secret scan",
    "codebase-qa": "Codebase Q&A", "readme-generator": "README",
}


def _fmt_secs(sec: Optional[float]) -> str:
    if sec is None:
        return "—"
    s = int(round(sec))
    return f"{s}s" if s < 60 else f"{s // 60}m {s % 60:02d}s"


def build_onboarding_report(job: "Job", metrics: Dict[str, Any], order: List[str]) -> str:
    repo, name = job.repo, job.repo.url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    commit = repo.commit or {}
    m = metrics
    if m["time_to_onboard_seconds"]:
        onboard = _fmt_secs(m["time_to_onboard_seconds"])
    elif m["pipeline_seconds"]:
        onboard = f"pipeline had failures ({_fmt_secs(m['pipeline_seconds'])})"
    else:
        onboard = "still running"
    bob = m["bob"]
    lines = [
        f"# Onboarding report: {name}", "",
        f"- **Repository:** {repo.url}", f"- **Branch:** {repo.branch or 'default'}",
        f"- **Commit:** `{(commit.get('hash') or '')[:7]}` {commit.get('message', '')}",
        f"- **Generated:** {time.strftime('%Y-%m-%d %H:%M')} by the Model X backend from IBM Bob's results.", "",
        "## At a glance", "", "| What | Result |", "|---|---|",
        f"| Time to onboard | {onboard} |",
        f"| Skills completed | {m['skills']['succeeded']} of {m['skills']['total']} |",
        f"| Files produced | {m['files_produced']} ({m['outputs_valid']} of {m['outputs_checked']} passed the output check) |",
        f"| Secret findings (raw, masked) | {m['secret_findings']['git_history']} in git history, "
        f"{m['secret_findings']['current_files']} in current files |",
        f"| Bob | {bob['runs']} runs, {bob['tool_calls']} tool calls, {_fmt_secs(bob['seconds'])} of Bob work"
        + (f", cost {bob['cost']:.2f}" if bob["cost"] else "") + " |",
        "", "## Results", "",
    ]
    names = [n for n in order if n in job.steps] + [n for n in job.steps if n not in order]
    actions: List[str] = []
    for n in names:
        s = job.steps[n]
        status = getattr(s.status, "value", s.status)
        mark = {"success": "✓", "failed": "✗", "skipped": "–"}.get(status, "…")
        interrupted = (s.error or "").startswith("Interrupted")
        took = _fmt_secs(s.finished_at - s.started_at) if s.finished_at and s.started_at and not interrupted else ""
        lines += [f"### {mark} {SKILL_TITLES.get(n, n)} · {'interrupted' if interrupted else status}{' · ' + took if took else ''}", ""]
        out = s.output or {}
        if n == "codebase-qa":
            asked = [(r.input or {}).get("question") or "Role briefing" for r in s.runs if r.status == StepStatus.success]
            lines += [f"{len(asked)} question(s) answered:", ""] + [f"- {q}" for q in asked] + [""]
        elif out.get("summary"):
            summary = out["summary"].strip()
            lines += [summary if len(summary) <= 1500 else summary[:1500].rsplit("\n", 1)[0] + "\n\n*(shortened, see the files below)*", ""]
        if s.error:
            lines += [f"> **Problem:** {s.error.splitlines()[0][:300]}", ""]
            actions.append(f"Retry **{SKILL_TITLES.get(n, n)}** ({s.error.splitlines()[0][:120]})")
        if s.files_written:
            lines += ["**Files:** " + ", ".join(f"`{f}`" for f in s.files_written), ""]
        for w in out.get("warnings") or []:
            lines += [f"> ⚠ {w[:300]}", ""]
        actions += [a for a in out.get("actions_for_user") or [] if isinstance(a, str)]
    lines += ["## What you need to do", ""]
    lines += [f"- {a}" for a in dict.fromkeys(actions)] or ["- Nothing: every skill finished without open actions."]
    lines += ["", "## All files", "", "Readable reports are the `.md` files; the `.json` files hold the same "
              "results for the ModelXGit app.", ""]
    lines += [f"- `{f}`" for f in m["files"]] + ["- `onboarding/metrics.json` (measured numbers)", ""]
    return "\n".join(lines)


def write_onboarding_report(repo_dir: Path, text: str) -> None:
    target = repo_dir / REPORT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    masked, _ = mask_secrets(text)  # the summaries come from Bob: never let a secret into the report
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(masked, encoding="utf-8")
    os.replace(tmp, target)


def collect_outputs(repo_dir: Path, skill: "SkillInfo", since: float) -> List[str]:
    """The skill's declared files that Bob wrote during this run."""
    return [rel for rel in skill.produces
            if (repo_dir / rel).is_file() and (repo_dir / rel).stat().st_mtime >= since - 1]


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

# Errors a second try can't fix: retrying them would only double the cost.
PERMANENT_ERRORS = ("license", "bob_api_key is not set", "not found", "rejected the request",
                    "http 401", "http 403", "http 404", "unauthorized", "forbidden",
                    "invalid api key", "non-json")


def is_transient(message: str) -> bool:
    return not any(p in message.lower() for p in PERMANENT_ERRORS)


class Orchestrator:
    def __init__(self, registry: SkillRegistry, bob: "BobShellClient | HttpBobClient"):
        self.registry = registry
        self.bob = bob
        self.jobs: Dict[str, Job] = {}         # job_id -> job (in memory)
        self.repo_jobs: Dict[str, str] = {}    # repo_id -> job_id
        self._contexts: Dict[str, asyncio.Future] = {}
        self._tasks: set = set()
        self._bob_slots = asyncio.Semaphore(config.MAX_PARALLEL_SKILLS)
        config.WORKSPACE.mkdir(parents=True, exist_ok=True)
        config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
        self._load_jobs()

    # ---- persistence: jobs survive a backend restart ------------------
    def _save(self, job: Job):
        try:
            target = config.JOBS_DIR / f"{job.id}.json"
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(job.model_dump_json(), encoding="utf-8")
            os.replace(tmp, target)  # atomic
        except OSError:
            pass  # saving is best-effort; the job keeps running in memory

    def _load_jobs(self):
        """Reload saved jobs. Steps that were running when the backend stopped are marked
        failed, so they can be retried; the job never stays 'running' forever."""
        for path in sorted(config.JOBS_DIR.glob("*.json")):
            try:
                job = Job.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # skip a damaged file rather than refusing to start
            if job.repo.cloned and not self.repo_dir(job).is_dir():
                job.repo.cloned = False  # the clone was deleted since
            interrupted = "Interrupted: the backend restarted during this step. Use Retry to run it again."
            for name, step in job.steps.items():
                if step.status in ACTIVE:
                    if job.repo.cloned:
                        step.status, step.error = StepStatus.failed, interrupted
                    else:
                        step.status, step.error = StepStatus.skipped, "repository not available after restart"
                    step.finished_at = step.finished_at or time.time()
            self.jobs[job.id] = job
            self.repo_jobs[job.repo_id] = job.id
            self._recompute(job)
            self._save(job)

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
        self._save(job)
        return job

    def active_onboardings(self) -> int:
        return sum(j.status in (JobStatus.queued, JobStatus.running) for j in self.jobs.values())

    def prune_jobs(self, keep: int) -> List[str]:
        """Delete the oldest finished jobs (clone and saved file) so at most `keep` remain."""
        finished = sorted((j for j in self.jobs.values() if j.status not in (JobStatus.queued, JobStatus.running)),
                          key=lambda j: j.created_at)
        removed = []
        for job in finished[:max(0, len(self.jobs) - keep)]:
            try:
                if self.repo_dir(job).exists():
                    remove_tree(self.repo_dir(job))
                (config.JOBS_DIR / f"{job.id}.json").unlink(missing_ok=True)
            except OSError:
                continue  # try again next time; never block a new onboarding on cleanup
            self.jobs.pop(job.id, None)
            self.repo_jobs.pop(job.repo_id, None)
            self._contexts.pop(job.repo_id, None)
            removed.append(job.id)
        return removed

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
        self._recompute(job)
        self._save(job)

    @staticmethod
    def _recompute(job: Job):
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

    async def _call_bob(self, call) -> Tuple[Any, Optional[str]]:
        """Run one Bob call (a zero-argument coroutine factory), retrying temporary failures.
        Returns (result, error of the failed first attempt or None)."""
        first_error = None
        for attempt in range(config.BOB_RETRIES + 1):
            try:
                async with self._bob_slots:
                    return await call(), first_error
            except Exception as e:
                if attempt >= config.BOB_RETRIES or not is_transient(str(e)):
                    raise
                first_error = str(e)
                await asyncio.sleep(config.BOB_RETRY_DELAY)

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
        clone_started = time.time()
        self._update(job, clone_step, status=StepStatus.running, started_at=clone_started)
        try:
            await asyncio.to_thread(clone_with_retry, job.repo.url, job.repo.branch, dest, job.repo.depth)
            await asyncio.to_thread(check_clone_size, dest)
            job.repo.clone_seconds = round(time.time() - clone_started, 2)
            facts = await asyncio.to_thread(repo_facts, dest)
            job.repo.cloned = True
            job.repo.commit, job.repo.commits = facts["commit"], facts["commits"]
            job.repo.facts = facts["facts"]
            job.repo.branch = job.repo.branch or facts["branch"]
            try:  # a hand-off problem must never stop the onboarding
                job.bob_ide = await asyncio.to_thread(install_bob_ide_files, dest, self.registry)
            except Exception as e:
                job.bob_ide = {"error": f"Couldn't prepare the Bob IDE hand-off: {e}"}
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
            skill_md = Path(skill.path).read_text(encoding="utf-8")
            tools = (await asyncio.to_thread(run_local_tools, skill, self.repo_dir(job))
                     if skill.local_tools else None)
            if self.bob.reads_workspace:
                output, written = await self._run_with_shell(job, skill, mode, user_input, skill_md, started, tools)
            else:
                context = await self._context(job, skill, mode, user_input, tools=tools)
                output, retried = await self._call_bob(lambda: self.bob.run_skill(skill.name, skill_md, context))
                if retried:
                    output["retried_after"] = retried
                    output.setdefault("warnings", []).append(f"Bob failed once and was retried: {retried[:200]}")
                files = output.get("files")
                if isinstance(files, dict) and files:
                    written, rejected = await asyncio.to_thread(write_outputs, self.repo_dir(job), files)
                    if rejected:
                        output.setdefault("warnings", []).append(
                            f"Ignored files outside onboarding/ and README.md: {rejected}"
                        )
            if tools is not None:
                output["scanner"] = summarize_tools(tools)  # counts only, for the impact metrics
            if written:  # no secrets in outputs (enforced), then the output contract
                masked = await asyncio.to_thread(mask_output_files, self.repo_dir(job), written)
                if masked:
                    output["auto_masked"] = masked
                    output.setdefault("warnings", []).append(
                        f"Masked {sum(masked.values())} secret-looking value(s) Bob wrote in: {', '.join(masked)}")
                checks = await asyncio.to_thread(validate_outputs, self.repo_dir(job), written)
                output["validation"] = checks
                bad = {f: p for f, p in checks.items() if p}
                if bad:
                    output.setdefault("warnings", []).append(
                        "Output check: " + "; ".join(f"{f}: {', '.join(p)}" for f, p in bad.items()))
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
        try:  # metrics and the readable report must never break a run
            metrics = self.metrics(job)
            await asyncio.to_thread(write_metrics, self.repo_dir(job), metrics)
            report = build_onboarding_report(job, metrics, self.pipeline_names)
            await asyncio.to_thread(write_onboarding_report, self.repo_dir(job), report)
        except Exception:
            pass

    @property
    def pipeline_names(self) -> List[str]:
        entry = self.registry.entry
        return ([entry.name] if entry else [CLONE_EVENT]) + [s.name for s in self.registry.auto_after(CLONE_EVENT)]

    def metrics(self, job: Job) -> Dict[str, Any]:
        return compute_metrics(job, self.pipeline_names)

    async def _run_with_shell(
        self, job: Job, skill: SkillInfo, mode: str, user_input: Optional[Dict[str, Any]],
        skill_md: str, started: float, tools: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], List[str]]:
        """Bob Shell works in the clone: it reads the code and writes onboarding/ itself."""
        repo_dir = self.repo_dir(job)
        context = await self._context(job, skill, mode, user_input, include_repo=False, tools=tools)
        await asyncio.to_thread(write_skill_bundle, repo_dir, skill, skill_md, context)
        answer_mode = bool(user_input)
        prompt = shell_prompt(skill, answer_mode)
        result, retried = await self._call_bob(lambda: asyncio.to_thread(self.bob.run_prompt, prompt, repo_dir))
        handoff = frozenset((job.bob_ide or {}).get("installed", []))
        reverted = await asyncio.to_thread(contain_workspace, repo_dir, handoff)
        if handoff:  # undo any edit Bob made to the hand-off files
            await asyncio.to_thread(install_bob_ide_files, repo_dir, self.registry)
        written = await asyncio.to_thread(collect_outputs, repo_dir, skill, started)

        text = result["text"].strip()
        output: Dict[str, Any] = {"summary": text, "stats": result["stats"]}
        if answer_mode:
            output["answer"] = text
        warnings = []
        if retried:
            output["retried_after"] = retried
            warnings.append(f"Bob failed once and was retried: {retried[:200]}")
        if reverted:
            warnings.append(f"Undid changes outside onboarding/ and README.md: {reverted}")
        missing = [f for f in skill.produces if f not in written and not (repo_dir / f).is_file()]
        if missing:
            warnings.append(f"Bob did not write: {missing}")
        if warnings:
            output["warnings"] = warnings
        return output, written

    async def _context(
        self, job: Job, skill: SkillInfo, mode: str, user_input: Optional[Dict[str, Any]],
        include_repo: bool = True, tools: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        context: Dict[str, Any] = {
            "orchestrator": (
                {"mode": mode, "rules": RULES, "response_format": RESPONSE_FORMAT, "expected_files": skill.produces}
                if include_repo else {"mode": mode, "expected_files": skill.produces}
            ),
            "repo_id": job.repo_id,
            "job_id": job.id,
            "skill_name": skill.name,
            "clone": job.repo.model_dump(),
            "previous_results": {
                name: step.output for name, step in job.steps.items()
                if name != skill.name and step.status == StepStatus.success and step.output
            },
        }
        if include_repo:  # HTTP: Bob can't open the clone, so the files travel in the request
            context["repo"] = await self._repo_context(job)
        if skill.local_tools:
            context["local_tool_results"] = tools if tools is not None else await asyncio.to_thread(
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
            added = frozenset((job.bob_ide or {}).get("installed", []))
            future = asyncio.ensure_future(asyncio.to_thread(build_repo_context, self.repo_dir(job), added))
            self._contexts[job.repo_id] = future
        try:
            return await future
        except Exception:
            self._contexts.pop(job.repo_id, None)  # allow a retry
            raise
