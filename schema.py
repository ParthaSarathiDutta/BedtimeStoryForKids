"""Taxonomy definitions, normalization, and validation.

Single source of truth for the metadata vocabulary. Imported by the offline
annotation pipeline and, later, by runtime child-request extraction. Both paths
must share this module: the design assumes that annotating a story and
extracting from a child's request produce the same shape of object, which is
false if either side has its own copy of the vocabulary or synonym map.

See METADATA_PREPARATION_PLAN.md sections 3 and 5.2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

SCHEMA_VERSION = "v0"

Cardinality = Literal["single", "multi"]

# Annotation-only escape values. Never valid as a child preference, and they
# score as zero rather than matching every query. `other` means no category
# fits (the taxonomy is missing something); `uncertain` means the text does not
# support a confident choice (definitions are ambiguous). They diagnose
# different faults, so they are kept distinct.
OTHER = "other"
UNCERTAIN = "uncertain"
ESCAPE_VALUES = (OTHER, UNCERTAIN)

CONFIDENCE_LEVELS = ("high", "medium", "low")


@dataclass(frozen=True)
class Field:
    name: str
    values: tuple[str, ...]
    cardinality: Cardinality
    child_facing: bool
    # Weight in the retrieval scorer. Kept here so the scorer and the
    # validation "does this field earn its weight" test read the same numbers.
    weight: float
    open_vocabulary: bool = False

    @property
    def allowed(self) -> tuple[str, ...]:
        return self.values + ESCAPE_VALUES


FIELDS: tuple[Field, ...] = (
    Field("reading_band", ("5-6", "7-8", "9-10"), "single", False, 0.0),
    Field(
        "story_type",
        ("everyday", "adventure", "fantasy", "mystery", "folktale",
         "discovery/learning", "bedtime"),
        "multi", True, 2.0,
    ),
    Field(
        "protagonist_type",
        ("child", "animal", "family/adult", "personified object/nature", "group"),
        "single", True, 2.0,
    ),
    Field(
        "setting",
        ("home/school", "city/village", "nature/farm/jungle", "travel",
         "fantasy/space", "historical/cultural"),
        "multi", True, 1.5,
    ),
    Field("interest_tags", (), "multi", True, 3.0, open_vocabulary=True),
    Field(
        "tone",
        ("funny", "warm/cozy", "exciting", "wondrous", "curious", "mysterious",
         "heartfelt"),
        "multi", True, 1.5,
    ),
    Field(
        "fantasy_level",
        ("realistic", "whimsical/personified", "fully magical"),
        "single", True, 1.0,
    ),
    Field(
        "plot_shape",
        ("problem→solution", "quest/rescue", "exploration", "discovery/learning",
         "overcome challenge", "silly/cumulative events"),
        "single", False, 1.0,
    ),
    Field(
        "narrative_style",
        ("regular prose", "dialogue-heavy", "repetitive", "rhyming/poetic",
         "question-and-answer"),
        "multi", False, 0.5,
    ),
    Field(
        "energy_level",
        ("calm", "playful", "exciting", "mildly tense/spooky"),
        "single", True, 0.5,
    ),
)

FIELDS_BY_NAME: dict[str, Field] = {f.name: f for f in FIELDS}
FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in FIELDS)

# reading_band is excluded: it is a suitability constraint applied separately,
# not something to soft-score against a child's stated preferences.
SCORED_FIELDS: tuple[Field, ...] = tuple(f for f in FIELDS if f.weight > 0)


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------
# Safety is deliberately outside the ten taxonomy fields. `tone = mildly
# tense/spooky` is a legitimate preference for a 9-10 year old; safety is a
# system constraint. Collapsing them would stop a child from ever asking for a
# slightly spooky story.

SAFETY_FLAGS = ("violence", "intense_fear", "death", "threat", "disturbing_imagery")

# Retrieval effect. Retrieved stories are Planner inspiration and are never
# narrated to the child, so the bar for retrieval is lower than for output.
# Excluding everything flagged would discard legitimate structure, since most
# quest/rescue arcs require a threat.
HARD_EXCLUDE_FLAGS = frozenset({"violence", "disturbing_imagery"})
DOWNWEIGHT_FLAGS = frozenset({"intense_fear", "death", "threat"})

# Policy, deliberately in code rather than in the record. A stored
# `bedtime_safe` boolean would silently commit to one age band -- a story in
# which a grandparent dies may be fine at 9-10 and not at 5-6 -- and revising
# policy should not require re-annotating the corpus through the API.
_FLAGS_TOLERATED_BY_BAND: dict[str, frozenset[str]] = {
    "5-6": frozenset(),
    "7-8": frozenset({"threat"}),
    "9-10": frozenset({"threat", "intense_fear", "death"}),
}


def bedtime_safe(flags: list[str], reading_band: str) -> bool:
    """Whether a story is suitable to narrate at bedtime for a reading band.

    Derived rather than stored. `flags` are observations about the text;
    this function is the policy applied to them.
    """
    present = {f.strip().lower() for f in flags if f and f.strip()}
    if present & HARD_EXCLUDE_FLAGS:
        return False
    tolerated = _FLAGS_TOLERATED_BY_BAND.get(reading_band, frozenset())
    return present <= tolerated


def retrieval_penalty(flags: list[str]) -> float | None:
    """Scoring multiplier for a story's safety flags.

    Returns None if the story must be excluded from retrieval entirely.
    """
    present = {f.strip().lower() for f in flags if f and f.strip()}
    if present & HARD_EXCLUDE_FLAGS:
        return None
    return 0.5 if present & DOWNWEIGHT_FLAGS else 1.0


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------
# Runs *before* validation. Validating first would reject a recoverable "Warm"
# as a schema violation and burn an API retry on what a dictionary lookup fixes
# for free. gpt-3.5-turbo will not follow a controlled vocabulary perfectly
# every time; that is normal and cheap to absorb here.

_VALUE_SYNONYMS: dict[str, dict[str, str]] = {
    "reading_band": {
        "5-6 years": "5-6", "56": "5-6", "5 to 6": "5-6", "ages 5-6": "5-6",
        "7-8 years": "7-8", "78": "7-8", "7 to 8": "7-8", "ages 7-8": "7-8",
        "9-10 years": "9-10", "910": "9-10", "9 to 10": "9-10",
        "ages 9-10": "9-10", "9-10+": "9-10",
    },
    "story_type": {
        "daily life": "everyday", "slice of life": "everyday",
        "realistic": "everyday", "real life": "everyday",
        "adventurous": "adventure", "quest": "adventure",
        "fantastical": "fantasy", "magical": "fantasy", "fairy tale": "fantasy",
        "fairytale": "fantasy", "mystery/suspense": "mystery",
        "detective": "mystery", "folk tale": "folktale", "fable": "folktale",
        "myth": "folktale", "legend": "folktale", "traditional": "folktale",
        "educational": "discovery/learning", "learning": "discovery/learning",
        "discovery": "discovery/learning", "science": "discovery/learning",
        "informational": "discovery/learning", "nonfiction": "discovery/learning",
        "goodnight": "bedtime", "sleep": "bedtime", "bed time": "bedtime",
    },
    "protagonist_type": {
        "kid": "child", "children": "child", "boy": "child", "girl": "child",
        "animals": "animal", "creature": "animal", "bird": "animal",
        "insect": "animal", "family": "family/adult", "adult": "family/adult",
        "parent": "family/adult", "mother": "family/adult",
        "father": "family/adult", "grandparent": "family/adult",
        "personified object": "personified object/nature",
        "object": "personified object/nature",
        "nature": "personified object/nature",
        "personified nature": "personified object/nature",
        "inanimate object": "personified object/nature",
        "ensemble": "group", "multiple": "group", "community": "group",
        "village": "group",
    },
    "setting": {
        "home": "home/school", "school": "home/school",
        "house": "home/school", "classroom": "home/school",
        "city": "city/village", "town": "city/village",
        "village": "city/village", "urban": "city/village",
        "rural": "city/village", "neighbourhood": "city/village",
        "neighborhood": "city/village",
        "nature": "nature/farm/jungle", "farm": "nature/farm/jungle",
        "jungle": "nature/farm/jungle", "forest": "nature/farm/jungle",
        "outdoors": "nature/farm/jungle", "wilderness": "nature/farm/jungle",
        "sea": "nature/farm/jungle", "mountain": "nature/farm/jungle",
        "journey": "travel", "road trip": "travel", "train": "travel",
        "space": "fantasy/space", "fantasy": "fantasy/space",
        "outer space": "fantasy/space", "magical land": "fantasy/space",
        "imaginary world": "fantasy/space",
        "historical": "historical/cultural", "cultural": "historical/cultural",
        "festival": "historical/cultural", "mythological": "historical/cultural",
    },
    "tone": {
        "warm": "warm/cozy", "cozy": "warm/cozy", "cosy": "warm/cozy",
        "gentle": "warm/cozy", "tender": "warm/cozy", "comforting": "warm/cozy",
        "humorous": "funny", "humourous": "funny", "silly": "funny",
        "playful": "funny", "comic": "funny", "amusing": "funny",
        "thrilling": "exciting", "energetic": "exciting",
        "adventurous": "exciting", "wonder": "wondrous",
        "magical": "wondrous", "awe": "wondrous", "dreamy": "wondrous",
        "inquisitive": "curious", "questioning": "curious",
        "curiosity": "curious", "mysterious/suspenseful": "mysterious",
        "suspenseful": "mysterious", "eerie": "mysterious",
        "emotional": "heartfelt", "touching": "heartfelt",
        "moving": "heartfelt", "poignant": "heartfelt",
        "sincere": "heartfelt",
    },
    "fantasy_level": {
        "real": "realistic", "realistic/everyday": "realistic",
        "non-fantasy": "realistic", "grounded": "realistic",
        "whimsical": "whimsical/personified",
        "personified": "whimsical/personified",
        "mildly magical": "whimsical/personified",
        "lightly magical": "whimsical/personified",
        "some magic": "whimsical/personified",
        "magical": "fully magical", "fantasy": "fully magical",
        "fully fantastical": "fully magical", "high fantasy": "fully magical",
    },
    "plot_shape": {
        "problem-solution": "problem→solution",
        "problem solution": "problem→solution",
        "problem to solution": "problem→solution",
        "problem->solution": "problem→solution",
        "problem_solution": "problem→solution",
        "quest": "quest/rescue", "rescue": "quest/rescue",
        "quest or rescue": "quest/rescue", "quest_rescue": "quest/rescue",
        "exploration/encounters": "exploration", "encounters": "exploration",
        "journey": "exploration", "wandering": "exploration",
        "discovery": "discovery/learning", "learning": "discovery/learning",
        "question and answer": "discovery/learning",
        "question→imagination→explanation": "discovery/learning",
        "overcome a challenge": "overcome challenge",
        "overcoming challenge": "overcome challenge",
        "challenge": "overcome challenge",
        "perseverance": "overcome challenge",
        "silly events": "silly/cumulative events",
        "cumulative": "silly/cumulative events",
        "cumulative events": "silly/cumulative events",
        "silly": "silly/cumulative events",
        "escalating": "silly/cumulative events",
    },
    "narrative_style": {
        "prose": "regular prose", "standard prose": "regular prose",
        "narrative": "regular prose", "straightforward": "regular prose",
        "dialogue": "dialogue-heavy", "dialogue heavy": "dialogue-heavy",
        "conversational": "dialogue-heavy",
        "repetition": "repetitive", "repeating": "repetitive",
        "refrain": "repetitive", "cumulative": "repetitive",
        "rhyming": "rhyming/poetic", "rhyme": "rhyming/poetic",
        "poetic": "rhyming/poetic", "verse": "rhyming/poetic",
        "poem": "rhyming/poetic",
        "q&a": "question-and-answer",
        "question and answer": "question-and-answer",
        "questions": "question-and-answer",
    },
    "energy_level": {
        "quiet": "calm", "soothing": "calm", "gentle": "calm",
        "relaxed": "calm", "peaceful": "calm", "slow": "calm",
        "lively": "playful", "fun": "playful", "light": "playful",
        "cheerful": "playful",
        "high energy": "exciting", "energetic": "exciting",
        "fast-paced": "exciting", "thrilling": "exciting",
        "tense": "mildly tense/spooky", "spooky": "mildly tense/spooky",
        "suspenseful": "mildly tense/spooky",
        "mildly scary": "mildly tense/spooky",
        "slightly scary": "mildly tense/spooky",
    },
}

# interest_tags is semi-open by design: children's requests contain things we
# will not anticipate, so unrecognized tags are valid rather than errors. We
# only fold obvious synonyms so a child's word and a story's label agree.
_INTEREST_SYNONYMS: dict[str, str] = {
    "puppy": "dogs", "puppies": "dogs", "dog": "dogs",
    "kitten": "cats", "kittens": "cats", "cat": "cats", "kitty": "cats",
    "spaceship": "space", "rocket": "space", "space travel": "space",
    "astronaut": "space", "planets": "space", "planet": "space",
    "outer space": "space",
    "soccer": "football/soccer", "football": "football/soccer",
    "dinosaur": "dinosaurs", "dino": "dinosaurs",
    "animal": "animals", "birds": "animals", "bird": "animals",
    "elephant": "animals", "elephants": "animals", "tiger": "animals",
    "tigers": "animals", "monkey": "animals", "monkeys": "animals",
    "friend": "friendship", "friends": "friendship", "friendly": "friendship",
    "families": "family", "mother": "family", "father": "family",
    "grandmother": "family", "grandfather": "family", "sibling": "family",
    "brother": "family", "sister": "family",
    "schools": "school", "teacher": "school", "classroom": "school",
    "study": "school",
    "nature": "science/nature", "plants": "science/nature",
    "trees": "science/nature", "tree": "science/nature",
    "weather": "science/nature", "seasons": "science/nature",
    "science": "science/nature", "insects": "science/nature",
    "eating": "food", "cooking": "food", "fruit": "food", "fruits": "food",
    "sport": "sports", "cricket game": "sports", "games": "sports",
    "painting": "art", "drawing": "art", "music": "art", "dance": "art",
    "singing": "art",
    "journey": "travel", "trip": "travel", "train": "travel",
    "magical": "magic", "wizard": "magic", "spell": "magic",
    "night time": "night", "nighttime": "night", "moon": "night",
    "stars": "night", "sleep": "night", "bedtime": "night",
    "brave": "courage", "bravery": "courage",
}

_WS = re.compile(r"\s+")


def _clean(value: str) -> str:
    """Lowercase, collapse whitespace, unify dash/arrow variants."""
    v = value.strip().lower()
    v = v.replace("\u2192", "→").replace("->", "→").replace("=>", "→")
    v = v.replace("\u2013", "-").replace("\u2014", "-")
    v = _WS.sub(" ", v)
    return v.strip(" .;:,")


def normalize_value(field_name: str, raw: str) -> str:
    """Map one raw label onto the controlled vocabulary where possible.

    Unrecognized values are returned cleaned but unchanged, NOT coerced to
    `other`. The distinction matters: `other` means the model deliberately
    judged that no category fits, while an unrecognized string means it went
    off-vocabulary. Validation reports the latter so it can be re-annotated.
    """
    field = FIELDS_BY_NAME[field_name]
    v = _clean(raw)
    if not v:
        return v

    if field.open_vocabulary:
        return _INTEREST_SYNONYMS.get(v, v)

    if v in field.allowed:
        return v
    # Canonical values are stored lowercase, but compare defensively.
    for candidate in field.allowed:
        if v == candidate.lower():
            return candidate

    mapped = _VALUE_SYNONYMS.get(field_name, {}).get(v)
    if mapped:
        return mapped

    # "warm/cozy" emitted as "warm, cozy" or "warm or cozy".
    for sep in (" or ", ", ", "/"):
        if sep in v:
            head = v.split(sep)[0].strip()
            if head in field.allowed:
                return head
            mapped = _VALUE_SYNONYMS.get(field_name, {}).get(head)
            if mapped:
                return mapped
            break
    return v


def normalize_metadata(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Normalize a raw `search_metadata` object.

    Returns the normalized object and a log of every change applied. The log is
    collected because how often the model deviates from the vocabulary is
    itself a signal about prompt quality, and it is free to gather.
    """
    out: dict[str, Any] = {}
    log: list[str] = []

    for field in FIELDS:
        value = raw.get(field.name)
        if value is None:
            out[field.name] = [] if field.cardinality == "multi" else UNCERTAIN
            log.append(f"{field.name}: missing -> default")
            continue

        if field.cardinality == "multi":
            items = value if isinstance(value, list) else [value]
            normalized: list[str] = []
            for item in items:
                if not isinstance(item, str):
                    continue
                new = normalize_value(field.name, item)
                if new and new not in normalized:
                    normalized.append(new)
                if new != _clean(item):
                    log.append(f"{field.name}: {item!r} -> {new!r}")
            out[field.name] = normalized
        else:
            if isinstance(value, list):
                value = value[0] if value else UNCERTAIN
                log.append(f"{field.name}: list -> scalar")
            if not isinstance(value, str):
                out[field.name] = UNCERTAIN
                log.append(f"{field.name}: non-string -> uncertain")
                continue
            new = normalize_value(field.name, value)
            if new != _clean(value):
                log.append(f"{field.name}: {value!r} -> {new!r}")
            out[field.name] = new

    return out, log


def normalize_safety_flags(raw: Any) -> tuple[list[str], list[str]]:
    """Normalize a safety flag list, dropping anything unrecognized."""
    log: list[str] = []
    if raw is None:
        return [], log
    items = raw if isinstance(raw, list) else [raw]
    out: list[str] = []
    aliases = {
        "scary": "intense_fear", "fear": "intense_fear",
        "frightening": "intense_fear", "danger": "threat",
        "dying": "death", "dead": "death", "violent": "violence",
        "disturbing": "disturbing_imagery", "gore": "disturbing_imagery",
    }
    for item in items:
        if not isinstance(item, str):
            continue
        v = _clean(item).replace(" ", "_")
        v = aliases.get(v, v)
        if v in SAFETY_FLAGS:
            if v not in out:
                out.append(v)
        elif v:
            log.append(f"safety: dropped unrecognized flag {item!r}")
    return out, log


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate_metadata(md: dict[str, Any]) -> list[str]:
    """Return a list of schema violations. Empty list means valid.

    Run only after normalization, so anything reported here is a genuine
    failure rather than a recoverable formatting difference.
    """
    errors: list[str] = []

    for field in FIELDS:
        if field.name not in md:
            errors.append(f"{field.name}: absent")
            continue
        value = md[field.name]

        if field.cardinality == "multi":
            if not isinstance(value, list):
                errors.append(f"{field.name}: expected list, got {type(value).__name__}")
                continue
            if not value:
                errors.append(f"{field.name}: empty list")
                continue
            for item in value:
                if field.open_vocabulary:
                    if not isinstance(item, str) or not item:
                        errors.append(f"{field.name}: bad tag {item!r}")
                elif item not in field.allowed:
                    errors.append(f"{field.name}: {item!r} not in vocabulary")
        else:
            if not isinstance(value, str):
                errors.append(f"{field.name}: expected str, got {type(value).__name__}")
            elif value not in field.allowed:
                errors.append(f"{field.name}: {value!r} not in vocabulary")

    return errors


def validate_confidence(conf: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(conf, dict):
        return ["confidence: not an object"]
    for name in FIELD_NAMES:
        level = conf.get(name)
        if level is None:
            errors.append(f"confidence.{name}: absent")
        elif str(level).strip().lower() not in CONFIDENCE_LEVELS:
            errors.append(f"confidence.{name}: {level!r} invalid")
    return errors


def vocabulary_prompt_block() -> str:
    """Human-readable vocabulary listing for the annotation prompt.

    Generated from FIELDS so the prompt cannot drift from the schema.
    """
    lines: list[str] = []
    for field in FIELDS:
        card = "choose ALL that apply" if field.cardinality == "multi" else "choose EXACTLY ONE"
        if field.open_vocabulary:
            lines.append(
                f"- {field.name} ({card}): free-form lowercase tags, 2-5 of them. "
                f"Common examples: animals, friendship, family, school, "
                f"science/nature, food, sports, art, travel, magic, night, courage."
            )
        else:
            lines.append(f"- {field.name} ({card}): {' | '.join(field.values)}")
    return "\n".join(lines)
