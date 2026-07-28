from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class ClaudeError(RuntimeError):
    pass


class ClaudeClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        self.model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6").strip()
        self.timeout = int(os.getenv("CLAUDE_TIMEOUT_SECONDS", "60"))

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def create_message(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1200,
        json_schema: dict | None = None,
    ) -> str:
        if not self.configured:
            raise ClaudeError("ANTHROPIC_API_KEY is not configured")

        payload: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if json_schema:
            payload["output_config"] = {
                "format": {"type": "json_schema", "schema": json_schema}
            }

        # Need to update the request payload later once model is decided
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise ClaudeError(f"Claude API returned HTTP {error.code}: {detail[:500]}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise ClaudeError(f"Could not reach Claude API: {error}") from error

        text_parts = [part.get("text", "") for part in body.get("content", []) if part.get("type") == "text"]
        if not text_parts:
            raise ClaudeError("Claude API returned no text content")
        return "".join(text_parts)
