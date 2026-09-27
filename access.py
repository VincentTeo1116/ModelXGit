"""Guards for a public deployment: the access code and the usage limits.

Everything here is off unless it is configured (ACCESS_CODE, MAX_* in the environment),
so running on your own computer works exactly as before.
"""
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, Optional

import httpx

import config

COOKIE = "modelxgit_session"


# ---- access code -----------------------------------------------------------------------
def gate_on() -> bool:
    return bool(config.ACCESS_CODE)


def _key() -> bytes:
    # The access code is part of the key: changing the code signs everyone out.
    return f"{config.SESSION_SECRET}:{config.ACCESS_CODE}".encode()


def _sign(expires: int) -> str:
    return hmac.new(_key(), str(expires).encode(), hashlib.sha256).hexdigest()


def code_matches(code: str) -> bool:
    return gate_on() and hmac.compare_digest((code or "").strip().encode(), config.ACCESS_CODE.encode())


def make_session(now: Optional[float] = None) -> str:
    expires = int((now or time.time()) + config.SESSION_DAYS * 86400)
    return f"{expires}.{_sign(expires)}"


def session_valid(token: Optional[str]) -> bool:
    if not gate_on():
        return True
    try:
        expires_s, sig = (token or "").split(".", 1)
        expires = int(expires_s)
    except ValueError:
        return False
    return hmac.compare_digest(sig, _sign(expires)) and expires > time.time()


# ---- usage limits ----------------------------------------------------------------------
class LimitError(Exception):
    """A usage limit was reached; the message is shown to the user as is."""


LIMITS = {"onboardings": "MAX_ONBOARDINGS_PER_DAY", "skill_runs": "MAX_SKILL_RUNS_PER_DAY"}


class Usage:
    """Counts today's onboardings and extra skill runs (questions, README, retries).
    Saved next to the jobs so a restart doesn't reset the day's count. Days are UTC."""

    def __init__(self, path: Path):
        self.path = path
        self.day, self.counts = self._today(), {k: 0 for k in LIMITS}
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("day") == self.day:
                self.counts.update({k: int(saved.get(k, 0)) for k in LIMITS})
        except (OSError, ValueError):
            pass

    @staticmethod
    def _today() -> str:
        return dt.datetime.now(dt.timezone.utc).date().isoformat()

    def _roll(self):
        if self.day != self._today():
            self.day, self.counts = self._today(), {k: 0 for k in LIMITS}

    @staticmethod
    def limit(kind: str) -> int:
        return getattr(config, LIMITS[kind])

    def check(self, kind: str):
        self._roll()
        limit = self.limit(kind)
        if limit and self.counts[kind] >= limit:
            what = "onboardings" if kind == "onboardings" else "questions and extra skill runs"
            raise LimitError(f"Today's limit of {limit} {what} on this server is used up. "
                             "It resets at midnight UTC.")

    def add(self, kind: str):
        self._roll()
        self.counts[kind] += 1
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"day": self.day, **self.counts}), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass  # counting is best-effort; never block a run on it

    def snapshot(self) -> Dict[str, Dict[str, int]]:
        self._roll()
        return {k: {"used": self.counts[k], "limit": self.limit(k)} for k in LIMITS}


# ---- which repositories this server accepts --------------------------------------------
HOST = re.compile(r"^(?:https://|ssh://[^@/]+@|[^@/]+@)([A-Za-z0-9.-]+)")
GITHUB = re.compile(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/([^/]+?)(?:\.git)?/?$")


def repo_host(url: str) -> str:
    m = HOST.match(url)
    return m.group(1).lower() if m else ""


def check_host(url: str):
    allowed = config.ALLOWED_GIT_HOSTS
    if allowed and repo_host(url) not in allowed:
        raise LimitError(f"This server only onboards repositories from {', '.join(allowed)}.")


async def check_repo_size(url: str):
    """Refuse a GitHub repo over MAX_REPO_MB before cloning it. Other hosts, or a failed
    lookup, fall through: the size is checked again after the clone."""
    m = GITHUB.match(url)
    if not (config.MAX_REPO_MB and m):
        return
    try:
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": config.USER_AGENT}) as client:
            r = await client.get(f"https://api.github.com/repos/{m.group(1)}/{m.group(2)}")
        size_mb = r.json()["size"] / 1024 if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        size_mb = None
    if size_mb and size_mb > config.MAX_REPO_MB:
        raise LimitError(f"This repository is about {size_mb:,.0f} MB. This server accepts up to "
                         f"{config.MAX_REPO_MB} MB; run ModelXGit on your own computer for bigger repos.")
