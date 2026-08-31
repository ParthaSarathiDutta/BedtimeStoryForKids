"""Dump the pilot annotations as readable text for manual review.

Reading raw cache JSON one file at a time makes it hard to see whether a label
is defensible, because the judgement needs the story next to it. This puts each
story's text, labels, and both annotation passes on one screen.

Run: python review_pilot.py [--out artifacts/pilot_review.md]
"""

from __future__ import annotations

import argparse
import json
import pathlib

import annotate_corpus
import corpus_io
import schema

CACHE = pathlib.Path("artifacts/annotation_cache")


def load(variant: str) -> dict[str, dict]:
    suffix = annotate_corpus.cache_suffix(variant)
    return {p.name[: -len(suffix)]: json.loads(p.read_text())
            for p in CACHE.glob(f"*{suffix}")}


def render() -> str:
    a, b = load("a"), load("b")
    stories = {s.story_id: s for s in corpus_io.load_stories()}
    out: list[str] = []

    out.append(f"# Pilot annotation review "
               f"({schema.SCHEMA_VERSION}/{annotate_corpus.annotation_fingerprint()}, "
               f"{len(a)} stories)\n")
    out.append("Pass A is the annotation of record (temperature 0.0). Pass B is "
               "the self-consistency probe (temperature 0.3); it exists only to "
               "measure label stability and is not part of the index.\n")
    out.append("`DIFFERS` marks a field where the two passes disagree, which is "
               "the honest signal of how load-bearing that label is.\n")

    for sid in sorted(a):
        rec, alt = a[sid], b.get(sid, {})
        story = stories.get(sid)
        md = rec["search_metadata"]
        alt_md = alt.get("search_metadata", {})

        out.append(f"\n---\n\n## {sid} — {story.title if story else '?'}\n")
        if story:
            out.append(f"*{story.word_count} words · {story.license} · "
                       f"{story.source_file}*\n")

        out.append("\n| field | pass A | pass B |")
        out.append("|---|---|---|")
        for f in schema.FIELDS:
            va, vb = md.get(f.name), alt_md.get(f.name)
            fmt = lambda v: ", ".join(v) if isinstance(v, list) else str(v)
            flag = "" if va == vb else "  **DIFFERS**"
            out.append(f"| `{f.name}` | {fmt(va)}{flag} | {fmt(vb)} |")

        flags = rec.get("safety", {}).get("flags", [])
        band = md.get("reading_band", "")
        advisory = schema.bedtime_safe(flags, band) if band else None
        out.append(f"| `safety.flags` | {flags or 'none'} | "
                   f"{alt.get('safety', {}).get('flags') or 'none'} |")
        out.append(f"\nAdvisory `bedtime_safe` at band {band}: **{advisory}** "
                   f"(not enforced anywhere; retrieval penalty "
                   f"{schema.retrieval_penalty(flags)}×)\n")

        out.append(f"\n**Annotator summary:** {rec.get('summary', '')}\n")

        ann = rec.get("annotation", {})
        if ann.get("validation_errors"):
            out.append(f"\n**Rejected values (all retries):** "
                       f"{ann['validation_errors']}\n")
        norms = [n for n in (ann.get("normalizations") or [])]
        if norms:
            out.append(f"\n**Repairs applied:** {norms}\n")

        if story:
            text = " ".join(story.text.split())
            excerpt = text[:700] + ("…" if len(text) > 700 else "")
            out.append(f"\n**Story text:**\n\n> {excerpt}\n")

    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/pilot_review.md")
    args = ap.parse_args()

    text = render()
    path = pathlib.Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"wrote {path}  ({len(text.splitlines())} lines)")


if __name__ == "__main__":
    main()
