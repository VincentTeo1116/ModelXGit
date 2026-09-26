# backend/bob_integration.py
import os
import httpx
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables from .env file
load_dotenv()

class BobClient:
    """Client for interacting with the IBM Bob 2.0 API."""

    def __init__(self):
        self.api_key = os.getenv("BOB_API_KEY")
        self.api_endpoint = os.getenv("BOB_API_ENDPOINT")
        
        if not self.api_key:
            raise ValueError("BOB_API_KEY is not set in your environment or .env file.")
        if not self.api_endpoint:
            raise ValueError("BOB_API_ENDPOINT is not set in your environment or .env file.")
        
        # Base URL for the Bob API
        self.base_url = f"{self.api_endpoint}/inference/v1"

    async def run_skill(self, skill_path: Path, context: dict) -> dict:
        """
        Sends a skill and its context to the Bob API for execution.
        
        Args:
            skill_path: The path to the SKILL.md file.
            context: A dictionary with repository context (repo_id, repo_path, etc.).
            
        Returns:
            The JSON response from the Bob API.
        """
        skill_content = skill_path.read_text(encoding="utf-8")

        payload = {
            "skill": skill_content,
            "context": context,
            # Optional: specify model or other parameters if needed
            # "model": "bob-default"
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # Bob's Cloudflare WAF may require a specific User-Agent
            "User-Agent": "ibm-bob-openwiki-provider"
        }

        async with httpx.AsyncClient(timeout=300.0) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/skills/run",
                    json=payload,
                    headers=headers
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                # Provide more context on API errors
                raise RuntimeError(f"Bob API request failed: {e.response.status_code} - {e.response.text}")
            except httpx.RequestError as e:
                raise RuntimeError(f"Could not connect to Bob API: {e}")