"""Backend settings, read once from the environment and the backend's .env file."""
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")  # the .env next to this file, whatever the working directory


def _int(name: str, default: int) -> int:
    return int(os.getenv(name) or default)


def _bool(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes")


# How skills reach Bob:
#   shell - IBM Bob Shell's headless mode (`bob run`), the official way to automate Bob
#           with an API key. Bob reads the cloned repo itself.
#   http  - direct HTTP call. The endpoint is NOT an official public API: it is behind
#           a firewall that blocks third-party clients. Use it only with the stand-in
#           (devtools/mock_bob.py) or an endpoint the organisers confirm.
BOB_CLIENT = os.getenv("BOB_CLIENT", "shell").strip().lower()
BOB_API_KEY = os.getenv("BOB_API_KEY") or os.getenv("BOBSHELL_API_KEY") or ""

# Bob Shell (BOB_CLIENT=shell)
BOB_SHELL_JS = os.getenv("BOB_SHELL_JS", "")         # bobshell's dist/bob.js; auto-detected when empty
BOB_TEAM_ID = os.getenv("BOB_TEAM_ID", "")           # only for API keys of type "general"
BOB_ACCEPT_LICENSE = _bool("BOB_ACCEPT_LICENSE")     # set to true only after accepting the IBM license
BOB_MAX_TURNS = _int("BOB_MAX_TURNS", 30)
BOB_MAX_COST = os.getenv("BOB_MAX_COST", "")         # per skill run, empty = no limit
BOB_SHELL_TIMEOUT = _int("BOB_SHELL_TIMEOUT", 900)
# Pipeline runs may read and edit files only: no commands, browsing, MCP servers, sub-agents,
# other skills or mode switches (the clone's .bob/ holds skills and modes for Bob IDE).
BOB_DISABLED_TOOL_GROUPS = os.getenv("BOB_DISABLED_TOOL_GROUPS", "execute,browser,mcp,subagent,skill,mode")

# Bob IDE hand-off: the `bobide` launcher; auto-detected when empty.
BOB_IDE_CMD = os.getenv("BOB_IDE_CMD", "")

# Direct HTTP (BOB_CLIENT=http)
BOB_API_ENDPOINT = os.getenv("BOB_API_ENDPOINT", "https://api.us-east.bob.ibm.com").rstrip("/")
BOB_SKILLS_PATH = os.getenv("BOB_SKILLS_PATH", "/inference/v1/skills/run")
BOB_TIMEOUT = float(os.getenv("BOB_TIMEOUT") or 300)
# Identify this app honestly; never borrow another client's User-Agent.
USER_AGENT = "modelx-onboarding-backend/1.0"

SKILLS_DIR = BASE_DIR / "skills"

# Absolute path with a drive letter on Windows: git rejects drive-less paths.
WORKSPACE = Path(
    os.getenv("WORKSPACE_DIR") or Path(tempfile.gettempdir()) / "hackathon_workspaces"
).resolve()

# Jobs are saved here as JSON so they survive a backend restart.
JOBS_DIR = Path(os.getenv("JOBS_DIR") or WORKSPACE / "_jobs").resolve()

# A failed Bob run is retried this many times after a short pause, but only for
# temporary problems (timeouts, network errors, 5xx/429), never for a bad key or license.
BOB_RETRIES = _int("BOB_RETRIES", 1)
BOB_RETRY_DELAY = float(os.getenv("BOB_RETRY_DELAY") or 3)

CLONE_TIMEOUT = _int("CLONE_TIMEOUT", 600)
LOCAL_TOOL_TIMEOUT = _int("LOCAL_TOOL_TIMEOUT", 300)
MAX_PARALLEL_SKILLS = _int("MAX_PARALLEL_SKILLS", 3)

# Size limits for the repo content sent to Bob (characters).
REPO_CONTEXT_MAX_CHARS = _int("REPO_CONTEXT_MAX_CHARS", 1_000_000)
REPO_FILE_MAX_CHARS = _int("REPO_FILE_MAX_CHARS", 400_000)

# Impact metrics: your team's own measured time to onboard a repo by hand (minutes).
# Leave empty rather than guessing; the app then shows no comparison.
MANUAL_BASELINE_MINUTES = float(os.getenv("MANUAL_BASELINE_MINUTES") or 0) or None

CORS_ORIGINS =[o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]


# ---- Public deployment (e.g. Render). Everything below is off when empty. ---------------
# ACCESS_CODE: when set, the app asks for this code before anything else. Use a long random one.
ACCESS_CODE = os.getenv("ACCESS_CODE", "").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "")     # signs the sign-in cookie; set a random value
SESSION_DAYS = _int("SESSION_DAYS", 7)               # how long a sign-in lasts

# Usage limits (0 = no limit). Days are UTC.
MAX_ONBOARDINGS_PER_DAY = _int("MAX_ONBOARDINGS_PER_DAY", 0)
MAX_SKILL_RUNS_PER_DAY = _int("MAX_SKILL_RUNS_PER_DAY", 0)   # questions, README runs and retries
MAX_ACTIVE_ONBOARDINGS = _int("MAX_ACTIVE_ONBOARDINGS", 0)   # onboardings running at the same time
MAX_REPO_MB = _int("MAX_REPO_MB", 0)                         # repository size on disk
MAX_STORED_JOBS = _int("MAX_STORED_JOBS", 0)                 # older finished ones are deleted
ALLOWED_GIT_HOSTS = [h.strip().lower() for h in os.getenv("ALLOWED_GIT_HOSTS", "").split(",") if h.strip()]
