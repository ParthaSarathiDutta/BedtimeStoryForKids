"""Taxonomy validation and diagnostics.

Answers one question: are these ten fields worth annotating a corpus with?

The checks are deliberately not all distribution-based. A field can have a
healthy-looking distribution and still be useless -- if the same story gets
`exploration` on one pass and `quest/rescue` on the next, the distribution is
measuring noise. Hence the self-consistency and ablation checks.

See METADATA_PREPARATION_PLAN.md sections 5.3 and 5.4.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import schema
import story_search

CACHE_DIR = Path("artifacts/annotation_cache")

# Thresholds. Dominance is a WARNING, not a failure: if the corpus genuinely
# consists of simple stories then reading_band skewing to 5-6 is a fact about
# the corpus, not a defect in the taxonomy. A field is only problematic when it
# is dominant AND contributes little retrieval discrimination.
DOMINANCE_WARN = 0.90
ESCAPE_FAIL = 0.20
REDUNDANCY_WARN = 0.95

# Synthetic requests for the ablation test. Deliberately varied in how much they
# specify, since real children range from "a story about a dog" to a fully-formed
# premise.
#
# The last few include `plot_shape` and `narrative_style`, which a child would
# never state. Those come from the Planner once it has picked a structure from
# the retrieved examples, so they are legitimate query fields even though they
# are not child-facing -- and without them the ablation could not evaluate two
# of the ten fields at all.
SAMPLE_REQUESTS: list[dict[str, Any]] = [
    {"interest_tags": ["cats", "space"], "tone": ["funny"]},
    {"interest_tags": ["dogs"], "protagonist_type": "animal"},
    {"story_type": ["bedtime"], "energy_level": "calm"},
    {"interest_tags": ["magic"], "fantasy_level": "fully magical"},
    {"story_type": ["adventure"], "setting": ["fantasy/space"], "tone": ["exciting"]},
    {"interest_tags": ["friendship"], "tone": ["warm/cozy"]},
    {"story_type": ["folktale"], "protagonist_type": "animal"},
    {"interest_tags": ["school"], "setting": ["home/school"]},
    {"story_type": ["discovery/learning"], "tone": ["curious"]},
    {"interest_tags": ["night"], "story_type": ["bedtime"], "energy_level": "calm"},
    {"protagonist_type": "child", "setting": ["nature/farm/jungle"]},
    {"interest_tags": ["food"], "tone": ["funny"]},
    {"story_type": ["mystery"], "energy_level": "mildly tense/spooky"},
    {"interest_tags": ["family"], "tone": ["heartfelt"]},
    {"interest_tags": ["dinosaurs"], "fantasy_level": "fully magical"},
    {"setting": ["travel"], "story_type": ["adventure"]},
    {"interest_tags": ["animals", "friendship"], "protagonist_type": "animal",
     "tone": ["warm/cozy"]},
    {"story_type": ["everyday"], "fantasy_level": "realistic"},
    {"interest_tags": ["sports"], "energy_level": "exciting"},
    {"interest_tags": ["art"], "tone": ["wondrous"], "protagonist_type": "child"},
    # Planner-supplied structural fields.
    {"interest_tags": ["animals"], "plot_shape": "quest/rescue"},
    {"story_type": ["bedtime"], "plot_shape": "exploration",
     "narrative_style": ["repetitive"]},
    {"interest_tags": ["space"], "plot_shape": "problem→solution",
     "tone": ["funny"]},
    {"story_type": ["folktale"], "plot_shape": "overcome challenge",
     "narrative_style": ["dialogue-heavy"]},
    {"interest_tags": ["night"], "narrative_style": ["rhyming/poetic"],
     "energy_level": "calm"},
]


def load_annotations(variant: str = "a") -> dict[str, dict]:
    suffix = f"_{schema.SCHEMA_VERSION}_{variant}.json"
    out: dict[str, dict] = {}
    for path in sorted(CACHE_DIR.glob(f"*{suffix}")):
        story_id = path.name[: -len(suffix)]
        try:
            out[story_id] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
    return out


def _values(record: dict, field: schema.Field) -> list[str]:
    raw = record.get("search_metadata", {}).get(field.name)
    if raw is None:
        return []
    return list(raw) if isinstance(raw, list) else [raw]


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


def report_distributions(records: dict[str, dict]) -> dict[str, dict]:
    """Per-field label distributions.

    Multi-valued fields are reported as coverage (share of stories carrying a
    label) rather than as a share of label instances, because instance counts
    do not sum to the story count and the two are easy to confuse.
    """
    n = len(records)
    print(f"\n{'=' * 74}\nLABEL DISTRIBUTIONS  ({n} stories)\n{'=' * 74}")
    summary: dict[str, dict] = {}

    for field in schema.FIELDS:
        counts: Counter[str] = Counter()
        for rec in records.values():
            for v in set(_values(rec, field)):
                counts[v] += 1
        if not counts:
            print(f"\n{field.name}: NO DATA")
            summary[field.name] = {"dominance": 1.0, "escape_rate": 1.0, "distinct": 0}
            continue

        escapes = sum(counts[e] for e in schema.ESCAPE_VALUES)
        top_value, top_count = counts.most_common(1)[0]
        dominance = top_count / n

        card = field.cardinality
        print(f"\n{field.name}  [{card}, weight {field.weight}]")
        for value, count in counts.most_common(12):
            bar = "#" * max(1, round(count / n * 34))
            flag = "  <-- escape" if value in schema.ESCAPE_VALUES else ""
            print(f"    {value:<28} {count:>4}  {count / n * 100:>5.1f}%  {bar}{flag}")
        if len(counts) > 12:
            print(f"    ... {len(counts) - 12} more values")

        note = []
        if dominance > DOMINANCE_WARN:
            note.append(f"WARN dominance {dominance:.0%} on {top_value!r}")
        if escapes / n > ESCAPE_FAIL:
            note.append(f"FAIL escape rate {escapes / n:.0%}")
        if note:
            print("    " + " | ".join(note))

        summary[field.name] = {
            "dominance": round(dominance, 3),
            "dominant_value": top_value,
            "escape_rate": round(escapes / n, 3),
            "distinct": len(counts),
        }
    return summary


def report_escapes(records: dict[str, dict]) -> dict[str, dict]:
    """`other` and `uncertain` rates, kept separate.

    They diagnose different faults: high `other` means the taxonomy is missing
    a category; high `uncertain` means the definitions are ambiguous or the text
    is insufficient. Collapsing them loses the diagnostic.
    """
    n = len(records)
    print(f"\n{'=' * 74}\nESCAPE VALUE RATES\n{'=' * 74}")
    print(f"{'field':<22}{'other':>10}{'uncertain':>12}   diagnosis")
    out: dict[str, dict] = {}
    for field in schema.FIELDS:
        other = uncertain = 0
        for rec in records.values():
            vals = set(_values(rec, field))
            other += schema.OTHER in vals
            uncertain += schema.UNCERTAIN in vals
        diag = ""
        if other / n > ESCAPE_FAIL:
            diag = "missing category"
        elif uncertain / n > ESCAPE_FAIL:
            diag = "ambiguous definitions"
        print(f"{field.name:<22}{other / n * 100:>9.1f}%{uncertain / n * 100:>11.1f}%   {diag}")
        out[field.name] = {"other": round(other / n, 3), "uncertain": round(uncertain / n, 3)}
    return out


def report_confidence(records: dict[str, dict]) -> dict[str, float]:
    """Diagnostics only.

    The v0 pilot showed self-reported confidence is not merely weakly
    calibrated but near-constant: `reading_band`, `narrative_style`, and
    `energy_level` all returned 100% "high", including on labels that were
    plainly wrong. It is therefore excluded from the acceptance checklist and
    from every retrieval and filtering decision. Printed because a sudden shift
    in the pattern would still be worth noticing.
    """
    n = len(records)
    print(f"\n{'=' * 74}\nCONFIDENCE  (diagnostic only -- NOT used in any decision)\n{'=' * 74}")
    print(f"{'field':<22}{'high':>8}{'medium':>9}{'low':>7}{'null':>7}")
    low_rates: dict[str, float] = {}
    for name in schema.FIELD_NAMES:
        counts: Counter[str] = Counter()
        nulls = 0
        for rec in records.values():
            level = rec.get("annotation", {}).get("confidence", {}).get(name)
            if level:
                counts[level] += 1
            else:
                nulls += 1
        total = sum(counts.values()) or 1
        low_rates[name] = round(counts["low"] / total, 3)
        print(f"{name:<22}{counts['high'] / total * 100:>7.0f}%"
              f"{counts['medium'] / total * 100:>8.0f}%"
              f"{counts['low'] / total * 100:>6.0f}%{nulls:>7}")
    spread = {name for name, r in low_rates.items()}
    if len(spread) <= 1:
        print("\n  Note: confidence is effectively constant across fields, "
              "consistent with the pilot finding.")
    return low_rates


def report_self_consistency(a: dict[str, dict], b: dict[str, dict]) -> dict[str, float]:
    """Agreement between two independent annotation passes.

    This is the check distribution analysis cannot do. A field whose value
    flips between identical runs is noisy no matter how healthy its
    distribution looks.
    """
    shared = sorted(set(a) & set(b))
    print(f"\n{'=' * 74}\nSELF-CONSISTENCY  ({len(shared)} stories annotated twice)\n{'=' * 74}")
    if not shared:
        print("  No variant-b annotations found.")
        print("  Run: annotate_corpus.py --variant b --no-cache")
        return {}

    agreement: dict[str, float] = {}
    print(f"{'field':<22}{'agreement':>11}   status")
    for field in schema.FIELDS:
        scores = []
        for sid in shared:
            va, vb = set(_values(a[sid], field)), set(_values(b[sid], field))
            if not va and not vb:
                continue
            union = va | vb
            scores.append(len(va & vb) / len(union) if union else 1.0)
        score = sum(scores) / len(scores) if scores else 0.0
        agreement[field.name] = round(score, 3)
        status = "WARN unstable" if score < 0.6 else ""
        print(f"{field.name:<22}{score * 100:>10.0f}%   {status}")
    return agreement


def report_redundancy(records: dict[str, dict]) -> list[tuple[str, str, float]]:
    """Pairwise field redundancy.

    `tone` / `energy_level` is a pre-registered hypothesis rather than a
    generic sweep: those two are the most likely pair to collapse into each
    other, so the check is stated in advance and looked for deliberately.
    """
    print(f"\n{'=' * 74}\nREDUNDANCY\n{'=' * 74}")
    single = [f for f in schema.FIELDS if f.cardinality == "single"]
    findings: list[tuple[str, str, float]] = []

    for i, fa in enumerate(single):
        for fb in single[i + 1:]:
            pairs: dict[str, Counter[str]] = defaultdict(Counter)
            for rec in records.values():
                va, vb = _values(rec, fa), _values(rec, fb)
                if va and vb:
                    pairs[va[0]][vb[0]] += 1
            total = sum(sum(c.values()) for c in pairs.values())
            if not total:
                continue
            # If knowing fa determines fb, fb adds nothing beyond fa.
            predictable = sum(c.most_common(1)[0][1] for c in pairs.values())
            ratio = predictable / total
            if ratio >= REDUNDANCY_WARN:
                findings.append((fa.name, fb.name, round(ratio, 3)))

    pre = _pair_predictability(records, "tone", "energy_level")
    print(f"  pre-registered  tone -> energy_level:  {pre:.0%} predictable"
          f"{'   WARN likely redundant' if pre >= REDUNDANCY_WARN else '   ok, distinct'}")

    if findings:
        for fa, fb, ratio in findings:
            print(f"  WARN  {fa} -> {fb}: {ratio:.0%} predictable, possible merge candidate")
    else:
        print("  No single-valued field pair is redundant above "
              f"{REDUNDANCY_WARN:.0%}.")
    return findings


def _pair_predictability(records: dict[str, dict], name_a: str, name_b: str) -> float:
    fa, fb = schema.FIELDS_BY_NAME[name_a], schema.FIELDS_BY_NAME[name_b]
    pairs: dict[str, Counter[str]] = defaultdict(Counter)
    for rec in records.values():
        va, vb = _values(rec, fa), _values(rec, fb)
        if va and vb:
            pairs[va[0]][vb[0]] += 1
    total = sum(sum(c.values()) for c in pairs.values())
    if not total:
        return 0.0
    return sum(c.most_common(1)[0][1] for c in pairs.values()) / total


def report_discrimination(records: dict[str, dict], index: list[dict],
                          dominance: dict[str, dict]) -> dict[str, float]:
    """Ablation: does dropping a field change what retrieval returns?

    This is what makes "dominant and provides little discrimination" runnable
    rather than a judgment call. Reported for every scored field, but it only
    condemns a field when paired with high dominance.
    """
    print(f"\n{'=' * 74}\nRETRIEVAL DISCRIMINATION  (ablation over "
          f"{len(SAMPLE_REQUESTS)} sample requests)\n{'=' * 74}")
    print(f"{'field':<22}{'top-3 changed':>15}   verdict")

    baselines = [
        [h.story_id for h in story_search.search_stories(req, index, 3)]
        for req in SAMPLE_REQUESTS
    ]

    impact: dict[str, float] = {}
    for field in schema.SCORED_FIELDS:
        changed = 0
        relevant = 0
        for req, base in zip(SAMPLE_REQUESTS, baselines):
            if field.name not in req:
                continue
            relevant += 1
            ablated = [h.story_id for h in
                       story_search.search_stories(req, index, 3, skip_fields=[field.name])]
            if ablated != base:
                changed += 1
        share = changed / relevant if relevant else 0.0
        impact[field.name] = round(share, 3)

        dom = dominance.get(field.name, {}).get("dominance", 0.0)
        if relevant == 0:
            verdict = "not exercised by samples"
        elif share == 0 and dom > DOMINANCE_WARN:
            verdict = "FAIL dominant and no discrimination"
        elif share == 0:
            verdict = "WARN no effect on ranking"
        else:
            verdict = "earns its weight"
        label = f"{changed}/{relevant}" if relevant else "-"
        print(f"{field.name:<22}{label:>15}   {verdict}")
    return impact


def report_checklist(records: dict[str, dict], dominance: dict[str, dict],
                     escapes: dict[str, dict], low_conf: dict[str, float],
                     agreement: dict[str, float], redundancy: list,
                     impact: dict[str, float]) -> bool:
    print(f"\n{'=' * 74}\nVALIDATION CHECKLIST\n{'=' * 74}")

    flagged_dominant = [f for f, d in dominance.items() if d["dominance"] > DOMINANCE_WARN]
    high_other = [f for f, e in escapes.items() if e["other"] > ESCAPE_FAIL]
    high_uncertain = [f for f, e in escapes.items() if e["uncertain"] > ESCAPE_FAIL]
    unstable = [f for f, s in agreement.items() if s < 0.6] if agreement else []
    dead = [f for f, s in impact.items()
            if s == 0 and dominance.get(f, {}).get("dominance", 0) > DOMINANCE_WARN]
    tags = {t for rec in records.values()
            for t in _values(rec, schema.FIELDS_BY_NAME["interest_tags"])}

    checks = [
        ("Dominant fields flagged (warning, not failure)",
         True, f"{len(flagged_dominant)} flagged: {flagged_dominant or 'none'}"),
        ("'other' rate below threshold", not high_other,
         f"over {ESCAPE_FAIL:.0%}: {high_other or 'none'}"),
        ("'uncertain' rate below threshold", not high_uncertain,
         f"over {ESCAPE_FAIL:.0%}: {high_uncertain or 'none'}"),
        ("Repeat annotations agree", not unstable,
         f"unstable: {unstable or ('none' if agreement else 'NOT RUN')}"),
        ("tone / energy_level not redundant",
         _pair_predictability(records, "tone", "energy_level") < REDUNDANCY_WARN,
         "pre-registered hypothesis"),
        ("No other field pair redundant", not redundancy,
         f"{len(redundancy)} pair(s) flagged"),
        ("interest_tags diverse", len(tags) >= 10, f"{len(tags)} distinct tags"),
        ("No field both dominant and non-discriminating", not dead,
         f"{dead or 'none'}"),
    ]

    all_pass = True
    for label, ok, detail in checks:
        mark = "[x]" if ok else "[ ]"
        if not ok:
            all_pass = False
        print(f"  {mark} {label:<48} {detail}")

    print(f"\n{'=' * 74}")
    if all_pass:
        print("RESULT: no failure signal. Lock schema v0 as v1 unchanged.")
    else:
        print("RESULT: failure signal present. Revise the taxonomy ONCE, then re-run.")
    print(f"{'=' * 74}")
    return all_pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate the taxonomy against annotations.")
    ap.add_argument("--variant", default="a")
    ap.add_argument("--compare-variant", default="b",
                    help="second pass for the self-consistency check")
    ap.add_argument("--index", type=Path, default=None,
                    help="corpus_index.json; defaults to the annotation cache")
    ap.add_argument("--json-out", type=Path, default=Path("artifacts/validation_report.json"))
    args = ap.parse_args()

    records = load_annotations(args.variant)
    if not records:
        raise SystemExit(
            f"No variant-{args.variant} annotations in {CACHE_DIR}. "
            "Run annotate_corpus.py first."
        )

    if args.index and args.index.exists():
        index = json.loads(args.index.read_text(encoding="utf-8"))["stories"]
    else:
        index = [
            {"source": {"id": sid, "title": sid}, **rec}
            for sid, rec in records.items()
        ]

    dominance = report_distributions(records)
    escapes = report_escapes(records)
    low_conf = report_confidence(records)
    agreement = report_self_consistency(records, load_annotations(args.compare_variant))
    redundancy = report_redundancy(records)
    impact = report_discrimination(records, index, dominance)
    passed = report_checklist(records, dominance, escapes, low_conf,
                             agreement, redundancy, impact)

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps({
        "schema_version": schema.SCHEMA_VERSION,
        "stories": len(records),
        "passed": passed,
        "dominance": dominance,
        "escapes": escapes,
        "low_confidence": low_conf,
        "self_consistency": agreement,
        "redundant_pairs": redundancy,
        "retrieval_impact": impact,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
