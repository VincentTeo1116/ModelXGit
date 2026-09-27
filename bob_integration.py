"""How the backend talks to IBM Bob (the only module that does).

Two clients with the same `kind` / `configured` / `url` attributes:
  BobShellClient - runs IBM Bob Shell headless (`bob run`) inside the cloned repo. This is
                   the official way to automate Bob with an API key; Bob reads the code itself.
  HttpBobClient  - posts the skill + context to an HTTP endpoint (the stand-in in
                   devtools/mock_bob.py, or an endpoint the organisers confirm).
"""
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

import config


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


class BobShellClient:
    kind = "shell"
    reads_workspace = True  # Bob opens the repo's files itself

    def __init__(self):
        self.api_key = config.BOB_API_KEY
        self.script = self._find_bob_js()
        self.url = f"bob-shell: {self.script}" if self.script else "bob-shell: not found"

    @staticmethod
    def _find_bob_js() -> Optional[str]:
        """bobshell's dist/bob.js. Run with node directly: no .cmd/.ps1 wrapper, so no shell quoting."""
        candidates = [config.BOB_SHELL_JS] if config.BOB_SHELL_JS else []
        bob = shutil.which("bob")
        if bob:
            candidates.append(str(Path(bob).resolve().parent / "node_modules" / "bobshell" / "dist" / "bob.js"))
            candidates.append(str(Path(bob).resolve()))  # Linux/macOS: the symlink target is bob.js
        if os.name == "nt" and os.getenv("APPDATA"):
            candidates.append(str(Path(os.environ["APPDATA"]) / "npm" / "node_modules" / "bobshell" / "dist" / "bob.js"))
        return next((c for c in candidates if c and c.endswith(".js") and Path(c).is_file()), None)

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.script and shutil.which("node"))

    def _command(self, prompt: str, workspace: Path) -> List[str]:
        cmd = [shutil.which("node") or "node", self.script, "run", prompt,
               "--format", "json", "--workspace", str(workspace),
               "--max-turns", str(config.BOB_MAX_TURNS), "--log-level", "error"]
        if config.BOB_MAX_COST:
            cmd += ["--max-cost", config.BOB_MAX_COST]
        if config.BOB_DISABLED_TOOL_GROUPS:
            cmd += ["--disable-tool-groups", config.BOB_DISABLED_TOOL_GROUPS]
        if config.BOB_TEAM_ID:
            cmd += ["--team-id", config.BOB_TEAM_ID]
        if config.BOB_ACCEPT_LICENSE:
            cmd.append("--accept-license")
        return cmd

    def run_prompt(self, prompt: str, workspace: Path) -> Dict[str, Any]:
        """Blocking: run one headless Bob task in `workspace`. Returns {"text", "stats"}."""
        if not self.api_key:
            raise RuntimeError("BOB_API_KEY is not set: add it to the backend's .env file.")
        if not self.script:
            raise RuntimeError("Bob Shell not found: install it (npm install -g bobshell) or set BOB_SHELL_JS.")
        env = {**os.environ, "BOBSHELL_API_KEY": self.api_key, "FORCE_COLOR": "0", "NO_COLOR": "1"}
        try:
            r = subprocess.run(
                self._command(prompt, workspace), cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=config.BOB_SHELL_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Bob Shell timed out after {config.BOB_SHELL_TIMEOUT}s")
        err = _redact(r.stderr.strip(), self.api_key)
        if "license agreement is required" in err.lower():
            raise RuntimeError(
                "Bob Shell needs the IBM license accepted: run `bob` once and accept it, "
                "or set BOB_ACCEPT_LICENSE=true in .env after reading it (bob --show-license)."
            )
        result = None
        for line in reversed(r.stdout.strip().splitlines()):  # the result is the last JSON line
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("type") == "result":
                result = data
                break
        if r.returncode != 0 or not result or result.get("status") != "success":
            detail = err or _redact(r.stdout.strip(), self.api_key)
            raise RuntimeError(f"Bob Shell failed (exit {r.returncode}): {detail[-800:] or 'no output'}")
        return {"text": result.get("last_message") or "", "stats": result.get("stats") or {}}


class HttpBobClient:
    kind = "http"
    reads_workspace = False  # the repo's files are sent in the request

    def __init__(self):
        self.api_key = config.BOB_API_KEY
        self.url = f"{config.BOB_API_ENDPOINT}/{config.BOB_SKILLS_PATH.lstrip('/')}"
        self.timeout = config.BOB_TIMEOUT

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def run_skill(self, skill_name: str, skill_md: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Send a SKILL.md plus its context to Bob and return Bob's JSON response."""
        if not self.api_key:
            raise RuntimeError("BOB_API_KEY is not set: add it to the backend's .env file.")

        payload = {"skill": skill_md, "skill_name": skill_name, "context": context}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": config.USER_AGENT,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(self.url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status in (401, 403):
                    raise RuntimeError(
                        f"Bob API rejected the request (HTTP {status}) at {self.url}. "
                        "This endpoint is not a public API; use BOB_CLIENT=shell."
                    ) from e
                if status == 404:
                    raise RuntimeError(f"Bob API path not found (HTTP 404) at {self.url}.") from e
                raise RuntimeError(f"Bob API error {status}: {e.response.text[:1000]}") from e
            except httpx.RequestError as e:
                raise RuntimeError(f"Bob API unreachable at {self.url}: {e}") from e
            except ValueError as e:
                raise RuntimeError(f"Bob API returned a non-JSON response from {self.url}") from e

        return data if isinstance(data, dict) else {"result": data}


def make_bob_client():
    return HttpBobClient() if config.BOB_CLIENT == "http" else BobShellClient()
