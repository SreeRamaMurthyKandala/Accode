"""Anthropic Claude wrapper with prompt caching and automatic retries."""
import re
import time
from typing import Any
import anthropic


_FENCE_RE = re.compile(
    r"\A\s*```(?:python|sql|bash|sh|yaml|xml|json|hql)?\s*\n(.*?)\n```\s*\Z",
    re.DOTALL,
)


def strip_markdown_fences(text: str) -> str:
    """Remove a single surrounding ```lang ... ``` fence if present.

    Claude occasionally wraps output in a markdown code fence despite being
    told not to. Strip it so the file is valid in its target language.
    """
    m = _FENCE_RE.match(text)
    if m:
        return m.group(1)
    return text


class LLMClient:
    def __init__(self, cfg: dict[str, Any]):
        api_key = cfg["anthropic"].get("api_key")
        if not api_key:
            raise ValueError(
                "Anthropic API key not set.\n"
                "  Option 1: Set the ANTHROPIC_API_KEY environment variable.\n"
                "  Option 2: Add 'anthropic.api_key: <key>' to config.yaml."
            )
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model: str = cfg["anthropic"].get("model", "claude-sonnet-4-6")
        self.max_tokens: int = cfg["anthropic"].get("max_tokens", 8192)

    def convert(self, system_prompt: str, user_content: str, retries: int = 3) -> str:
        """Send a conversion request to Claude.

        The system_prompt is cached (ephemeral) so repeated calls with the
        same prompt hit the cache rather than re-tokenising.
        """
        for attempt in range(retries):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=[{
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }],
                    messages=[{"role": "user", "content": user_content}],
                )
                return strip_markdown_fences(response.content[0].text)
            except anthropic.RateLimitError:
                if attempt < retries - 1:
                    wait = 30 * (attempt + 1)
                    time.sleep(wait)
                else:
                    raise
            except anthropic.APIError:
                if attempt < retries - 1:
                    time.sleep(5)
                else:
                    raise
        return ""
