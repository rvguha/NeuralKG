"""What THIS deployment answers over.

The engine is corpus-independent by construction: nothing in the query path names a source
(`tests/test_query_understanding_isolation.py` enforces that). But an engine still has to be
pointed somewhere, and a few things genuinely do vary per deployment -- which ARD to ask, what
kinds of thing that ARD describes, what to call the site, and which questions to offer as
examples. Those lived as module constants in `harness.py` and `derive.py`, which meant standing
up a second instance over a different corpus required editing the code.

They live here instead, in `instance.yaml`. The file is data about a deployment, not about the
engine; two instances over different ARDs differ only in this file.

Every accessor falls back to a value that reproduces the previous hard-coded behaviour, so a
missing or partial file is not an error -- an instance that only wants to change `ard.finder_url`
writes three lines and inherits the rest.
"""
import os
import functools

try:
    import yaml
except ImportError:                                   # pyyaml is a hard dependency; be explicit
    yaml = None

ROOT = os.path.dirname(os.path.abspath(__file__))


def path():
    """Resolved per call, not at import: a process that sets INSTANCE_CONFIG after importing
    something that imports this module should still get the instance it asked for."""
    return os.getenv("INSTANCE_CONFIG") or os.path.join(ROOT, "instance.yaml")

# Identifier vocabulary is the one part of this file the QUERY PATH reads. `derive.py` uses it to
# tell an operation parameter that selects an entity by identity (cik=, ein=, fips_place=) from
# one that searches by name (q=, org=). An ARD over the WHO Global Health Observatory would say
# SpatialDim and IndicatorCode here, with IndicatorName among the name selectors; nothing else
# about the engine would change.
_FALLBACK_IDENTIFIERS = ("cik", "ein", "qid", "gnis", "lei", "geo", "fips*")
_FALLBACK_NAME_SELECTORS = ("q", "org", "awardee", "awardeename", "name", "org_names",
                            "recipient_search_text", "place")


@functools.lru_cache(maxsize=None)
def _read(target):
    if not (yaml and os.path.isfile(target)):
        return {}
    with open(target, encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def config(target=None):
    """The parsed instance file, or {} when there is none. Cached per path."""
    return _read(target or path())


def reload():
    """Drop the cache. For tests, and for a process that swaps instances at runtime."""
    _read.cache_clear()


def configured():
    """True when this deployment has an instance file at all.

    The distinction matters for presentational lists. `or <legacy literal>` looked like a safe
    fallback and was not: an instance over the WHO Global Health Observatory, which correctly
    declares no example questions of its own, inherited a homepage full of US nonprofit
    questions. A file that exists governs, including where it is silent."""
    return bool(config())


def _section(name):
    value = config().get(name)
    return value if isinstance(value, dict) else {}


def identity():
    """Display identity. `name` is substituted into the served pages."""
    out = {"name": "Neural KG", "tagline": "OKF + ARD", "description": ""}
    out.update({k: v for k, v in _section("identity").items() if v})
    return out


def finder_url():
    """Where the ARD Agent Finder is. AGENT_FINDER_URL still wins: a container is configured by
    environment, and a file baked into an image must not override what the platform sets."""
    return (os.getenv("AGENT_FINDER_URL")
            or _section("ard").get("finder_url")
            or "http://127.0.0.1:8088").rstrip("/")


def domains():
    """The kinds of thing this ARD describes: [{name, identifiers, describes}].

    Declarative, and deliberately open -- `type` from query understanding is a free noun phrase
    and is never checked against this list. Constraining it here would re-create the closed
    vocabulary that was removed for being duplicated between prompt and code.
    """
    listed = config().get("domains")
    return listed if isinstance(listed, list) else []


def identifiers():
    """Parameter names that select an entity by identity. A trailing `*` matches by prefix."""
    named = tuple(str(i) for d in domains() for i in (d.get("identifiers") or []))
    return named or _FALLBACK_IDENTIFIERS


def name_selectors():
    """Parameter names that search by name rather than select by identity."""
    listed = config().get("name_selectors")
    return tuple(listed) if isinstance(listed, list) and listed else _FALLBACK_NAME_SELECTORS


def is_identifier(param):
    """True when `param` selects by identity. Handles the `fips*` prefix form.

    Case-insensitive on BOTH sides. The parameter was folded and the configured entry was not,
    which worked only because every identifier in the original vocabulary happened to be
    lowercase (cik, ein, fips_place). Real APIs are not so obliging -- the WHO Global Health
    Observatory selects on `SpatialDim` and `IndicatorCode` -- and the mismatch failed silently,
    classifying an identity parameter as a name search.
    """
    name = (param or "").casefold()
    for entry in identifiers():
        entry = str(entry).casefold()
        if entry.endswith("*"):
            if name.startswith(entry[:-1]):
                return True
        elif name == entry:
            return True
    return False


def examples():
    """Homepage example tabs: [{label, dirs, queries}]. Empty is fine -- the section hides."""
    listed = config().get("examples")
    return listed if isinstance(listed, list) else []


def source_examples():
    """dir -> [question], shown on the sources page."""
    return _section("source_examples")


def source_order():
    """Display order for the sources page. Anything unlisted sorts last."""
    listed = config().get("source_order")
    return list(listed) if isinstance(listed, list) else []
