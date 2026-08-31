"""Unit checks for the parts most likely to be silently wrong.

Run: python test_schema.py
"""

from __future__ import annotations

import schema
import story_search

FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def test_normalization() -> None:
    check("tone casing", schema.normalize_value("tone", "Warm"), "warm/cozy")
    check("fantasy synonym", schema.normalize_value("fantasy_level", "whimsical"),
          "whimsical/personified")
    check("arrow ascii", schema.normalize_value("plot_shape", "problem->solution"),
          "problem→solution")
    check("arrow words", schema.normalize_value("plot_shape", "problem to solution"),
          "problem→solution")
    check("setting synonym", schema.normalize_value("setting", "forest"),
          "nature/farm/jungle")
    check("escape passthrough", schema.normalize_value("plot_shape", "other"), "other")
    check("unknown tag kept", schema.normalize_value("interest_tags", "unicorns"),
          "unicorns")


def test_v1_setting() -> None:
    """`unspecified/abstract` added for stories with no physical place."""
    f = schema.FIELDS_BY_NAME["setting"]
    check("value exists", "unspecified/abstract" in f.values, True)
    for raw in ("abstract", "none", "no setting", "not specified", "N/A", "Unspecified"):
        check(f"setting {raw!r}", schema.normalize_value("setting", raw),
              "unspecified/abstract")


def test_v1_question_plot_shape() -> None:
    """The shape the annotator kept asking for, now in the right field."""
    check("value exists",
          "question→explanation" in schema.FIELDS_BY_NAME["plot_shape"].values, True)
    for raw in ("question-and-answer", "Question and Answer", "q&a",
                "question->explanation", "question → imagination → explanation"):
        check(f"plot_shape {raw!r}", schema.normalize_value("plot_shape", raw),
              "question→explanation")

    # The same string must remain a narrative_style value, unrewritten.
    check("narrative_style unaffected",
          schema.normalize_value("narrative_style", "question-and-answer"),
          "question-and-answer")
    check("narrative_style value intact",
          "question-and-answer" in schema.FIELDS_BY_NAME["narrative_style"].values, True)

    # A record using both, in their correct fields, must validate.
    md = {f.name: (["other"] if f.cardinality == "multi" else "other")
          for f in schema.FIELDS}
    md["plot_shape"] = "question→explanation"
    md["narrative_style"] = ["question-and-answer"]
    check("both fields valid together", schema.validate_metadata(md), [])

    # No longer coerced away, since it is now a real value.
    fixed, _ = schema.coerce_invalid(dict(md))
    check("survives coercion", fixed["plot_shape"], "question→explanation")


def test_v1_interest_synonyms() -> None:
    check("education", schema.normalize_value("interest_tags", "education"), "learning")
    check("educational", schema.normalize_value("interest_tags", "Educational"), "learning")
    check("funny", schema.normalize_value("interest_tags", "funny"), "humor")
    check("humour", schema.normalize_value("interest_tags", "humour"), "humor")
    check("uniqueness", schema.normalize_value("interest_tags", "uniqueness"),
          "individuality")
    check("self-acceptance", schema.normalize_value("interest_tags", "self-acceptance"),
          "individuality")
    check("creativity", schema.normalize_value("interest_tags", "creativity"),
          "imagination")
    # Deliberately NOT folded: near-neighbours, not synonyms.
    check("curiosity distinct", schema.normalize_value("interest_tags", "curiosity"),
          "curiosity")
    check("senses distinct", schema.normalize_value("interest_tags", "senses"), "senses")


def test_v1_safety_never_excludes() -> None:
    """No flag may remove a story from retrieval."""
    for flag in schema.SAFETY_FLAGS:
        p = schema.retrieval_penalty([flag])
        check(f"penalty {flag} is positive", p > 0, True)
        check(f"penalty {flag} is not None", p is not None, True)
    check("clean penalty", schema.retrieval_penalty([]), 1.0)
    check("severe down-weights", schema.retrieval_penalty(["disturbing_imagery"]) < 1.0, True)
    check("mild down-weights", schema.retrieval_penalty(["threat"]) < 1.0, True)
    check("severe worse than mild",
          schema.retrieval_penalty(["violence"]) < schema.retrieval_penalty(["threat"]),
          True)

    # The Spot and Spike regression: a false-positive severe flag must not
    # remove the story from results.
    index = [
        {"source": {"id": "FP", "title": "false positive"},
         "search_metadata": {"story_type": ["everyday"]},
         "safety": {"flags": ["disturbing_imagery"]}},
        {"source": {"id": "OK", "title": "clean"},
         "search_metadata": {"story_type": ["everyday"]},
         "safety": {"flags": []}},
    ]
    ids = [h.story_id for h in story_search.search_stories({"story_type": ["everyday"]}, index, 5)]
    check("flagged story still retrievable", sorted(ids), ["FP", "OK"])
    check("clean story ranks first", ids[0], "OK")


def test_bedtime_safe_advisory() -> None:
    """Still band-dependent, still policy-in-code."""
    check("clean 5-6", schema.bedtime_safe([], "5-6"), True)
    check("threat 5-6", schema.bedtime_safe(["threat"], "5-6"), False)
    check("threat 7-8", schema.bedtime_safe(["threat"], "7-8"), True)
    check("death 7-8", schema.bedtime_safe(["death"], "7-8"), False)
    check("death 9-10", schema.bedtime_safe(["death"], "9-10"), True)
    check("violence 9-10", schema.bedtime_safe(["violence"], "9-10"), False)


def test_confidence_never_blocks() -> None:
    """The 0042 regression: bad confidence must not fail a record."""
    conf, log = schema.normalize_confidence({"tone": "uncertain", "setting": "HIGH"})
    check("invalid level nulled", conf["tone"], None)
    check("valid level kept", conf["setting"], "high")
    check("absent level nulled", conf["plot_shape"], None)
    check("nulling logged", any("tone" in m for m in log), True)
    check("all fields present", set(conf) == set(schema.FIELD_NAMES), True)
    conf, log = schema.normalize_confidence("garbage")
    check("non-dict tolerated", conf["tone"], None)
    check("no exception on None", schema.normalize_confidence(None)[0]["tone"], None)


def test_escape_values_score_zero() -> None:
    """Escapes must never act as wildcards matching every query."""
    f = schema.FIELDS_BY_NAME["plot_shape"]
    check("story escape", story_search.field_match(f, "quest/rescue", "other"), 0.0)
    check("query escape", story_search.field_match(f, "other", "quest/rescue"), 0.0)
    check("uncertain scores 0",
          story_search.field_match(f, "exploration", "uncertain"), 0.0)
    check("exact match", story_search.field_match(f, "exploration", "exploration"), 1.0)
    ft = schema.FIELDS_BY_NAME["interest_tags"]
    check("partial overlap",
          story_search.field_match(ft, ["cats", "space"], ["space", "food"]), 0.5)


def test_validation() -> None:
    md = {f.name: (["other"] if f.cardinality == "multi" else "other")
          for f in schema.FIELDS}
    check("escapes valid", schema.validate_metadata(md), [])
    md["tone"] = ["banana"]
    check("off-vocabulary rejected", len(schema.validate_metadata(md)), 1)
    md["tone"] = ["warm/cozy"]
    md["setting"] = ["unspecified/abstract"]
    check("v1 setting valid", schema.validate_metadata(md), [])
    del md["plot_shape"]
    check("missing field rejected",
          any("plot_shape" in e for e in schema.validate_metadata(md)), True)


def test_coerce_invalid() -> None:
    """Off-vocabulary values must never reach the index."""
    good = {f.name: (["other"] if f.cardinality == "multi" else "other")
            for f in schema.FIELDS}

    # The two real v1 failures.
    md = dict(good, story_type=["funny"])
    fixed, log = schema.coerce_invalid(md)
    check("bleed dropped", fixed["story_type"], ["other"])
    check("coercion logged", any("funny" in m for m in log), True)

    md = dict(good, plot_shape="question-and-answer")
    fixed, _ = schema.coerce_invalid(md)
    check("single-value coerced", fixed["plot_shape"], "other")

    # Partially valid multi-value: keep the good, drop the bad.
    md = dict(good, tone=["warm/cozy", "nonsense"])
    fixed, _ = schema.coerce_invalid(md)
    check("valid kept, invalid dropped", fixed["tone"], ["warm/cozy"])

    # Open vocabulary is never coerced.
    md = dict(good, interest_tags=["unicorns", "tractors"])
    fixed, _ = schema.coerce_invalid(md)
    check("open vocab untouched", fixed["interest_tags"], ["unicorns", "tractors"])

    # Junk shapes must not raise and must not leak.
    for junk in (None, 42, [], {}, [None, ""]):
        md = dict(good, setting=junk)
        fixed, _ = schema.coerce_invalid(md)
        check(f"junk setting {junk!r}", fixed["setting"], ["other"])

    # Whatever goes in, the result must always validate.
    for md in ({}, dict(good, story_type=["funny"], plot_shape="q-and-a", tone=None)):
        fixed, _ = schema.coerce_invalid(md)
        check(f"coerced output validates ({len(md)} keys)",
              schema.validate_metadata(fixed), [])

    # Retrieval-equivalence: the claim that justifies coercing to `other`.
    f = schema.FIELDS_BY_NAME["story_type"]
    check("bogus and 'other' score alike",
          story_search.field_match(f, ["everyday"], ["funny"]),
          story_search.field_match(f, ["everyday"], ["other"]))


def test_prompt_mentions_new_rules() -> None:
    """The prompt is the fix for cross-field bleed, so assert it says so."""
    import annotate_corpus
    from corpus_io import Story
    s = Story("0001", "T", "t.md", "CC-BY", "A", "I", "en", "text")
    # Collapse whitespace so hard-wrapped prompt lines still match.
    prompt = " ".join(annotate_corpus.build_prompt(s, "text").split())
    for needle in ("Never put a value from one field into another",
                   'Do NOT default to "problem→solution"',
                   "NEVER a confidence level",
                   "unspecified/abstract"):
        check(f"prompt contains {needle[:34]!r}", needle in prompt, True)


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"ran {len(tests)} test groups")
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print("  ", f)
        raise SystemExit(1)
    print("all passed")


if __name__ == "__main__":
    main()
