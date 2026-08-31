"""Runtime LLM wrapper shared by the preference extractor, Planner, and Judge.

Deliberately not shared code with `annotate_corpus.py`, even though the shape
is similar: that module is offline batch annotation, this is interactive
runtime, and the only things that should couple them are `schema.py` (shared
vocabulary) and `jsonutil.py` (shared JSON parsing). A direct import either
direction would be backwards -- runtime code depending on an offline batch
script, or vice versa.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

import config
from jsonutil import extract_json


class LLMError(RuntimeError):
    """Raised when a call exhausts retries without a usable, valid response."""


class LLMClient:
    def __init__(self, mock: bool = False):
        self.mock = mock
        self._client = None
        if not mock:
            from openai import OpenAI  # imported lazily so mock mode needs no dependency/key
            key = os.getenv("OPENAI_API_KEY")
            if not key:
                raise SystemExit(
                    "OPENAI_API_KEY is not set. Put it in .env, or construct "
                    "LLMClient(mock=True) to exercise the pipeline without a key."
                )
            self._client = OpenAI(api_key=key)

    def _raw_complete(self, prompt: str, temperature: float, max_tokens: int, json_mode: bool) -> str:
        kwargs: dict[str, Any] = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(
            model=config.MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        return resp.choices[0].message.content or ""

    def complete_json(
        self,
        prompt: str,
        *,
        temperature: float,
        max_tokens: int = 700,
        max_attempts: int = config.MAX_ATTEMPTS,
        validate: Callable[[dict[str, Any]], list[str]] | None = None,
        mock_fn: Callable[[str], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Call the model, parse JSON, validate, and retry on either failure.

        Mirrors the offline annotation pipeline's retry shape (call -> parse
        -> validate) because that shape, not the specific prompt, is what
        makes `gpt-3.5-turbo` usable for structured output.
        """
        errors: list[str] = []
        for attempt in range(1, max_attempts + 1):
            if self.mock:
                if mock_fn is None:
                    raise LLMError("mock client requires a mock_fn for this call")
                parsed = mock_fn(prompt)
            else:
                try:
                    raw = self._raw_complete(prompt, temperature, max_tokens, json_mode=True)
                except Exception as exc:  # transient API failure
                    errors.append(f"attempt {attempt}: api error: {exc}")
                    if attempt < max_attempts:
                        time.sleep(2 ** attempt)
                    continue
                parsed = extract_json(raw)
                if parsed is None:
                    errors.append(f"attempt {attempt}: unparseable JSON")
                    continue

            problems = validate(parsed) if validate else []
            if problems:
                errors.append(f"attempt {attempt}: " + "; ".join(problems[:4]))
                if attempt < max_attempts:
                    continue
                raise LLMError(f"validation failed after {max_attempts} attempts: {errors}")
            return parsed

        raise LLMError(f"no usable response after {max_attempts} attempts: {errors}")

    def complete_text(
        self,
        prompt: str,
        *,
        temperature: float,
        max_tokens: int = 900,
        max_attempts: int = config.MAX_ATTEMPTS,
        mock_fn: Callable[[str], str] | None = None,
    ) -> str:
        if self.mock:
            if mock_fn is None:
                raise LLMError("mock client requires a mock_fn for this call")
            return mock_fn(prompt)
        errors: list[str] = []
        for attempt in range(1, max_attempts + 1):
            try:
                return self._raw_complete(prompt, temperature, max_tokens, json_mode=False)
            except Exception as exc:  # transient API failure
                errors.append(f"attempt {attempt}: api error: {exc}")
                if attempt < max_attempts:
                    time.sleep(2 ** attempt)
        raise LLMError(f"no usable response after {max_attempts} attempts: {errors}")


def load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    if os.getenv("OPENAI_API_KEY"):
        return
    try:
        import streamlit as st

        key = st.secrets.get("OPENAI_API_KEY")
        if key:
            os.environ["OPENAI_API_KEY"] = key
    except Exception:
        pass
