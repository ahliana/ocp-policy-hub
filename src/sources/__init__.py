"""Structured policy sources and their registry.

Client modules self-register on import; add new imports at the bottom.
"""

from .base import PolicySource, SourceError

SOURCE_REGISTRY: dict[str, type[PolicySource]] = {}


def register_source(cls: type[PolicySource]) -> type[PolicySource]:
    """Register a PolicySource class under its id (usable as a decorator)."""
    if not cls.id:
        raise ValueError(f"{cls.__name__} has no id")
    SOURCE_REGISTRY[cls.id] = cls
    return cls


def get_source(source_id: str) -> PolicySource:
    """Instantiate the source registered under source_id.

    Raises:
        KeyError: unknown source id (surfaces as a failed domain with a
            clear message rather than a silent skip).
    """
    if source_id not in SOURCE_REGISTRY:
        raise KeyError(
            f"Unknown source_type '{source_id}'. "
            f"Known: {sorted(SOURCE_REGISTRY)} (plus 'crawl')"
        )
    return SOURCE_REGISTRY[source_id]()


__all__ = [
    "PolicySource",
    "SourceError",
    "SOURCE_REGISTRY",
    "register_source",
    "get_source",
]

from . import riksdagen  # noqa: E402, F401
from . import uk_bills  # noqa: E402, F401
from . import legisinfo  # noqa: E402, F401
from . import folketing  # noqa: E402, F401
from . import eurlex_nim  # noqa: E402, F401
from . import oireachtas  # noqa: E402, F401
from . import govuk  # noqa: E402, F401
from . import eu_have_your_say  # noqa: E402, F401
from . import tweede_kamer  # noqa: E402, F401
from . import egov_japan  # noqa: E402, F401
from . import kokkai  # noqa: E402, F401
from . import stortinget  # noqa: E402, F401
from . import diavgeia  # noqa: E402, F401
from . import riigikogu  # noqa: E402, F401
from . import sejm  # noqa: E402, F401
from . import pmg  # noqa: E402, F401
from . import camara  # noqa: E402, F401
from . import nz_pco  # noqa: E402, F401
from . import ris_austria  # noqa: E402, F401

# Client modules self-register via @register_source on import.
from . import legiscan  # noqa: E402,F401
from . import govinfo  # noqa: E402,F401
from . import regulations_gov  # noqa: E402,F401
from . import dip_bundestag  # noqa: E402,F401
from . import va_lis  # noqa: E402,F401
