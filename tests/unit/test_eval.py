"""Tests for the golden set and the scoreboard.

Nothing measured whether a change to this pipeline helped. These pin the
arithmetic, the refusal to accept an uncountable label, and the guard that
protects the documents a human already decided to keep.
"""

import json

import pytest

from src.eval.golden import (
    PROTECTED_RECALL,
    REJECTION_REASONS,
    GoldenItem,
    GoldenSetError,
    load_golden,
    missing_protected,
    protected_urls,
)
from src.eval.score import evaluate, format_report

HB323 = "https://lis.virginia.gov/bill-details/20261/HB323"


def _write(tmp_path, rows, version="v1"):
    path = tmp_path / f"{version}.jsonl"
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return tmp_path


class TestArithmetic:
    @pytest.mark.small
    def test_a_hand_worked_confusion_matrix(self):
        """Four keeps and six rejects, worked out by hand in the test so the
        expected numbers do not come from the code being tested."""
        golden = [
            GoldenItem(url=f"k{i}", keep=True) for i in range(4)
        ] + [
            GoldenItem(url=f"r{i}", keep=False, reason="duplicate")
            for i in range(6)
        ]
        # Kept three of the four wanted, and two of the six unwanted.
        kept = {"k0", "k1", "k2", "r0", "r1"}
        scores = evaluate(kept, golden)

        assert scores.true_positives == 3
        assert scores.false_positives == 2
        assert scores.false_negatives == 1
        assert scores.precision == pytest.approx(3 / 5)
        assert scores.recall == pytest.approx(3 / 4)
        assert scores.f1 == pytest.approx(2 * 0.6 * 0.75 / (0.6 + 0.75))

    @pytest.mark.small
    def test_precision_is_undefined_not_zero_when_nothing_was_kept(self):
        """Zero reads as a catastrophe. An empty run is not one."""
        golden = [GoldenItem(url="k0", keep=True)]
        scores = evaluate(set(), golden)
        assert scores.precision is None
        assert scores.recall == 0.0

    @pytest.mark.small
    def test_an_unlabelled_document_is_not_counted_against_the_pipeline(self):
        """Finding something nobody has judged yet is not a mistake."""
        golden = [GoldenItem(url="k0", keep=True)]
        scores = evaluate({"k0", "https://something.new/"}, golden)
        assert scores.false_positives == 0
        assert scores.precision == 1.0

    @pytest.mark.small
    def test_wrong_keeps_are_counted_by_reason(self):
        """The largest category is the one worth building a rule for, so the
        reasons have to survive into the score."""
        golden = [
            GoldenItem(url="a", keep=False,
                       reason="thermal_network_without_data_centre"),
            GoldenItem(url="b", keep=False,
                       reason="thermal_network_without_data_centre"),
            GoldenItem(url="c", keep=False, reason="proceedings_or_transcript"),
        ]
        scores = evaluate({"a", "b", "c"}, golden)
        assert scores.by_reason["thermal_network_without_data_centre"] == 2
        assert scores.by_reason["proceedings_or_transcript"] == 1


class TestLoading:
    @pytest.mark.medium
    def test_a_reject_without_a_reason_is_refused_at_load(self, tmp_path):
        """An uncountable label is what made the first review round unusable
        as evidence. It is refused at load, not discovered later."""
        root = _write(tmp_path, [{"url": "https://x.gov/a", "keep": False}])
        with pytest.raises(GoldenSetError, match="reject with no reason"):
            load_golden("v1", golden_dir=root)

    @pytest.mark.medium
    def test_an_unknown_reason_is_refused(self, tmp_path):
        root = _write(tmp_path, [
            {"url": "https://x.gov/a", "keep": False, "reason": "vibes"}])
        with pytest.raises(GoldenSetError, match="unknown reason"):
            load_golden("v1", golden_dir=root)

    @pytest.mark.medium
    def test_a_keep_needs_no_reason(self, tmp_path):
        root = _write(tmp_path, [{"url": HB323, "keep": True}])
        items = load_golden("v1", golden_dir=root)
        assert len(items) == 1
        assert items[0].keep

    @pytest.mark.medium
    def test_loading_the_same_version_twice_is_identical(self, tmp_path):
        root = _write(tmp_path, [
            {"url": HB323, "keep": True},
            {"url": "https://x.gov/b", "keep": False, "reason": "duplicate"},
        ])
        assert load_golden("v1", golden_dir=root) == load_golden("v1", golden_dir=root)

    @pytest.mark.medium
    def test_a_missing_version_names_the_ones_that_exist(self, tmp_path):
        root = _write(tmp_path, [{"url": HB323, "keep": True}], version="v1")
        with pytest.raises(GoldenSetError, match="Available: v1"):
            load_golden("v2", golden_dir=root)

    @pytest.mark.medium
    def test_a_missing_directory_says_none_exist_yet(self, tmp_path):
        with pytest.raises(GoldenSetError, match="None exist yet"):
            load_golden("v1", golden_dir=tmp_path / "nothing")


class TestProtectedRecall:
    @pytest.mark.small
    def test_the_flagship_bill_is_protected(self):
        assert HB323 in protected_urls()

    @pytest.mark.small
    def test_the_curated_keeps_most_at_risk_are_protected(self):
        """The three the data-centre scope rule would drop when applied to
        the sheet's own text. Listed so a test says it, not the silence."""
        urls = protected_urls()
        assert "https://legislation.nysenate.gov/pdf/bills/2021/S9422" in urls
        assert "https://www.nyserda.ny.gov/All-Programs/Heat-Recovery-Program" in urls
        assert "https://www.emb3rs.eu/" in urls

    @pytest.mark.small
    def test_every_protected_entry_explains_itself(self):
        """A failure that says only "something was lost" sends the next
        person hunting. Each entry carries why it is here."""
        for url, why in PROTECTED_RECALL:
            assert url.startswith("http"), url
            assert len(why) > 25, f"{url} has no useful reason"

    @pytest.mark.small
    def test_a_lost_document_is_reported_with_its_reason(self):
        """FAILS ON OLD BEHAVIOR. Nothing protected recall at all before
        this: a change could drop the flagship bill and every test stayed
        green."""
        retrieved = protected_urls() - {HB323}
        missing = missing_protected(retrieved)
        assert len(missing) == 1
        url, why = missing[0]
        assert url == HB323
        assert "first state law" in why

    @pytest.mark.small
    def test_all_losses_are_reported_not_just_the_first(self):
        """Stopping at the first loss hides the others, and the others are
        how you tell a bad rule from a bad document."""
        assert len(missing_protected(set())) == len(PROTECTED_RECALL)

    @pytest.mark.small
    def test_nothing_is_protected_twice(self):
        assert len(protected_urls()) == len(PROTECTED_RECALL)

    @pytest.mark.small
    def test_a_trivially_different_address_is_not_a_loss(self):
        """FAILS ON OLD BEHAVIOR. The first run of this check reported
        Norway's assessment mandate as missing when the store held the same
        document with a chapter anchor on the end. A guard that cries wolf
        teaches people to ignore it."""
        retrieved = {
            url + ("&utm_source=newsletter" if "?" in url else "/")
            for url in protected_urls()
        }
        assert missing_protected(retrieved) == []

    @pytest.mark.small
    def test_a_genuinely_different_document_is_still_a_loss(self):
        """Normalising addresses must not go so far that a different
        document matches a protected one."""
        retrieved = {"https://lis.virginia.gov/bill-details/20261/HB906"}
        missing = missing_protected(retrieved)
        assert any(url.endswith("HB323") for url, _ in missing)


class TestReport:
    @pytest.mark.small
    def test_every_number_carries_a_sentence(self):
        golden = [GoldenItem(url="k0", keep=True),
                  GoldenItem(url="r0", keep=False, reason="duplicate")]
        report = format_report(evaluate({"k0", "r0"}, golden), "v1")
        assert "precision" in report
        assert "Of the 2 items the pipeline kept" in report
        assert "recall" in report
        assert "Of the 1 items the reviewer wanted" in report

    @pytest.mark.small
    def test_a_recall_miss_is_called_out_as_the_expensive_kind(self):
        golden = [GoldenItem(url="k0", keep=True)]
        report = format_report(evaluate(set(), golden), "v1")
        assert "nobody sees them" in report

    @pytest.mark.small
    def test_reasons_are_valid_categories(self):
        for reason in REJECTION_REASONS:
            assert reason.islower()
            assert " " not in reason


class TestProvenance:
    @pytest.mark.small
    def test_the_report_says_whose_judgement_it_measures_against(self):
        """A precision figure is only as good as the labels behind it, so
        the report names them rather than presenting the number as if it
        came from nowhere."""
        from src.eval.score import provenance

        golden = [
            GoldenItem(url="a", keep=True, labelled_by="Anna Dixon",
                       labelled_on="2026-08-01"),
            GoldenItem(url="b", keep=True, labelled_by="Anna Dixon",
                       labelled_on="2026-08-14"),
        ]
        line = provenance(golden)
        assert "Anna Dixon" in line
        assert "2026-08-01 to 2026-08-14" in line

    @pytest.mark.small
    def test_an_unattributed_set_says_so_rather_than_inventing_a_name(self):
        from src.eval.score import provenance

        assert "not recorded" in provenance([GoldenItem(url="a", keep=True)])
