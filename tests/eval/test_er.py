"""Metric and sampling logic on synthetic data (test fixtures, not gold labels)."""

import csv
from pathlib import Path

import pytest

from app.resolution.cascade import Resolved, Unresolved
from eval.er import (
    GOLD_NONE,
    Judgement,
    SampledMention,
    assign_stratum,
    compute_metrics,
    pick_threshold,
    split_of,
    stratified_sample,
)
from eval.er_eval import LabelError, load_labeled
from eval.sample_er_mentions import LabelsExistError, write_sample

RESOLVED_EXACT = Resolved(1, "exact", 1.0, "full", "x")
RESOLVED_FUZZY = Resolved(1, "fuzzy", 0.95, "full", "x")


def _j(
    gold: str, predicted: str | None, stage: str | None = "exact", stratum: str = "s"
) -> Judgement:
    reason = None if predicted else "no_match"
    return Judgement(gold, predicted, stage if predicted else None, reason, stratum)


def test_precision_recall_f1() -> None:
    judgements = [
        _j("1", "1"),  # TP
        _j("2", "2", "fuzzy"),  # TP
        _j("3", "9", "fuzzy"),  # FP (wrong ingredient) and FN
        _j("4", None),  # FN (unresolved)
        _j(GOLD_NONE, "5"),  # FP: resolved something that isn't an ingredient
        _j(GOLD_NONE, None),  # correctly left unresolved: neither
    ]
    m = compute_metrics(judgements)

    assert m["overall"] == {
        "n": 6,
        "tp": 2,
        "fp": 2,
        "fn": 2,
        "precision": 0.5,
        "recall": 0.5,
        "f1": 0.5,
    }
    assert m["per_stage"] == {
        "exact": {"resolved": 2, "precision": 0.5},
        "fuzzy": {"resolved": 2, "precision": 0.5},
    }
    assert m["unresolved"] == {"no_match": 2}


def test_metrics_per_stratum() -> None:
    m = compute_metrics([_j("1", "1", stratum="slash"), _j("2", None, stratum="salt_ester")])
    assert m["per_stratum"]["slash"]["f1"] == 1.0
    assert m["per_stratum"]["salt_ester"]["recall"] == 0.0


def test_empty_metrics_do_not_divide_by_zero() -> None:
    assert compute_metrics([])["overall"]["f1"] == 0.0


def test_split_is_deterministic_and_roughly_70_30() -> None:
    ids = [f"m{i}" for i in range(2000)]
    splits = [split_of(i) for i in ids]
    assert splits == [split_of(i) for i in ids]
    assert 0.65 < splits.count("dev") / len(ids) < 0.75


def test_pick_threshold_prefers_f1_among_precise_thresholds() -> None:
    sweep = [
        (85.0, {"precision": 0.90, "f1": 0.93}),  # best F1 but not precise enough
        (90.0, {"precision": 0.96, "f1": 0.91}),
        (92.0, {"precision": 0.98, "f1": 0.91}),  # tie on F1 -> higher threshold
        (95.0, {"precision": 0.99, "f1": 0.88}),
    ]
    assert pick_threshold(sweep, 0.95) == 92.0
    assert pick_threshold(sweep, 0.999) is None


@pytest.mark.parametrize(
    ("raw", "outcome", "flags", "stratum"),
    [
        ("Aqua/Water", RESOLVED_EXACT, {}, "slash"),
        ("Zea Mays (Corn) Starch", RESOLVED_EXACT, {}, "parenthetical"),
        ("Sodium Hyaluronate", RESOLVED_EXACT, {}, "salt_ester"),
        ("Tocopheryl Acetate", RESOLVED_EXACT, {}, "salt_ester"),
        ("Glycerine", RESOLVED_FUZZY, {}, "misspelling_suspect"),
        ("Vitamin E", Unresolved("below_threshold"), {}, "misspelling_suspect"),
        ("Huile de Tournesol", Unresolved("no_match", ()), {}, "non_english"),
        ("Glycérine", RESOLVED_EXACT, {}, "non_english"),
        ("Lipodermin®", Unresolved("no_match"), {}, "trade_name_suspect"),
        ("CI 77491", RESOLVED_EXACT, {"may_contain": True}, "may_contain_or_noise"),
        ("Glycerin", RESOLVED_EXACT, {}, "random"),
    ],
)
def test_assign_stratum(raw: str, outcome: object, flags: dict[str, bool], stratum: str) -> None:
    kwargs = {"may_contain": False, "suspected_noise": False} | flags
    assert assign_stratum(raw, outcome, **kwargs) == stratum  # type: ignore[arg-type]


def _m(i: int, stratum: str, normalized: str | None = None) -> SampledMention:
    return SampledMention(f"id{i:03d}", "p", i, f"raw {i}", normalized or f"n{i}", stratum)


def test_stratified_sample_targets_dedupe_and_shortfall() -> None:
    mentions = (
        [_m(i, "slash") for i in range(10)]
        + [_m(100, "salt_ester"), _m(101, "salt_ester", normalized="n100")]  # duplicate text
        + [_m(200 + i, "random") for i in range(10)]
    )
    targets = {"slash": 3, "salt_ester": 3, "random": 2}

    sample = stratified_sample(mentions, targets, seed=1)

    by_stratum = [m.stratum for m in sample]
    assert by_stratum.count("salt_ester") == 1  # only one distinct text available
    assert len(sample) == 3 + 1 + 2 + 2  # 2-mention shortfall is filled
    assert len({m.normalized for m in sample}) == len(sample)
    assert sample == stratified_sample(mentions, targets, seed=1)


def _csv(path: Path, gold: str) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["mention_id", "raw_mention", "stratum", "gold_ingredient_id"]
        )
        writer.writeheader()
        writer.writerow(
            {
                "mention_id": "a",
                "raw_mention": "Aqua",
                "stratum": "random",
                "gold_ingredient_id": gold,
            }
        )


def test_write_sample_refuses_to_overwrite_labels(tmp_path: Path) -> None:
    path = tmp_path / "er.csv"
    _csv(path, gold="31959")
    with pytest.raises(LabelsExistError):
        write_sample(path, [_m(1, "slash")])

    _csv(path, gold="")  # unlabeled files may be regenerated
    write_sample(path, [_m(1, "slash")])
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["gold_ingredient_id"] == ""


def test_load_labeled_validates_gold_values(tmp_path: Path) -> None:
    path = tmp_path / "er.csv"
    _csv(path, gold="none")
    assert load_labeled(path, set())[0]["gold_ingredient_id"] == GOLD_NONE
    _csv(path, gold="SKIP")
    assert load_labeled(path, set()) == []
    _csv(path, gold="31959")
    assert len(load_labeled(path, {"31959"})) == 1
    with pytest.raises(LabelError, match="unknown gold_ingredient_id"):
        load_labeled(path, {"1"})
