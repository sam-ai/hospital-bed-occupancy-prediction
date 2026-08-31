"""Client for the AAVA agent execution platform.

AAVA agents are invoked asynchronously:
1. POST /agents/execute/agent-executions submits a job and returns an
   `agentExecutionId`.
2. GET /agents/execute/history/execution?execution_id=... is polled until
   the job reaches a terminal status (SUCCESS/FAILED), returning the
   agent's JSON-encoded `output` string.

Usage:
    from app.integrations.aava_client import AAVAClient

    client = AAVAClient()
    output = await client.execute_agent(agent_id="56091", content={"foo": "bar"})
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import aiohttp

from app.config import (
    AAVA_API_KEY,
    AAVA_BASE_URL,
    AAVA_POLL_INTERVAL_SECONDS,
    AAVA_POLL_TIMEOUT_SECONDS,
)

_TERMINAL_STATUSES = {"SUCCESS", "FAILED", "ERROR"}


class AAVAExecutionError(RuntimeError):
    """Raised when an AAVA agent execution fails or times out."""


class AAVAClient:
    """Minimal async client for submitting and polling AAVA agent jobs."""

    def __init__(
        self,
        base_url: str = AAVA_BASE_URL,
        api_key: str = AAVA_API_KEY,
        poll_interval: float = AAVA_POLL_INTERVAL_SECONDS,
        poll_timeout: float = AAVA_POLL_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def submit_job(self, agent_id: str, content: dict[str, Any] | str) -> str:
        """Submit an agent execution job. Returns the `agentExecutionId`."""
        content_str = content if isinstance(content, str) else json.dumps(content)
        user_inputs = json.dumps({"{{content}}": content_str})
        execution_id = str(uuid.uuid4())

        form = aiohttp.FormData(default_to_multipart=True)
        form.add_field("agentId", str(agent_id))
        form.add_field("userInputs", user_inputs)
        form.add_field("executionId", execution_id)

        url = f"{self._base_url}/agents/execute/agent-executions"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=form, headers=self._headers()) as resp:
                if resp.status >= 400:
                    error_text = await resp.text()
                    raise AAVAExecutionError(
                        f"Agent job submission failed ({resp.status}): {error_text}"
                    )
                body = await resp.json()

        if body.get("status") != "SUCCESS":
            raise AAVAExecutionError(f"Agent job submission failed: {body}")

        agent_execution_id = body.get("data", {}).get("agentExecutionId")
        if not agent_execution_id:
            raise AAVAExecutionError(f"No agentExecutionId in response: {body}")
        return agent_execution_id

    async def get_execution(self, agent_execution_id: str) -> dict[str, Any]:
        """Fetch a single execution history record (may still be RUNNING)."""
        url = f"{self._base_url}/agents/execute/history/execution"
        params = {"execution_id": agent_execution_id}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=self._headers()) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def poll_until_complete(self, agent_execution_id: str) -> dict[str, Any]:
        """Poll the history endpoint until the job reaches a terminal status."""
        elapsed = 0.0
        while elapsed <= self._poll_timeout:
            record = await self.get_execution(agent_execution_id)
            if record.get("status") in _TERMINAL_STATUSES:
                if record["status"] != "SUCCESS":
                    raise AAVAExecutionError(f"Agent execution failed: {record}")
                return record
            await asyncio.sleep(self._poll_interval)
            elapsed += self._poll_interval

        raise AAVAExecutionError(
            f"Timed out after {self._poll_timeout}s waiting for execution {agent_execution_id}"
        )

    async def execute_agent(
        self, agent_id: str, content: dict[str, Any] | str
    ) -> dict[str, Any]:
        """Submit a job and poll until complete. Returns the parsed `output` JSON.

        The AAVA `output` field is itself a JSON-encoded string; this method
        parses it and returns the resulting dict.
        """
        agent_execution_id = await self.submit_job(agent_id, content)
        record = await self.poll_until_complete(agent_execution_id)

        raw_output = record.get("output", "")
        try:
            return json.loads(raw_output)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AAVAExecutionError(
                f"Could not parse agent output as JSON: {raw_output!r}"
            ) from exc
