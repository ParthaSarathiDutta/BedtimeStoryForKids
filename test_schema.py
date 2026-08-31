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


def test_cache_fingerprint() -> None:
    """A changed prompt or vocabulary must invalidate the cache by itself."""
    import annotate_corpus

    annotate_corpus.annotation_fingerprint.cache_clear()
    base = annotate_corpus.annotation_fingerprint()
    check("fingerprint is stable", annotate_corpus.annotation_fingerprint(), base)
    check("fingerprint in path",
          base in annotate_corpus.cache_path("0001", "a").name, True)
    check("variant still separates",
          annotate_corpus.cache_path("0001", "a")
          != annotate_corpus.cache_path("0001", "b"), True)

    def refingerprint():
        annotate_corpus.annotation_fingerprint.cache_clear()
        return annotate_corpus.annotation_fingerprint()

    # 1. Prompt text change.
    original_prompt = annotate_corpus.PROMPT_TEMPLATE
    try:
        annotate_corpus.PROMPT_TEMPLATE = original_prompt + "\nExtra instruction."
        check("prompt edit invalidates", refingerprint() != base, True)
    finally:
        annotate_corpus.PROMPT_TEMPLATE = original_prompt
    check("restoring prompt restores hash", refingerprint(), base)

    # 2. Vocabulary change.
    original_fields = schema.FIELDS
    try:
        tone = schema.FIELDS_BY_NAME["tone"]
        widened = schema.Field(tone.name, tone.values + ("brand new",),
                               tone.cardinality, tone.child_facing, tone.weight,
                               tone.open_vocabulary)
        schema.FIELDS = tuple(widened if f.name == "tone" else f
                              for f in original_fields)
        check("vocabulary edit invalidates", refingerprint() != base, True)
    finally:
        schema.FIELDS = original_fields
    check("restoring vocabulary restores hash", refingerprint(), base)

    # 3. Synonym-map change: invisible in the prompt, but the cache stores
    #    post-normalization labels, so it still has to invalidate.
    schema._INTEREST_SYNONYMS["zzz-probe"] = "animals"
    try:
        check("synonym edit invalidates", refingerprint() != base, True)
    finally:
        del schema._INTEREST_SYNONYMS["zzz-probe"]
    check("restoring synonyms restores hash", refingerprint(), base)

    # The validator must read exactly what the annotator wrote.
    import validate_taxonomy
    check("validator suffix matches writer",
          annotate_corpus.cache_path("0001", "a").name.endswith(
              validate_taxonomy.annotate_corpus.cache_suffix("a")), True)


def test_failures_are_not_cached() -> None:
    """A transient API failure must not be checkpointed as a finished story."""
    import annotate_corpus
    from corpus_io import Story

    story = Story("9999", "Probe", "p.md", "CC-BY", "A", "I", "en",
                  "Once there was a probe story about a cat.")
    path = annotate_corpus.cache_path(story.story_id, "a")
    path.unlink(missing_ok=True)

    class AlwaysFails:
        mock = False
        calls = 0

        def complete(self, prompt, salt=""):
            AlwaysFails.calls += 1
            raise RuntimeError("Error code: 429 - rate_limit_exceeded")

    rec = annotate_corpus.annotate_story(story, AlwaysFails())
    check("failure reported", rec["annotation"]["failed"], True)
    check("failure not written to cache", path.exists(), False)

    # A second run must actually retry rather than skip the story.
    before = AlwaysFails.calls
    annotate_corpus.annotate_story(story, AlwaysFails())
    check("resume retries the failure", AlwaysFails.calls > before, True)

    # A legacy failed entry already on disk must also be retried, not trusted.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"search_metadata": {}, "annotation": {"failed": true}}')
    before = AlwaysFails.calls
    annotate_corpus.annotate_story(story, AlwaysFails())
    check("stale failed entry ignored", AlwaysFails.calls > before, True)
    path.unlink(missing_ok=True)

    # A successful record still caches and still short-circuits.
    class Succeeds:
        mock = True
        calls = 0

        def complete(self, prompt, salt=""):
            Succeeds.calls += 1
            return annotate_corpus._mock_response(prompt, salt)

    ok = annotate_corpus.annotate_story(story, Succeeds())
    check("success has metadata", bool(ok["search_metadata"]), True)
    check("success is cached", path.exists(), True)
    before = Succeeds.calls
    annotate_corpus.annotate_story(story, Succeeds())
    check("success short-circuits", Succeeds.calls, before)
    path.unlink(missing_ok=True)


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
