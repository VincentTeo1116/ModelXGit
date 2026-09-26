"""Thin async client for the IBM Bob 2.0 API (the only place that talks to Bob)."""
from typing import Any, Dict, Optional

import httpx

import config


class BobClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        skills_path: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        self.api_key = api_key if api_key is not None else config.BOB_API_KEY
        endpoint = (endpoint or config.BOB_API_ENDPOINT).rstrip("/")
        self.url = f"{endpoint}/{(skills_path or config.BOB_SKILLS_PATH).lstrip('/')}"
        self.timeout = timeout or config.BOB_TIMEOUT

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def run_skill(
        self, skill_name: str, skill_md: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
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
                        "Check BOB_API_KEY, and that BOB_API_ENDPOINT and "
                        "BOB_SKILLS_PATH match the official Bob API docs."
                    ) from e
                if status == 404:
                    raise RuntimeError(
                        f"Bob API path not found (HTTP 404) at {self.url}. "
                        "Set BOB_SKILLS_PATH to the documented endpoint."
                    ) from e
                raise RuntimeError(f"Bob API error {status}: {e.response.text[:1000]}") from e
            except httpx.RequestError as e:
                raise RuntimeError(f"Bob API unreachable at {self.url}: {e}") from e
            except ValueError as e:
                raise RuntimeError(f"Bob API returned a non-JSON response from {self.url}") from e

        return data if isinstance(data, dict) else {"result": data}
