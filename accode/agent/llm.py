"""Anthropic client wrapper for the agentic loop (multi-turn tool use).

Distinct from `accode/engine/llm.py`, which is the migration engine's
single-shot translation client. This one drives the tool-use conversation:
it sends the system prompt + tool schemas + message history and returns the
raw response so the loop can dispatch tool calls.
"""
from __future__ import annotations

import time
from typing import Any

import anthropic

# Rough Sonnet-class pricing, USD per million tokens. Adjust per your model.
_PRICE_INPUT = 3.00
_PRICE_OUTPUT = 15.00
_PRICE_CACHE_READ = 0.30
_PRICE_CACHE_WRITE = 3.75


class AgentLLM:
    def __init__(self, cfg: dict[str, Any]):
        api_key = cfg.get("anthropic", {}).get("api_key")
        if not api_key:
            raise ValueError(
                "Anthropic API key not set. Set the ANTHROPIC_API_KEY environment "
                "variable, or put anthropic.api_key in config.yaml."
            )
        self.client = anthropic.Anthropic(api_key=api_key)
        agent_cfg = cfg.get("agent", {})
        self.model: str = agent_cfg.get("model") or cfg["anthropic"]["model"]
        self.max_tokens: int = agent_cfg.get("max_tokens", 8192)
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read = 0
        self.cache_write = 0

    def turn(self, system: str, tools: list[dict], messages: list[dict], retries: int = 3):
        """Run one model turn. Returns the raw Anthropic response object."""
        # Cache the system prompt and the (constant) tool list so every turn
        # after the first re-reads them at ~10% of input-token cost.
        system_blocks = [{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }]
        cached_tools = [dict(t) for t in tools]
        if cached_tools:
            cached_tools[-1] = {**cached_tools[-1], "cache_control": {"type": "ephemeral"}}

        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                resp = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system_blocks,
                    tools=cached_tools,
                    messages=messages,
                )
                usage = resp.usage
                self.input_tokens += usage.input_tokens
                self.output_tokens += usage.output_tokens
                self.cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
                self.cache_write += getattr(usage, "cache_creation_input_tokens", 0) or 0
                return resp
            except anthropic.RateLimitError as exc:
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(20 * (attempt + 1))
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(5)
        assert last_exc is not None
        raise last_exc

    def usage_summary(self) -> str:
        cost = (
            self.input_tokens / 1e6 * _PRICE_INPUT
            + self.output_tokens / 1e6 * _PRICE_OUTPUT
            + self.cache_read / 1e6 * _PRICE_CACHE_READ
            + self.cache_write / 1e6 * _PRICE_CACHE_WRITE
        )
        return (
            f"tokens — in:{self.input_tokens} out:{self.output_tokens} "
            f"cache_read:{self.cache_read} cache_write:{self.cache_write}  |  "
            f"est. cost ≈ ${cost:.4f}"
        )
