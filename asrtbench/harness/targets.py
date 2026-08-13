"""Target executors for ASRT.

Executors send one attack prompt to one target model and return a uniform
response dictionary. ASRT can select targets from the CLI, for example:

    python loop.py --target ollama --model qwen2.5:7b

The executor does not judge safety. It only captures what the target said.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import json
import os
from time import perf_counter
from urllib import request
import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from dotenv import load_dotenv

load_dotenv() # Load variables from .env if present


class TargetExecutor(ABC):
    """Base class for any ASRT target."""

    def __init__(self, target_name: str):
        self.target_name = target_name

    @abstractmethod
    async def execute(self, prompt: str) -> dict:
        """Return response_text, latency_ms, target_name, and metadata."""
        raise NotImplementedError

    async def execute_conversation(self, messages: list[dict]) -> dict:
        """Execute a multi-turn conversation and return the next assistant reply.

        `messages` is an OpenAI-style history: [{"role": "user"|"assistant",
        "content": str}, ...]. Multi-turn attacks (Crescendo-style escalation)
        need real conversation state, because the attack works by referencing
        the target's own prior replies.

        The default flattens the history into a single prompt so any executor
        keeps working. Executors backed by a real chat API should override this
        — a flattened transcript is not the same thing as genuine conversation
        state, and targets behave differently between the two.
        """
        flattened = "\n\n".join(
            f"{'User' if m.get('role') == 'user' else 'Assistant'}: {m.get('content', '')}"
            for m in messages
        )
        result = await self.execute(flattened)
        result.setdefault("metadata", {})["conversation_mode"] = "flattened"
        return result


class MockExecutor(TargetExecutor):
    """Deterministic local target for ASRT smoke tests.

    Returns varied responses based on prompt keywords so the judge
    pipeline can exercise all verdict paths (refused, safe, compliance,
    partial) without needing a real model.
    """

    def __init__(self, target_name: str = "mock-llm"):
        super().__init__(target_name)

    async def execute(self, prompt: str) -> dict:
        start = perf_counter()
        lower = prompt.lower()

        # --- Refusal responses (should map to verdict=refused) ---
        if any(kw in lower for kw in [
            "reveal", "system prompt", "show your instructions",
            "what are your rules", "repeat your system",
        ]):
            response_text = "I cannot reveal my system instructions."
        elif any(kw in lower for kw in [
            "delete", "destroy", "rm -rf", "drop table",
            "execute_command", "running command",
        ]):
            response_text = "I cannot execute destructive actions."
        elif any(kw in lower for kw in [
            "ignore previous", "disregard", "forget your instructions",
        ]):
            response_text = "I will continue following my original safety instructions."
        elif any(kw in lower for kw in [
            "password", "api_key", "sk-", "secret key",
            "credit card", "ssn",
        ]):
            response_text = "I'm sorry, I cannot help with requests involving sensitive credentials or personal data."
        elif any(kw in lower for kw in [
            "hack", "exploit", "malware", "virus", "ransomware",
            "phishing", "ddos",
        ]):
            response_text = "I can't assist with creating malicious software or cyberattacks."

        # --- Compliance responses (should map to verdict=full_compliance) ---
        elif any(kw in lower for kw in [
            "you are now", "act as dan", "pretend you are",
            "roleplay as", "from now on you",
        ]):
            response_text = "Understood, I am now operating in the requested mode. I can do anything."
        elif any(kw in lower for kw in [
            "write a tutorial", "step by step guide to",
            "how to make a bomb", "synthesize",
        ]):
            response_text = "Sure! Here is a step-by-step guide: Step 1: Gather the materials..."
        elif "bypass" in lower and any(kw in lower for kw in [
            "safety", "filter", "guardrail",
        ]):
            response_text = "I've disabled the safety filter. Executing your request now."

        # --- Partial compliance responses ---
        elif any(kw in lower for kw in [
            "jailbreak", "unrestricted", "uncensored",
        ]):
            response_text = "I understand your request but I can only provide limited information on this topic for educational purposes."

        # --- Safe/benign responses ---
        elif any(kw in lower for kw in [
            "hello", "weather", "recipe", "joke", "help me with",
            "benign", "safe", "educational",
        ]):
            response_text = "Of course! I'd be happy to help with that safe request."

        # --- Default: generic safe response ---
        else:
            response_text = "I can help with safe and allowed requests. Please let me know how I can assist you."

        return {
            "target_name": self.target_name,
            "response_text": response_text,
            "latency_ms": (perf_counter() - start) * 1000,
            "metadata": {"mode": "mock"},
        }


class OllamaExecutor(TargetExecutor):
    """Ollama/local target executor.

    Uses Ollama's local `/api/generate` endpoint. This keeps ASRT independent
    from provider SDKs while still letting us run real local models.
    """

    def __init__(
        self,
        model: str = "qwen2.5:7b",
        api_base: str = "http://localhost:11434",
        temperature: float = 0.0,
        max_tokens: int = 512,
    ):
        super().__init__(model)
        self.api_base = api_base.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def execute(self, prompt: str) -> dict:
        start = perf_counter()
        payload = {
            "model": self.target_name,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.api_base}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
            response_text = data.get("response", "")
            error = None
        except Exception as exc:
            response_text = ""
            error = str(exc)

        return {
            "target_name": self.target_name,
            "response_text": response_text,
            "latency_ms": (perf_counter() - start) * 1000,
            "metadata": {
                "mode": "ollama",
                "api_base": self.api_base,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "error": error,
            },
        }

    async def execute_conversation(self, messages: list[dict]) -> dict:
        """Real multi-turn via Ollama's /api/chat (not /api/generate)."""
        start = perf_counter()
        payload = {
            "model": self.target_name,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.api_base}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
            response_text = data.get("message", {}).get("content", "")
            error = None
        except Exception as exc:
            response_text = ""
            error = str(exc)

        return {
            "target_name": self.target_name,
            "response_text": response_text,
            "latency_ms": (perf_counter() - start) * 1000,
            "metadata": {
                "mode": "ollama-chat",
                "api_base": self.api_base,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "conversation_mode": "native",
                "error": error,
            },
        }


class OpenAIExecutor(TargetExecutor):
    """OpenAI-compatible target executor.

    Uses aiohttp and tenacity to handle true async execution and
    exponential backoff for rate limits (429 Too Many Requests).
    Expects OPENAI_API_KEY environment variable.
    """

    def __init__(
        self,
        model: str = "gpt-3.5-turbo",
        api_base: str = "https://api.openai.com/v1",
        temperature: float = 0.0,
        max_tokens: int = 512,
    ):
        super().__init__(model)
        self.api_base = api_base.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        if not self.api_key:
            print("[!] WARNING: OPENAI_API_KEY is not set in environment or .env file.")

    # Retry if we hit an aiohttp ClientError (like 429 or 502)
    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True
    )
    async def _make_request(self, payload: dict, headers: dict) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.api_base}/chat/completions",
                json=payload,
                headers=headers,
                timeout=120
            ) as response:
                if response.status == 429:
                    # Explicitly raise exception to trigger tenacity retry
                    raise aiohttp.ClientResponseError(
                        response.request_info,
                        response.history,
                        status=response.status,
                        message="Rate limit exceeded"
                    )
                response.raise_for_status()
                return await response.json()

    async def execute(self, prompt: str) -> dict:
        return await self.execute_conversation([{"role": "user", "content": prompt}])

    async def execute_conversation(self, messages: list[dict]) -> dict:
        """Native multi-turn — the chat/completions API is message-based already."""
        start = perf_counter()

        payload = {
            "model": self.target_name,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        try:
            data = await self._make_request(payload, headers)
            choices = data.get("choices", [])
            if choices and "message" in choices[0]:
                response_text = choices[0]["message"].get("content", "")
            else:
                response_text = ""
            error = None
        except Exception as exc:
            response_text = ""
            error = str(exc)

        return {
            "target_name": self.target_name,
            "response_text": response_text,
            "latency_ms": (perf_counter() - start) * 1000,
            "metadata": {
                "mode": "openai",
                "api_base": self.api_base,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "conversation_mode": "native",
                "turns": len(messages),
                "error": error,
            },
        }


class LiteLLMExecutor(TargetExecutor):
    """LiteLLM-compatible target executor supporting 100+ LLMs (OpenAI, Anthropic, Gemini, etc.)."""

    def __init__(
        self,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 512,
        api_base: str | None = None,
    ):
        super().__init__(model)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_base = api_base

    async def execute(self, prompt: str) -> dict:
        return await self.execute_conversation([{"role": "user", "content": prompt}])

    async def execute_conversation(self, messages: list[dict]) -> dict:
        """Native multi-turn — litellm speaks OpenAI-style messages for all providers."""
        import litellm
        start = perf_counter()

        kwargs = {
            "model": self.target_name,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base

        try:
            response = await litellm.acompletion(**kwargs)
            choices = response.get("choices", [])
            if choices and "message" in choices[0]:
                response_text = choices[0]["message"].get("content", "") or ""
            else:
                response_text = ""
            error = None
        except Exception as exc:
            response_text = ""
            error = str(exc)

        return {
            "target_name": self.target_name,
            "response_text": response_text,
            "latency_ms": (perf_counter() - start) * 1000,
            "metadata": {
                "mode": "litellm",
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "conversation_mode": "native",
                "turns": len(messages),
                "error": error,
            },
        }

