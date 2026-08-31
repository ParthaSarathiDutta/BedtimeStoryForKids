"""Build corpus_index.json.

Orchestrates the pipeline: clone and count -> parse deterministic source fields
-> merge LLM annotations from the cache -> write a static, versioned artifact.

The index is shipped, not regenerated at runtime. Every file in the corpus is
accounted for: excluded entries are recorded with reasons rather than silently
dropped.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import annotate_corpus
import corpus_io
import schema

OUTPUT = Path("corpus_index.json")


def build_index(corpus_dir: Path, variant: str = "a") -> dict:
    all_stories = corpus_io.load_stories(corpus_dir)
    stories, excluded = corpus_io.partition_annotatable(all_stories)
    commit = corpus_io.corpus_commit(corpus_dir)

    records: list[dict] = []
    missing: list[str] = []

    for story in stories:
        path = annotate_corpus.cache_path(story.story_id, variant)
        if not path.exists():
            missing.append(story.story_id)
            continue
        try:
            annotation = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            missing.append(story.story_id)
            continue
        if annotation.get("annotation", {}).get("failed"):
            missing.append(story.story_id)
            continue

        records.append({
            "source": story.source_block(commit),
            "search_metadata": annotation["search_metadata"],
            "summary": annotation["summary"],
            "safety": annotation["safety"],
            "annotation": annotation["annotation"],
        })

    return {
        "schema_version": schema.SCHEMA_VERSION,
        # Identifies the exact prompt and taxonomy that produced these labels,
        # so an index can be traced back to its inputs after either changes.
        "annotation_fingerprint": annotate_corpus.annotation_fingerprint(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus": {
            "repo": corpus_io.CORPUS_REPO,
            "commit": commit,
            "language": corpus_io.LANGUAGE_SUBDIR,
            "files_parsed": len(all_stories),
            "annotatable": len(stories),
            "indexed": len(records),
            "min_story_words": corpus_io.MIN_STORY_WORDS,
            "excluded": excluded,
            "unannotated": missing,
        },
        "stories": records,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build corpus_index.json.")
    ap.add_argument("--clone", action="store_true", help="clone the corpus if absent")
    ap.add_argument("--corpus", type=Path, default=corpus_io.DEFAULT_CORPUS_DIR)
    ap.add_argument("--variant", default="a")
    ap.add_argument("--out", type=Path, default=OUTPUT)
    args = ap.parse_args()

    if args.clone:
        print(f"ensuring corpus at {args.corpus} ...")
        corpus_io.ensure_corpus(args.corpus)

    index = build_index(args.corpus, args.variant)
    c = index["corpus"]

    args.out.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\ncorpus {c['repo']} @ {c['commit']}")
    print(f"  files parsed : {c['files_parsed']}")
    print(f"  annotatable  : {c['annotatable']}  (excluded {len(c['excluded'])}, "
          f"floor {c['min_story_words']} words)")
    print(f"  indexed      : {c['indexed']}")
    if c["unannotated"]:
        print(f"  MISSING annotations for {len(c['unannotated'])}: "
              f"{', '.join(c['unannotated'][:10])}"
              f"{' ...' if len(c['unannotated']) > 10 else ''}")
        print("  run: annotate_corpus.py --mode full")
    print(f"\nwrote {args.out} ({args.out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
