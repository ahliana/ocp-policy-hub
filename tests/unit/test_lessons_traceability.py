"""The documentation cannot drift from the code without a red test.

Three things must point at each other for a lesson to count as recorded: a
human-readable explanation (docs/HOW_IT_WORKS.md or a decision record), a
test that fails if the lesson is undone (named in docs/LESSONS.md), and a
line in the file an AI session reads first (CLAUDE.md). These tests check
the pointers resolve. They read files only; no network, no clock, no data/.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
LESSONS = DOCS / "LESSONS.md"
DECISIONS = DOCS / "decisions"
DECISIONS_INDEX = DECISIONS / "README.md"
HOW_IT_WORKS = DOCS / "HOW_IT_WORKS.md"
CLAUDE_MD = ROOT / "CLAUDE.md"

LESSON_STATUSES = {"mechanized", "open"}
ADR_STATUS_PREFIXES = ("Proposed", "Accepted", "Superseded by ADR-", "Rejected")
NODE_ID = re.compile(r"^(tests/[\w/]+\.py)((?:::\w+)*)$")


def _lessons() -> list[dict]:
    """Each `## PL-NNN` block as a dict of its `- key: value` lines."""
    text = LESSONS.read_text(encoding="utf-8")
    blocks = re.split(r"^## (PL-\d{3})\s*$", text, flags=re.M)
    lessons = []
    for i in range(1, len(blocks), 2):
        body = blocks[i + 1]
        fields = dict(re.findall(r"^- (\w+): (.+)$", body, flags=re.M))
        fields["id"] = blocks[i]
        lessons.append(fields)
    return lessons


def _adr_files() -> list[Path]:
    return sorted(
        p for p in DECISIONS.glob("ADR-*.md") if not p.name.startswith("ADR-0000-")
    )


def _slug(heading: str) -> str:
    """GitHub's anchor for a markdown heading, close enough for our links."""
    heading = heading.strip().lower()
    heading = re.sub(r"[^\w\s-]", "", heading)
    return re.sub(r"\s+", "-", heading.strip())


def _headings(md: Path) -> set[str]:
    return {
        _slug(m.group(1))
        for m in re.finditer(r"^#{1,6}\s+(.+?)\s*$", md.read_text(encoding="utf-8"), flags=re.M)
    }


@pytest.mark.small
class TestLessonsNameRealGuards:
    def test_there_are_lessons(self):
        assert len(_lessons()) >= 6

    def test_every_lesson_has_the_required_fields(self):
        for lesson in _lessons():
            for key in ("title", "status", "guard", "first_seen", "class"):
                assert key in lesson, f"{lesson['id']} is missing '{key}'"
            assert lesson["status"] in LESSON_STATUSES, (
                f"{lesson['id']} has status {lesson['status']!r}; "
                f"use one of {sorted(LESSON_STATUSES)}"
            )

    def test_every_guard_is_a_test_that_exists_or_an_honest_none(self):
        for lesson in _lessons():
            guard = lesson["guard"].strip()
            if guard.startswith("none"):
                assert "(" in guard and guard.endswith(")"), (
                    f"{lesson['id']}: 'none' must say in parentheses what holds it instead"
                )
                continue
            m = NODE_ID.match(guard)
            assert m, f"{lesson['id']}: guard {guard!r} is not a pytest node id"
            path = ROOT / m.group(1)
            assert path.exists(), f"{lesson['id']}: {m.group(1)} does not exist"
            source = path.read_text(encoding="utf-8")
            for name in [n for n in m.group(2).split("::") if n]:
                assert f"def {name}(" in source or f"class {name}" in source, (
                    f"{lesson['id']}: {name} is not defined in {m.group(1)}"
                )

    def test_a_mechanized_lesson_names_a_test_or_a_gate(self):
        for lesson in _lessons():
            if lesson["status"] == "mechanized":
                guard = lesson["guard"]
                assert NODE_ID.match(guard) or "gate" in guard, (
                    f"{lesson['id']} claims to be mechanized but names no test or gate"
                )


@pytest.mark.small
class TestDecisionRecordsAreIndexedAndValid:
    def test_the_index_and_the_directory_agree(self):
        index = DECISIONS_INDEX.read_text(encoding="utf-8")
        listed = set(re.findall(r"\]\((ADR-\d{4}-[\w-]+\.md)\)", index))
        on_disk = {p.name for p in _adr_files()}
        assert listed == on_disk, (
            f"listed but missing: {sorted(listed - on_disk)}; "
            f"on disk but unlisted: {sorted(on_disk - listed)}"
        )

    def test_every_record_has_a_valid_status_line(self):
        for path in _adr_files():
            m = re.search(r"^- Status: (.+)$", path.read_text(encoding="utf-8"), flags=re.M)
            assert m, f"{path.name} has no '- Status:' line"
            assert m.group(1).startswith(ADR_STATUS_PREFIXES), (
                f"{path.name}: status {m.group(1)!r} must start with one of {ADR_STATUS_PREFIXES}"
            )

    def test_numbers_are_unique_and_the_template_is_not_a_record(self):
        numbers = [p.name[:8] for p in _adr_files()]
        assert len(numbers) == len(set(numbers)), numbers
        assert "ADR-0000" not in numbers


@pytest.mark.small
class TestTestsCiteRecordsThatExist:
    def test_every_adr_cited_from_a_test_exists(self):
        existing = {p.name[:8] for p in _adr_files()}
        for path in (ROOT / "tests").rglob("*.py"):
            if path == Path(__file__):
                continue
            for number in set(re.findall(r"ADR-(\d{4})", path.read_text(encoding="utf-8"))):
                assert f"ADR-{number}" in existing, (
                    f"{path.relative_to(ROOT)} cites ADR-{number}, which has no record"
                )

    def test_every_lesson_cited_from_a_test_exists(self):
        existing = {lesson["id"] for lesson in _lessons()}
        for path in (ROOT / "tests").rglob("*.py"):
            if path == Path(__file__):
                continue
            for number in set(re.findall(r"PL-(\d{3})", path.read_text(encoding="utf-8"))):
                assert f"PL-{number}" in existing, (
                    f"{path.relative_to(ROOT)} cites PL-{number}, which is not in LESSONS.md"
                )


@pytest.mark.small
class TestLinksResolve:
    def _pages(self):
        return [HOW_IT_WORKS, LESSONS, DOCS / "CHANGELOG.md", DECISIONS_INDEX, *_adr_files()]

    def test_every_relative_link_points_at_a_file(self):
        for page in self._pages():
            for target in re.findall(r"\]\(([^)\s]+)\)", page.read_text(encoding="utf-8")):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                file_part, _, anchor = target.partition("#")
                resolved = (page.parent / file_part).resolve() if file_part else page
                assert resolved.exists(), f"{page.name} links to {target}, which does not exist"
                if anchor and resolved.suffix == ".md":
                    assert anchor in _headings(resolved), (
                        f"{page.name} links to {target}; no heading slugs to #{anchor}"
                    )


@pytest.mark.small
class TestTheAiEntryPointPointsHere:
    def test_claude_md_names_the_three_pages(self):
        text = CLAUDE_MD.read_text(encoding="utf-8")
        for needle in ("docs/HOW_IT_WORKS.md", "docs/LESSONS.md", "docs/decisions/"):
            assert needle in text, f"CLAUDE.md does not point at {needle}"

    def test_how_it_works_explains_how_it_stays_true(self):
        text = HOW_IT_WORKS.read_text(encoding="utf-8")
        assert "test_lessons_traceability" in text
