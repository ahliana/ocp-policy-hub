"""Tests for the data-centre scope rule.

The rule exists because the reviewer and the pipeline disagreed about the
subject. Her decision on 2026-08-28: a policy without a data centre is not
what this tool is looking for. These tests pin both that rule and the two
positions either side of it, so the setting has a real no-change option and
a real middle.
"""

import pytest

from src.core.scope import (
    ADJACENT,
    DEFAULT_SETTING,
    IN_SCOPE,
    OUT_OF_SCOPE,
    OFF,
    REQUIRED,
    mentions_data_center,
    scope_setting,
    scope_verdict,
    screening_scope_line,
)

# The New Jersey bill named in the LegiScan source comment, stored in the
# production database, and rejected by the reviewer. It never says "data
# centre" and that is the whole point of it as a fixture.
NJ_A4490 = (
    "Thermal Energy Network Pilot Program for Gas Public Utilities. "
    "Directs utilities to establish thermal energy network pilot projects "
    "using geothermal, surface water and wastewater as thermal sources."
)

# Virginia HB 323, enacted, the flagship recall case.
VA_HB323 = (
    "Department of Energy; use of waste heat from data centers; findings "
    "and recommendations; work group; report."
)

# The German efficiency act: references data centres without being only
# about them, so a rule keyed on exclusivity would wrongly drop it.
DE_ENEFG = (
    "Energieeffizienzgesetz. Rechenzentren mit einer nicht redundanten "
    "Nennanschlussleistung von 300 Kilowatt oder mehr muessen Abwaerme nutzen."
)


class TestMentions:
    @pytest.mark.small
    def test_the_flagship_bill_is_recognised(self):
        assert mentions_data_center(VA_HB323)

    @pytest.mark.small
    def test_the_rejected_thermal_network_bill_is_not(self):
        assert not mentions_data_center(NJ_A4490)

    @pytest.mark.small
    def test_german_is_recognised_without_english(self):
        """A Danish or German bill does not say "data centre" in English.
        An English-only rule would drop most of the European corpus."""
        assert mentions_data_center(DE_ENEFG)

    @pytest.mark.small
    @pytest.mark.parametrize("text", [
        "datacenter", "data centres", "Datacentrum", "datasenter",
        "centre de données", "データセンター", "konesali",
    ])
    def test_spellings_and_languages(self, text):
        assert mentions_data_center(text)

    @pytest.mark.small
    def test_empty_text_is_not_a_match(self):
        assert not mentions_data_center("")
        assert not mentions_data_center(None)

    @pytest.mark.small
    def test_an_unrelated_compound_does_not_match(self):
        """Word boundaries matter: a term buried inside another word is
        not a reference to a data centre."""
        assert not mentions_data_center("metadatacenterpiece")

    @pytest.mark.small
    @pytest.mark.parametrize("text", [
        "data center", "data centre", "datacenter", "datacentre",
        "data centers", "data centres", "Data Center", "DATA CENTRE",
    ])
    def test_both_spellings_open_closed_and_plural(self, text):
        """American and British, spaced and closed up, singular and plural.
        Bills use all of them and none is more correct than another."""
        assert mentions_data_center(text)

    @pytest.mark.small
    @pytest.mark.parametrize("text", [
        "data-center",
        "data-centre",
        "data  centre",
        "data" + chr(0x00A0) + "centre",   # non-breaking space, out of HTML
        "data" + chr(0x000A) + "centre",   # a line break, out of a PDF
    ])
    def test_however_the_two_words_are_joined(self, text):
        """FAILS ON OLD BEHAVIOR. The matcher took a single ordinary space,
        so the hyphenated form was missed entirely and a document reading
        "data-centre waste heat" was silently out of scope. Legislative text
        hyphenates compounds, stripped markup leaves non-breaking spaces,
        and a line break lands between the words in any PDF extraction."""
        assert mentions_data_center(text)

    @pytest.mark.small
    def test_a_hyphen_does_not_join_unrelated_words(self):
        """The separator is tolerant, not blind: it still has to be this
        term's words in this order."""
        assert not mentions_data_center("data-driven centre of excellence")


class TestVerdict:
    @pytest.mark.small
    def test_required_puts_a_thermal_network_bill_out_of_scope(self):
        """FAILS ON OLD BEHAVIOR. Today this bill is kept: the screening
        prompt says heat network policy counts whether or not data centres
        are named, and this bill is in the production database as a result."""
        assert scope_verdict(NJ_A4490, REQUIRED) == "out"

    @pytest.mark.small
    def test_adjacent_keeps_the_same_bill_and_marks_it(self):
        assert scope_verdict(NJ_A4490, ADJACENT) == "adjacent"

    @pytest.mark.small
    def test_off_is_a_true_no_change_position(self):
        """Every document is in scope under off, including one with no
        text at all, so the switch can be returned to where it was."""
        assert scope_verdict(NJ_A4490, OFF) == "in_scope"
        assert scope_verdict("", OFF) == "in_scope"

    @pytest.mark.small
    @pytest.mark.parametrize("setting", [REQUIRED, ADJACENT, OFF])
    def test_the_flagship_bill_survives_every_setting(self, setting):
        """HB 323 names data centres explicitly, so no scope setting may
        cost us the one document this system is judged on."""
        assert scope_verdict(VA_HB323, setting) == "in_scope"

    @pytest.mark.small
    @pytest.mark.parametrize("setting", [REQUIRED, ADJACENT, OFF])
    def test_the_german_act_survives_every_setting(self, setting):
        """Marked "not exclusive to data centers" in the curated master
        tab, and still a keep. Exclusivity is not the question being asked."""
        assert scope_verdict(DE_ENEFG, setting) == "in_scope"


class TestSetting:
    @pytest.mark.small
    def test_the_default_is_the_reviewers_rule(self):
        assert DEFAULT_SETTING == REQUIRED
        assert scope_setting(None) == REQUIRED
        assert scope_setting({}) == REQUIRED

    @pytest.mark.small
    def test_a_configured_setting_is_read(self):
        assert scope_setting({"data_center_required": "adjacent"}) == ADJACENT
        assert scope_setting({"data_center_required": "OFF"}) == OFF

    @pytest.mark.small
    def test_an_unknown_value_narrows_rather_than_widens(self):
        """A typo must not quietly widen the scope. Falling back to off
        would let everything through and look like the rule working."""
        assert scope_setting({"data_center_required": "yes please"}) == REQUIRED


class TestPromptLine:
    @pytest.mark.small
    def test_the_prompt_changes_with_the_setting(self):
        """FAILS ON OLD BEHAVIOR. The line was written into the prompt as a
        constant, so the model kept asserting the broad reading no matter
        what the filter did."""
        required = screening_scope_line(REQUIRED)
        off = screening_scope_line(OFF)
        assert required != off
        assert "MUST concern data centres" in required
        assert "WHETHER OR NOT" in off

    @pytest.mark.small
    def test_every_setting_produces_a_line(self):
        for setting in (REQUIRED, ADJACENT, OFF):
            assert screening_scope_line(setting).strip()


class TestTheGateSeesStructuredSources:
    """The reason the gate sits where it does.

    Structured API sources skip the keyword stage entirely: the pipeline
    marks them relevant unconditionally, and 23 of the 24 configured
    sources are of that kind. They also produce most of what reaches the
    database. A scope rule added to the keyword layer would therefore never
    be consulted for the documents it exists to catch.
    """

    @pytest.mark.small
    def test_the_scope_gate_is_not_inside_the_crawl_only_branch(self):
        """FAILS ON OLD BEHAVIOR. Reads the pipeline source and checks the
        gate is applied after the two lanes rejoin rather than inside the
        branch structured sources skip."""
        from pathlib import Path

        source = (Path(__file__).resolve().parents[2]
                  / "src" / "core" / "scanner.py").read_text(encoding="utf-8")

        assert "scope_verdict(" in source, "the scope gate is not wired in at all"

        structured_branch = source.index("is_structured = ")
        keyword_branch_end = source.index("# Stage 4: Cache check")
        gate_at = source.index("scope_verdict(")
        assert gate_at > keyword_branch_end > structured_branch, (
            "The scope gate must run after the cache check, where the crawl "
            "and structured lanes have rejoined. Inside the crawl branch it "
            "would never see a LegiScan bill."
        )

    @pytest.mark.small
    def test_the_gate_runs_before_any_model_call(self):
        """An out-of-scope document should cost nothing, so the gate must
        come before the screener rather than after it."""
        from pathlib import Path

        source = (Path(__file__).resolve().parents[2]
                  / "src" / "core" / "scanner.py").read_text(encoding="utf-8")
        assert source.index("scope_verdict(") < source.index("screen_relevance("), (
            "The scope gate must precede the screening call or it saves nothing."
        )


class TestTheGateReadsSourceText:
    """Lesson PL-001, decision ADR-0001.

    The analysis model writes "data centers" into summaries of bills that
    never say it. A rule evaluated on a stored summary therefore passes
    exactly the documents it exists to drop, and reports that it ran.
    """

    # The summary stored for NJ A4490 in the production database on
    # 2026-08-28. The bill itself is NJ_A4490 above, which never mentions a
    # data centre.
    NJ_A4490_STORED_SUMMARY = (
        "Establishes a thermal energy network pilot program for gas public "
        "utilities that could incorporate waste heat sources including data "
        "centers."
    )

    @pytest.mark.small
    def test_the_summary_is_in_scope_while_the_bill_is_not(self):
        """The trap in one assertion: same document, opposite verdicts,
        depending on which text the rule is shown."""
        assert scope_verdict(self.NJ_A4490_STORED_SUMMARY, REQUIRED) == IN_SCOPE
        assert scope_verdict(NJ_A4490, REQUIRED) == OUT_OF_SCOPE

    @pytest.mark.small
    def test_the_verdict_is_taken_on_extracted_text_not_a_summary(self):
        """Reads the pipeline source and fails if the gate is ever handed
        anything but the page's own extracted text."""
        from pathlib import Path

        source = (Path(__file__).resolve().parents[2]
                  / "src" / "core" / "scanner.py").read_text(encoding="utf-8")
        call = source[source.index("scope_verdict("):]
        call = call[:call.index(")") + 1]
        assert "extracted.text" in call, call
        for forbidden in ("summary", "description", "analysis.", "cached."):
            assert forbidden not in call, call
