"""Loading and parsing pb-source story files.

Lives in one place so `select_pilot`, `annotate_corpus`, and `index_corpus`
share a single parser rather than three drifting copies.

Everything in the `source` block is parsed deterministically here and is never
requested from the LLM. `author` and `license` are CC BY 4.0 compliance data,
so a hallucinated author name would be an attribution failure rather than a
cosmetic bug. See METADATA_PREPARATION_PLAN.md section 4.1.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field as dc_field
from pathlib import Path

CORPUS_REPO = "https://github.com/global-asp/pb-source"
DEFAULT_CORPUS_DIR = Path("corpus/pb-source")
LANGUAGE_SUBDIR = "en"

# Below this, a story cannot support any of the ten taxonomy fields: the record
# would come back entirely `uncertain`, which pollutes the index and skews the
# validation distributions. Measured against the real corpus, this excludes 7 of
# 395 files -- 4 with no body text at all (title plus footer only) and 3 stubs
# of 4 to 17 words. Excluding them with a recorded reason beats annotating
# garbage. See METADATA_PREPARATION_PLAN.md section 2.1.
MIN_STORY_WORDS = 20

_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)
_FOOTER_RE = re.compile(r"^\*\s*([A-Za-z ]+?):\s*(.+?)\s*$", re.M)
_PAGE_SPLIT_RE = re.compile(r"^##\s*$", re.M)
_ID_RE = re.compile(r"^(\d+)_")


@dataclass
class Story:
    story_id: str
    title: str
    source_file: str
    license: str
    author: str
    illustrator: str
    language: str
    text: str
    pages: list[str] = dc_field(default_factory=list)

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    def source_block(self, corpus_commit: str) -> dict[str, object]:
        return {
            "id": self.story_id,
            "title": self.title,
            "license": self.license,
            "author": self.author,
            "illustrator": self.illustrator,
            "source_file": self.source_file,
            "word_count": self.word_count,
            "corpus_commit": corpus_commit,
        }


def ensure_corpus(corpus_dir: Path = DEFAULT_CORPUS_DIR) -> Path:
    """Clone the corpus if absent. Returns the repo root."""
    if (corpus_dir / LANGUAGE_SUBDIR).is_dir():
        return corpus_dir
    corpus_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", CORPUS_REPO, str(corpus_dir)],
        check=True,
        capture_output=True,
    )
    return corpus_dir


def corpus_commit(corpus_dir: Path = DEFAULT_CORPUS_DIR) -> str:
    """Short commit hash of the corpus clone, for reproducibility."""
    try:
        out = subprocess.run(
            ["git", "-C", str(corpus_dir), "rev-parse", "--short", "HEAD"],
            check=True, capture_output=True, text=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def parse_story(path: Path) -> Story | None:
    """Parse one story file, or None if it is not a story.

    `/en/README.md` is the only non-story file in the directory: it has no H1
    title and no footer block, so it would otherwise become a malformed record.
    """
    if path.name.lower() == "readme.md":
        return None

    raw = path.read_text(encoding="utf-8", errors="replace")

    title_match = _TITLE_RE.search(raw)
    if not title_match:
        return None
    title = title_match.group(1).strip()

    footer = {k.strip().lower(): v.strip() for k, v in _FOOTER_RE.findall(raw)}
    if "license" not in footer:
        return None

    # Body is everything between the title and the footer block. The footer
    # lives in a trailing `##` section, so drop any section containing it.
    body = raw[title_match.end():]
    sections = [s.strip() for s in _PAGE_SPLIT_RE.split(body)]
    pages = [
        s for s in sections
        if s and not re.match(r"^\*\s*(License|Text|Illustration|Language):", s, re.M)
    ]

    id_match = _ID_RE.match(path.name)
    story_id = id_match.group(1) if id_match else path.stem

    return Story(
        story_id=story_id,
        title=title,
        source_file=path.name,
        license=footer.get("license", "").strip("[]"),
        author=footer.get("text", ""),
        illustrator=footer.get("illustration", ""),
        language=footer.get("language", "en"),
        text="\n\n".join(pages),
        pages=pages,
    )


def load_stories(corpus_dir: Path = DEFAULT_CORPUS_DIR) -> list[Story]:
    """Parse every story in the English corpus, sorted by ID.

    Includes unannotatable entries; call `partition_annotatable` to filter.
    """
    en_dir = corpus_dir / LANGUAGE_SUBDIR
    if not en_dir.is_dir():
        raise FileNotFoundError(
            f"{en_dir} not found. Run `python index_corpus.py --clone` first."
        )
    stories = [s for p in sorted(en_dir.glob("*.md")) if (s := parse_story(p))]
    return stories


def partition_annotatable(
    stories: list[Story], min_words: int = MIN_STORY_WORDS
) -> tuple[list[Story], list[dict[str, object]]]:
    """Split stories into annotatable ones and excluded ones with reasons.

    Exclusions are returned rather than silently dropped so the shipped index
    can account for every file in the corpus.
    """
    keep: list[Story] = []
    excluded: list[dict[str, object]] = []
    for s in stories:
        wc = s.word_count
        if wc == 0:
            reason = "no body text (title and footer only)"
        elif wc < min_words:
            reason = f"stub: {wc} words, below the {min_words}-word minimum"
        else:
            keep.append(s)
            continue
        excluded.append({
            "id": s.story_id,
            "source_file": s.source_file,
            "title": s.title,
            "word_count": wc,
            "reason": reason,
        })
    return keep, excluded
