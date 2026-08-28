"""Precision, recall and F1 against a labelled set, with a plain sentence.

The numbers exist so a tuning change can be argued from evidence. The plain
sentences exist because the person who owns the decision is not the person
who wrote the code, and a bare 0.21 does not tell her whether to approve
the next change.
"""

import argparse
import sys
from dataclasses import dataclass, field

from .golden import GoldenItem, GoldenSetError, load_golden


@dataclass
class Scores:
    """One measurement of the pipeline against a labelled set."""

    kept: int = 0
    should_keep: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    by_reason: dict[str, int] = field(default_factory=dict)

    @property
    def precision(self) -> float | None:
        """Of what we kept, how much should we have kept.

        None rather than zero when nothing was kept: zero reads as a
        catastrophe, and an empty run is not a catastrophe.
        """
        if not self.kept:
            return None
        return self.true_positives / self.kept

    @property
    def recall(self) -> float | None:
        """Of what we should have kept, how much did we find."""
        if not self.should_keep:
            return None
        return self.true_positives / self.should_keep

    @property
    def f1(self) -> float | None:
        precision, recall = self.precision, self.recall
        if precision is None or recall is None or (precision + recall) == 0:
            return None
        return 2 * precision * recall / (precision + recall)


def evaluate(kept_urls: set[str], golden: list[GoldenItem]) -> Scores:
    """Score a set of kept URLs against the labels.

    Only labelled documents count. A document the pipeline kept that nobody
    has labelled is not evidence either way, and counting it as wrong would
    punish the pipeline for finding something new.
    """
    scores = Scores()
    for item in golden:
        was_kept = item.url in kept_urls
        if item.keep:
            scores.should_keep += 1
            if was_kept:
                scores.true_positives += 1
            else:
                scores.false_negatives += 1
        elif was_kept:
            scores.false_positives += 1
            reason = item.reason or "other"
            scores.by_reason[reason] = scores.by_reason.get(reason, 0) + 1
    scores.kept = scores.true_positives + scores.false_positives
    return scores


def _pct(value: float | None) -> str:
    return "not measurable" if value is None else f"{value:.0%}"


def provenance(golden: list[GoldenItem]) -> str:
    """Who labelled this set and when.

    A precision figure is only as good as the labels behind it, so the
    report says whose judgement it is measuring against rather than
    presenting the number as if it came from nowhere.
    """
    people = sorted({item.labelled_by for item in golden if item.labelled_by})
    dates = sorted({item.labelled_on for item in golden if item.labelled_on})
    if not people and not dates:
        return "Labelled by: not recorded."
    who = ", ".join(people) if people else "not recorded"
    when = (dates[0] if len(dates) == 1
            else f"{dates[0]} to {dates[-1]}" if dates else "not recorded")
    return f"Labelled by {who}, {when}."


def format_report(scores: Scores, version: str,
                  golden: list[GoldenItem] | None = None) -> str:
    """The numbers with a sentence under each, for a person to read."""
    lines = [
        f"Measured against golden set {version}.",
        provenance(golden) if golden else "",
        "",
        f"  precision  {_pct(scores.precision)}",
        f"             Of the {scores.kept} items the pipeline kept, "
        f"{scores.true_positives} were ones the reviewer wanted.",
        "",
        f"  recall     {_pct(scores.recall)}",
        f"             Of the {scores.should_keep} items the reviewer wanted, "
        f"the pipeline found {scores.true_positives}.",
        "",
        f"  F1         {_pct(scores.f1)}",
        "             The two above balanced against each other. Use it to "
        "compare runs, not on its own.",
    ]

    if scores.false_negatives:
        lines += [
            "",
            f"  {scores.false_negatives} wanted items were missed. Recall "
            "misses are the expensive kind: nobody sees them.",
        ]

    if scores.by_reason:
        lines += ["", "  Wrongly kept, by the reviewer's reason:"]
        for reason, count in sorted(
            scores.by_reason.items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"    {count:>4}  {reason.replace('_', ' ')}")
        lines += [
            "",
            "  The largest category is the one worth building a rule for. "
            "The rest can wait.",
        ]

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score the pipeline against a labelled set.")
    parser.add_argument("--golden", default="v1",
                        help="Which version of the labelled set to score against.")
    args = parser.parse_args(argv)

    try:
        golden = load_golden(args.golden)
    except GoldenSetError as e:
        print(f"Could not score: {e}", file=sys.stderr)
        return 1

    from ..storage.store import PolicyStore

    kept = {p.get("url", "") for p in PolicyStore().get_all()}
    scores = evaluate(kept, golden)
    print(format_report(scores, args.golden, golden))
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    raise SystemExit(main())
