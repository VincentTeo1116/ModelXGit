"""Backend settings, read once from the environment and the backend's .env file."""
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")  # the .env next to this file, whatever the working directory


def _int(name: str, default: int) -> int:
    return int(os.getenv(name) or default)


# Bob API. The path is NOT verified against an official Bob API spec: confirm it
# with the organisers and set BOB_SKILLS_PATH.
BOB_API_KEY = os.getenv("BOB_API_KEY", "")
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

CLONE_TIMEOUT = _int("CLONE_TIMEOUT", 600)
LOCAL_TOOL_TIMEOUT = _int("LOCAL_TOOL_TIMEOUT", 300)
MAX_PARALLEL_SKILLS = _int("MAX_PARALLEL_SKILLS", 3)

# Size limits for the repo content sent to Bob (characters).
REPO_CONTEXT_MAX_CHARS = _int("REPO_CONTEXT_MAX_CHARS", 1_000_000)
REPO_FILE_MAX_CHARS = _int("REPO_FILE_MAX_CHARS", 400_000)

CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
