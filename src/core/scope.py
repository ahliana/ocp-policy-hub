"""What counts as in scope: does the document reference a data centre?

The reviewer's rule, stated 2026-08-28: a policy that does not involve a
data centre is not what this tool is looking for. Until now the pipeline
was told the opposite. The screening prompt said in as many words that
district heating and heat network policy counts whether or not data centres
are named, and the LegiScan search terms were tuned to find three thermal
energy network bills that never mention one. Those bills are in the
database and they are what the reviewer rejected.

That disagreement is the reason this lives in one setting rather than in
two places. ``scope.data_center_required`` is read by the gate and by the
prompt builder, so the model and the filter cannot be told different
things again.

Three settings:

``required``
    The reviewer's rule and the default. A document that never references
    a data centre is out of scope.

``adjacent``
    Keep it, but mark it, so a heat network law a data centre might
    connect to next year is still visible without filling the queue.

``off``
    The behaviour as it was before this module existed. Byte identical, so
    the switch has a true no-change position.

Two things this deliberately does not do. It does not ask whether a policy
is *exclusive* to data centres, which is a different and much stricter
question: the EU Energy Efficiency Directive and the German efficiency act
both reference data centres without being only about them, and both are
curated keeps. And it does not match English only, because a Danish bill
does not say "data centre" in English.

**Run this on source text, never on a stored summary.** Measured on the
143 stored records on 2026-08-28: New Jersey A4490 and S684 are thermal
energy network bills that never mention a data centre, and both survive
the rule when it is applied to their stored summaries, because the
analysis model wrote "could incorporate waste heat sources including data
centers" into the summary itself. The model supplies the context the bill
lacks, which is helpful prose and a broken filter input. That is why the
gate in the scan pipeline runs before any model call, and why moving it
later would silently stop it working while still looking wired up.
"""

import logging
import re

logger = logging.getLogger(__name__)

REQUIRED = "required"
ADJACENT = "adjacent"
OFF = "off"
VALID_SETTINGS = (REQUIRED, ADJACENT, OFF)

IN_SCOPE = "in_scope"
ADJACENT_VERDICT = "adjacent"
OUT_OF_SCOPE = "out"

#: The reviewer's rule is the default, per her decision on 2026-08-28.
DEFAULT_SETTING = REQUIRED

#: Data-centre terms by language, taken from the vocabulary the keyword
#: file already uses so the two cannot drift. English spellings cover both
#: the American and British forms and the closed compound, because bills
#: use all three.
DATA_CENTRE_TERMS = [
    # English
    "data center", "data centre", "datacenter", "datacentre",
    "data centers", "data centres",
    # German
    "rechenzentrum", "rechenzentren",
    # Dutch
    "datacentrum", "datacenter",
    # Danish, Norwegian, Swedish
    "datacenter", "datasenter", "datacentre", "datacentraler",
    # French
    "centre de données", "centre de donnees",
    # Spanish, Portuguese, Italian
    "centro de datos", "centro de dados", "centro dati",
    # Finnish
    "konesali", "datakeskus",
    # Czech, Polish
    "datové centrum", "centrum danych", "centrum przetwarzania danych",
    # Japanese, Korean
    "データセンター", "데이터 센터", "데이터센터",
]

# Word-boundary matching for the Latin-script terms so "datacenters" is
# caught but an unrelated substring is not. CJK terms carry no word
# boundaries, so they are matched plainly.
_LATIN = re.compile(r"[a-zÀ-ɏ]")

# What can sit between the words of a multi-word term. Legislative text and
# stripped markup produce all of these for the same phrase: "data centre",
# "data-centre", "data  centre" across a line break, and non-breaking spaces
# out of HTML. Matching only a single ordinary space missed the hyphenated
# form entirely, which under the required scope rule would have dropped a
# real data centre bill without a word. Found by Ahliana asking whether
# spellings were covered, before this ever ran on production.
_SEPARATOR = r"[\s ‐-―-]+"


def _term_pattern(term: str) -> re.Pattern:
    """A matcher for one term, tolerant of how the words are joined.

    Word-boundary guarded for Latin-script terms so "datacenter" matches
    inside "datacenters" but not inside an unrelated word. CJK terms carry
    no word boundaries, so they are matched plainly.
    """
    body = _SEPARATOR.join(re.escape(word) for word in term.split())
    if _LATIN.search(term):
        return re.compile(rf"(?<![a-z0-9]){body}", re.IGNORECASE)
    return re.compile(body)


_PATTERNS = [_term_pattern(term) for term in DATA_CENTRE_TERMS]


def scope_setting(settings: dict | None = None) -> str:
    """The configured scope, defaulting to the reviewer's rule.

    An unrecognised value is not silently treated as off: that would widen
    the scope without anyone asking, which is the failure this module
    exists to prevent. It falls back to the default and says so.
    """
    if not settings:
        return DEFAULT_SETTING
    value = str(settings.get("data_center_required", DEFAULT_SETTING)).strip().lower()
    if value not in VALID_SETTINGS:
        logger.warning(
            "Unknown scope setting %r; falling back to %r. Valid: %s",
            value, DEFAULT_SETTING, ", ".join(VALID_SETTINGS),
        )
        return DEFAULT_SETTING
    return value


def mentions_data_center(text: str) -> bool:
    """Whether the text references a data centre, in any covered language."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in _PATTERNS)


def scope_verdict(text: str, setting: str = DEFAULT_SETTING) -> str:
    """Where a document sits against the configured scope.

    Returns ``in_scope``, ``adjacent`` or ``out``. Only ``out`` stops a
    document; ``adjacent`` is kept and labelled so the reviewer can filter
    it in one action rather than losing it.
    """
    if setting == OFF:
        return IN_SCOPE
    if mentions_data_center(text):
        return IN_SCOPE
    return OUT_OF_SCOPE if setting == REQUIRED else ADJACENT_VERDICT


def screening_scope_line(setting: str = DEFAULT_SETTING) -> str:
    """The sentence the screening prompt carries about data centres.

    Generated rather than written into the prompt, so changing the setting
    changes what the model is told. Before this, the prompt asserted the
    broad reading in capital letters while the reviewer applied the narrow
    one, and nothing reconciled them.
    """
    if setting == REQUIRED:
        return (
            "The page MUST concern data centres. District heating and heat "
            "network policy that never references a data centre is NOT "
            "relevant, however close the subject matter looks."
        )
    if setting == ADJACENT:
        return (
            "Prefer pages concerning data centres. District heating and heat "
            "network policy that does not name one may still be relevant, "
            "but mark it with lower confidence."
        )
    return (
        "District heating and heat network policy counts WHETHER OR NOT data "
        "centres are named. A page need not mention data centres to be relevant."
    )
