"""JSON extraction from LLM responses.

Shared by the offline annotation pipeline and the runtime agents so both
tolerate the same class of `gpt-3.5-turbo` output (prose around JSON, code
fences) via one implementation, rather than two copies that could drift.
"""

from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(raw: str) -> dict | None:
    """Pull a JSON object out of a model response.

    Defensive rather than a bare `json.loads`, because `gpt-3.5-turbo` wraps
    JSON in prose or code fences often enough that this must handle it.
    """
    if not raw:
        return None
    candidates: list[str] = []
    fence = _FENCE_RE.search(raw)
    if fence:
        candidates.append(fence.group(1))
    candidates.append(raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start:end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate.strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None
