"""LLM annotation of corpus stories.

Uses `gpt-3.5-turbo` -- the same model the runtime agents use, as the assignment
README requires. The constraint is arguably about the storytelling pipeline
rather than offline data prep, but honoring it costs nothing and removes any
question of a stronger model having been used offline to create an advantage.

Only `search_metadata`, `summary`, `safety.flags`, and confidence come from the
model. The `source` block is parsed deterministically in `corpus_io` and is
never requested here, because a hallucinated author name would be a CC BY
attribution failure rather than a cosmetic bug.

Pipeline order per story: call -> parse JSON -> normalize -> validate -> save.
Normalization runs before validation so a recoverable "Warm" does not burn an
API retry on what a dictionary lookup fixes for free.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import schema
import corpus_io
from corpus_io import Story

MODEL = "gpt-3.5-turbo"
CACHE_DIR = Path("artifacts/annotation_cache")
MAX_ATTEMPTS = 3

# gpt-3.5-turbo's 16,385-token context against a 3,801-word longest story
# (~5,100 tokens) means this guard should never fire. It is retained only
# because the model name is a moving alias: if it ever resolves to an older
# 4K-context variant, the four stories above 3,000 words would break.
# See METADATA_PREPARATION_PLAN.md section 5.7.
MAX_STORY_WORDS = 9000
HEAD_FRACTION = 0.60
TAIL_FRACTION = 0.20

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------

PROMPT_TEMPLATE = """You are annotating a children's story for a searchable metadata index.

Classify the story using ONLY the vocabularies below. Copy values exactly as written.

{vocabulary}

Escape values: every field above also accepts "other" and "uncertain".
Use "other" only if no listed category reasonably fits the story.
Use "uncertain" only if the text does not let you choose confidently.
Do not use them as a convenient default.

Also provide:
- summary: 2-3 sentences describing what happens. Plain description, no praise.
- safety.flags: any of {safety_flags} that genuinely apply, else an empty list.
  Judge the story's content, not its suitability. A tense moment in a folktale
  is a "threat" flag even if the story is gentle overall.
- confidence: "high", "medium", or "low" for EVERY one of the ten fields,
  reflecting how well the text supports your choice.

Reply with ONLY a JSON object in exactly this shape, no prose before or after:

{{
  "search_metadata": {{
{skeleton}
  }},
  "summary": "...",
  "safety": {{"flags": []}},
  "confidence": {{{confidence_keys}}}
}}

STORY TITLE: {title}

STORY TEXT:
{text}
"""


def build_prompt(story: Story, text: str) -> str:
    skeleton_lines = []
    for f in schema.FIELDS:
        placeholder = '["..."]' if f.cardinality == "multi" else '"..."'
        skeleton_lines.append(f'    "{f.name}": {placeholder}')
    confidence_keys = ", ".join(f'"{n}": "..."' for n in schema.FIELD_NAMES)
    return PROMPT_TEMPLATE.format(
        vocabulary=schema.vocabulary_prompt_block(),
        safety_flags=list(schema.SAFETY_FLAGS),
        skeleton=",\n".join(skeleton_lines),
        confidence_keys=confidence_keys,
        title=story.title,
        text=text,
    )


def prepare_text(story: Story) -> tuple[str, bool]:
    """Return the story text to annotate and whether it was truncated."""
    words = story.text.split()
    if len(words) <= MAX_STORY_WORDS:
        return story.text, False
    # Head plus tail, never head alone: the ending carries the evidence for
    # plot_shape, energy_level, and whether the story closes calm, which is the
    # single most important property for a bedtime story.
    head = int(MAX_STORY_WORDS * HEAD_FRACTION)
    tail = int(MAX_STORY_WORDS * TAIL_FRACTION)
    sampled = " ".join(words[:head]) + "\n\n[... middle omitted ...]\n\n" + " ".join(words[-tail:])
    return sampled, True


# --------------------------------------------------------------------------
# JSON extraction
# --------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(raw: str) -> dict | None:
    """Pull a JSON object out of a model response.

    gpt-3.5-turbo wraps JSON in prose or fences often enough that this must be
    defensive rather than a bare json.loads.
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


# --------------------------------------------------------------------------
# Model call
# --------------------------------------------------------------------------


class Annotator:
    def __init__(self, mock: bool = False, temperature: float = 0.0):
        self.mock = mock
        self.temperature = temperature
        self._client = None
        if not mock:
            from openai import OpenAI  # imported lazily so mock mode needs no key
            key = os.getenv("OPENAI_API_KEY")
            if not key:
                raise SystemExit(
                    "OPENAI_API_KEY is not set. Put it in .env or the environment, "
                    "or run with --mock to exercise the pipeline without API calls."
                )
            self._client = OpenAI(api_key=key)

    def complete(self, prompt: str, salt: str = "") -> str:
        if self.mock:
            return _mock_response(prompt, salt)
        resp = self._client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
            max_tokens=700,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or ""


def _mock_response(prompt: str, salt: str = "") -> str:
    """Deterministic pseudo-annotation for pipeline testing without a key.

    Seeded from the prompt so the same story yields the same labels within a
    run, and salted by variant so a second pass disagrees on some fields --
    otherwise the self-consistency check would trivially report 100% and never
    exercise its disagreement path.

    Deliberately emits off-vocabulary casing and synonyms ("Warm", "puppy") so
    the normalization stage is actually tested rather than bypassed.
    """
    rng = random.Random(hashlib.sha256((prompt + salt).encode()).hexdigest())
    md: dict[str, object] = {}
    for f in schema.FIELDS:
        if f.open_vocabulary:
            md[f.name] = rng.sample(
                ["animals", "friendship", "puppy", "space", "family", "night", "food"], 3
            )
        elif f.cardinality == "multi":
            picks = rng.sample(list(f.values), rng.randint(1, 2))
            md[f.name] = [p.title() if rng.random() < 0.2 else p for p in picks]
        else:
            v = rng.choice(list(f.values))
            md[f.name] = v.title() if rng.random() < 0.2 else v
    if rng.random() < 0.1:
        md["plot_shape"] = "other"
    flags = rng.sample(list(schema.SAFETY_FLAGS), 1) if rng.random() < 0.15 else []
    conf = {n: rng.choices(["high", "medium", "low"], weights=[5, 3, 2])[0]
            for n in schema.FIELD_NAMES}
    return json.dumps({
        "search_metadata": md,
        "summary": "A mock summary generated without calling the API.",
        "safety": {"flags": flags},
        "confidence": conf,
    })


# --------------------------------------------------------------------------
# Per-story annotation
# --------------------------------------------------------------------------


def cache_path(story_id: str, variant: str = "a") -> Path:
    return CACHE_DIR / f"{story_id}_{schema.SCHEMA_VERSION}_{variant}.json"


def annotate_story(
    story: Story,
    annotator: Annotator,
    variant: str = "a",
    use_cache: bool = True,
) -> dict:
    """Annotate one story, returning a record fragment.

    Cached per story and schema version so a re-run skips completed work.
    Several hundred sequential API calls will hit rate limits and transient
    failures; without a cache one failure late in the batch means paying for
    the whole batch again.
    """
    path = cache_path(story.story_id, variant)
    if use_cache and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass  # corrupt cache entry, re-annotate

    text, truncated = prepare_text(story)
    prompt = build_prompt(story, text)

    errors: list[str] = []
    norm_log: list[str] = []
    record: dict | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = annotator.complete(prompt, salt=variant)
        except Exception as exc:  # transient API failure
            errors.append(f"attempt {attempt}: api error: {exc}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(2 ** attempt)
            continue

        parsed = extract_json(raw)
        if parsed is None:
            errors.append(f"attempt {attempt}: unparseable JSON")
            continue

        # Normalize BEFORE validating.
        md, norm_log = schema.normalize_metadata(parsed.get("search_metadata", {}))
        flags, flag_log = schema.normalize_safety_flags(
            (parsed.get("safety") or {}).get("flags")
        )
        norm_log += flag_log

        problems = schema.validate_metadata(md)
        conf_raw = parsed.get("confidence", {})
        conf = {k: str(v).strip().lower() for k, v in conf_raw.items()} if isinstance(conf_raw, dict) else {}
        problems += schema.validate_confidence(conf)

        if problems:
            errors.append(f"attempt {attempt}: " + "; ".join(problems[:4]))
            if attempt < MAX_ATTEMPTS:
                continue

        record = {
            "search_metadata": md,
            "summary": str(parsed.get("summary", "")).strip(),
            "safety": {"flags": flags},
            "annotation": {
                "schema_version": schema.SCHEMA_VERSION,
                "model": "mock" if annotator.mock else MODEL,
                "annotated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "text_truncated": truncated,
                "attempts": attempt,
                "confidence": conf,
                "normalizations": norm_log,
                "validation_errors": problems,
            },
        }
        break

    if record is None:
        record = {
            "search_metadata": {},
            "summary": "",
            "safety": {"flags": []},
            "annotation": {
                "schema_version": schema.SCHEMA_VERSION,
                "model": "mock" if annotator.mock else MODEL,
                "annotated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "text_truncated": truncated,
                "attempts": MAX_ATTEMPTS,
                "confidence": {},
                "normalizations": norm_log,
                "validation_errors": errors,
                "failed": True,
            },
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return record


def annotate_batch(
    stories: list[Story],
    annotator: Annotator,
    variant: str = "a",
    workers: int = 5,
    use_cache: bool = True,
) -> dict[str, dict]:
    results: dict[str, dict] = {}
    total = len(stories)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(annotate_story, s, annotator, variant, use_cache): s
            for s in stories
        }
        for fut in as_completed(futures):
            story = futures[fut]
            try:
                results[story.story_id] = fut.result()
            except Exception as exc:
                log(f"  !! {story.story_id} {story.title[:36]}: {exc}")
                continue
            done += 1
            rec = results[story.story_id]
            mark = "FAIL" if rec["annotation"].get("failed") else "ok"
            log(f"  [{done:>3}/{total}] {story.story_id} {story.title[:40]:<40} {mark}")
    return results


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Annotate corpus stories with taxonomy metadata.")
    ap.add_argument("--mode", choices=["pilot", "full"], default="pilot")
    ap.add_argument("--mock", action="store_true",
                    help="generate deterministic fake annotations, no API calls")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--variant", default="a",
                    help="cache variant; use 'b' for the self-consistency re-run")
    # The self-consistency pass should NOT reuse temperature 0. At 0 the model
    # is near-deterministic for an identical prompt, so agreement would come
    # back at ~100% and the check would be vacuous. Running the second pass a
    # little warmer asks the more useful question: is this label stable, or was
    # it a coin flip the greedy decode happened to hide?
    ap.add_argument("--temperature", type=float, default=0.0,
                    help="0.0 for the main pass; try 0.3 for --variant b")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--corpus", type=Path, default=corpus_io.DEFAULT_CORPUS_DIR)
    ap.add_argument("--pilot-file", type=Path, default=Path("artifacts/pilot_selection.json"))
    args = ap.parse_args()

    load_env()

    all_stories = corpus_io.load_stories(args.corpus)
    stories, excluded = corpus_io.partition_annotatable(all_stories)
    by_id = {s.story_id: s for s in stories}

    if args.mode == "pilot":
        if not args.pilot_file.exists():
            raise SystemExit(f"{args.pilot_file} missing. Run select_pilot.py first.")
        sel = json.loads(args.pilot_file.read_text(encoding="utf-8"))
        targets = [by_id[r["story_id"]] for r in sel["selected"] if r["story_id"] in by_id]
    else:
        targets = stories

    if args.limit:
        targets = targets[:args.limit]

    log(f"mode={args.mode} model={'mock' if args.mock else MODEL} "
        f"stories={len(targets)} workers={args.workers} variant={args.variant} "
        f"temp={args.temperature}")
    log(f"corpus: {len(all_stories)} parsed, {len(stories)} annotatable, {len(excluded)} excluded\n")

    started = time.time()
    annotator = Annotator(mock=args.mock, temperature=args.temperature)
    results = annotate_batch(targets, annotator, args.variant,
                             args.workers, not args.no_cache)
    elapsed = time.time() - started

    failed = sum(1 for r in results.values() if r["annotation"].get("failed"))
    retried = sum(1 for r in results.values() if r["annotation"].get("attempts", 1) > 1)
    normalized = sum(len(r["annotation"].get("normalizations", [])) for r in results.values())

    log(f"\nannotated {len(results)}/{len(targets)} in {elapsed:.1f}s")
    log(f"  failed after {MAX_ATTEMPTS} attempts: {failed}")
    log(f"  needed a retry:                      {retried}")
    log(f"  normalizations applied:              {normalized}")
    log(f"  cache: {CACHE_DIR}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
