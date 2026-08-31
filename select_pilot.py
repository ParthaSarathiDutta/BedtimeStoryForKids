"""Pilot story selection using observable proxies.

We cannot stratify the pilot on taxonomy labels, because the labels do not
exist until after annotation -- that circularity is why selection uses only
properties measurable from the raw text.

Selection is computed rather than eyeballed so the pilot is reproducible, and
every pick records why it was chosen, which is what makes the later coverage-gap
analysis possible. See METADATA_PREPARATION_PLAN.md section 5.1.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path

import corpus_io
from corpus_io import Story

PILOT_PATH = Path("artifacts/pilot_selection.json")
RANDOM_SEED = 20260830

# Curly quotes dominate this corpus; straight quotes appear too.
_QUOTE_CHARS = "\u201c\u201d\u2018\u2019\"'"
_ANIMAL_WORDS = {
    "cat", "cats", "dog", "dogs", "bird", "birds", "elephant", "elephants",
    "tiger", "tigers", "monkey", "monkeys", "lion", "lions", "cow", "cows",
    "goat", "goats", "crow", "crows", "fish", "frog", "frogs", "mouse",
    "rat", "rats", "bear", "bears", "fox", "owl", "snake", "ant", "ants",
    "butterfly", "squirrel", "rabbit", "hen", "rooster", "donkey", "camel",
    "buffalo", "horse", "peacock", "parrot", "sparrow", "bee", "bees",
    "puppy", "kitten", "calf", "deer", "jackal", "elephant's", "paw", "tail",
}
_HUMAN_WORDS = {
    "girl", "boy", "mother", "father", "ma", "amma", "appa", "baba",
    "grandmother", "grandfather", "aunt", "uncle", "sister", "brother",
    "teacher", "friend", "friends", "children", "child", "man", "woman",
    "he", "she", "they", "her", "his", "school", "class", "village",
}
_TITLE_HINTS: dict[str, tuple[str, ...]] = {
    "title_educational": (
        "why", "how", "what", "where", "when", "science", "earth", "sun",
        "moon", "water", "grow", "learn", "count", "shapes", "body",
    ),
    "title_fantasy": (
        "magic", "magical", "dragon", "fairy", "giant", "witch", "wizard",
        "princess", "king", "queen", "monster", "ghost", "wish", "dream",
        "cloud", "star", "sky",
    ),
    "title_everyday": (
        "school", "home", "mother", "father", "amma", "day", "morning",
        "night", "birthday", "food", "market", "family", "sister",
        "brother", "friend",
    ),
}


@dataclass
class Features:
    story_id: str
    source_file: str
    title: str
    word_count: int
    dialogue_density: float
    repetition_score: float
    rhyme_score: float
    animal_ratio: float
    human_ratio: float
    title_tags: list[str]


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower())


def _dialogue_density(story: Story) -> float:
    """Quotation marks per 100 words. High means dialogue-heavy."""
    quotes = sum(story.text.count(c) for c in _QUOTE_CHARS)
    return round(quotes / max(story.word_count, 1) * 100, 2)


def _repetition_score(story: Story) -> float:
    """Share of 4-grams that appear more than once.

    Catches refrain-driven stories, e.g. Tinku's repeated
    "Will you be my friend?".
    """
    toks = _tokens(story.text)
    if len(toks) < 12:
        return 0.0
    grams = [" ".join(toks[i:i + 4]) for i in range(len(toks) - 3)]
    counts = Counter(grams)
    repeated = sum(c for g, c in counts.items() if c > 1)
    return round(repeated / len(grams), 3)


def _rhyme_score(story: Story) -> float:
    """Share of adjacent line pairs sharing a terminal sound.

    Crude 2-character suffix match on final words, which is enough to surface
    verse-driven stories for the pilot without a phonetic dictionary.
    """
    lines = [ln.strip() for ln in story.text.splitlines() if ln.strip()]
    finals: list[str] = []
    for ln in lines:
        words = _tokens(ln)
        if words:
            finals.append(words[-1])
    if len(finals) < 4:
        return 0.0
    hits = sum(
        1 for a, b in zip(finals, finals[1:])
        if a != b and len(a) >= 2 and len(b) >= 2 and a[-2:] == b[-2:]
    )
    return round(hits / (len(finals) - 1), 3)


def _ratios(story: Story) -> tuple[float, float]:
    toks = _tokens(story.text)
    if not toks:
        return 0.0, 0.0
    animal = sum(1 for t in toks if t in _ANIMAL_WORDS)
    human = sum(1 for t in toks if t in _HUMAN_WORDS)
    n = len(toks)
    return round(animal / n * 100, 2), round(human / n * 100, 2)


def _title_tags(title: str) -> list[str]:
    low = title.lower()
    return [tag for tag, words in _TITLE_HINTS.items()
            if any(re.search(rf"\b{re.escape(w)}", low) for w in words)]


def compute_features(stories: list[Story]) -> list[Features]:
    feats = []
    for s in stories:
        animal, human = _ratios(s)
        feats.append(Features(
            story_id=s.story_id,
            source_file=s.source_file,
            title=s.title,
            word_count=s.word_count,
            dialogue_density=_dialogue_density(s),
            repetition_score=_repetition_score(s),
            rhyme_score=_rhyme_score(s),
            animal_ratio=animal,
            human_ratio=human,
            title_tags=_title_tags(s.title),
        ))
    return feats


# Ratio and density proxies are unstable on very short texts: a 10-word entry
# containing one animal word outranks a genuine animal story. Such entries also
# cannot exercise plot_shape or narrative_style, so they are excluded from
# proxy-driven slots and covered by one deliberate edge-case slot instead.
MIN_PROXY_WORDS = 60


def _series_key(title: str) -> str:
    """Leading title bigram, used to avoid stacking one series.

    The corpus contains near-duplicates such as "Sister, Sister, Where Does the
    Sun Go at Night?" and "Sister, Sister Why is the Sky So Blue?", which are
    interchangeable for taxonomy purposes and would waste pilot slots. A bigram
    catches these; a trigram does not, because the titles diverge at word three.
    """
    words = re.findall(r"[a-z]+", title.lower())
    return " ".join(words[:2])


def select(feats: list[Features], target: int = 22) -> list[dict]:
    """Fill diversity slots, recording the reason for each pick.

    The random stratum is not filler: keyword and extremum selection
    systematically miss stories with uninformative titles and middling
    statistics, which is most of the corpus.
    """
    by_id = {f.story_id: f for f in feats}
    picked: dict[str, str] = {}
    used_series: set[str] = set()

    def take(candidates: list[Features], reason: str, n: int = 1,
             dedupe_series: bool = True) -> None:
        count = 0
        for f in candidates:
            if count >= n:
                break
            if f.story_id in picked:
                continue
            key = _series_key(f.title)
            if dedupe_series and key in used_series:
                continue
            picked[f.story_id] = reason
            used_series.add(key)
            count += 1

    usable = [f for f in feats if f.word_count >= MIN_PROXY_WORDS]

    take(sorted(feats, key=lambda f: -f.word_count), "length: longest in corpus", 2)
    take(sorted(usable, key=lambda f: f.word_count), "length: shortest usable", 2)
    # Shortest story that still clears the annotatable minimum, to confirm
    # annotation degrades sanely near the floor.
    take(sorted(feats, key=lambda f: f.word_count), "edge case: shortest annotatable", 1)

    take(sorted(usable, key=lambda f: -f.dialogue_density), "proxy: dialogue-heavy", 2)
    take(sorted(usable, key=lambda f: -f.repetition_score), "proxy: repetitive structure", 2)
    take(sorted(usable, key=lambda f: -f.rhyme_score), "proxy: rhyming/verse", 2)
    take(sorted(usable, key=lambda f: -f.animal_ratio), "proxy: animal protagonist", 2)
    take(sorted(usable, key=lambda f: -f.human_ratio), "proxy: human protagonist", 2)

    for tag in ("title_educational", "title_fantasy", "title_everyday"):
        take([f for f in usable if tag in f.title_tags], f"proxy: {tag}", 2)

    # Median-length, low-signal stories: the corpus's actual centre of mass.
    median_ish = sorted(usable, key=lambda f: abs(f.word_count - 274))
    take([f for f in median_ish if not f.title_tags], "proxy: typical median-length", 2)

    rng = random.Random(RANDOM_SEED)
    pool = [f for f in usable if f.story_id not in picked]
    rng.shuffle(pool)
    take(pool, "random sample (bias guard)", max(0, target - len(picked)))

    out = []
    for story_id, reason in picked.items():
        f = by_id[story_id]
        out.append({
            "story_id": story_id,
            "source_file": f.source_file,
            "title": f.title,
            "reason": reason,
            "features": {
                "word_count": f.word_count,
                "dialogue_density": f.dialogue_density,
                "repetition_score": f.repetition_score,
                "rhyme_score": f.rhyme_score,
                "animal_ratio": f.animal_ratio,
                "human_ratio": f.human_ratio,
                "title_tags": f.title_tags,
            },
        })
    return sorted(out, key=lambda r: r["story_id"])


def main() -> None:
    ap = argparse.ArgumentParser(description="Select pilot stories by observable proxies.")
    ap.add_argument("--target", type=int, default=22, help="pilot size (default 22)")
    ap.add_argument("--out", type=Path, default=PILOT_PATH)
    ap.add_argument("--corpus", type=Path, default=corpus_io.DEFAULT_CORPUS_DIR)
    args = ap.parse_args()

    all_stories = corpus_io.load_stories(args.corpus)
    stories, excluded = corpus_io.partition_annotatable(all_stories)
    feats = compute_features(stories)
    selection = select(feats, args.target)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "corpus_commit": corpus_io.corpus_commit(args.corpus),
        "corpus_parsed": len(all_stories),
        "corpus_annotatable": len(stories),
        "excluded": excluded,
        "seed": RANDOM_SEED,
        "selected": selection,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"corpus: {len(all_stories)} parsed, {len(stories)} annotatable "
          f"({len(excluded)} excluded)")
    print(f"pilot:  {len(selection)} stories -> {args.out}\n")
    width = max(len(r["title"]) for r in selection)
    for r in selection:
        print(f"  {r['story_id']}  {r['title']:<{width}}  {r['features']['word_count']:>5}w  {r['reason']}")


if __name__ == "__main__":
    main()
