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
# one that searches by name (q=, org=). An ARD over the NASA Exoplanet Archive would say pl_name
# and hostname here; nothing else about the engine would change.
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
    fallback and was not: an instance over the NASA Exoplanet Archive, which correctly declares
    no example questions of its own, inherited a homepage full of US nonprofit questions. A
    file that exists governs, including where it is silent."""
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


# Co-identification strategies. Which one applies is a property of the KIND OF THING, not of the
# deployment: an instance may hold planets whose catalogue designations are already identifiers
# and stars that must be resolved by name to coordinates. Declared per domain as `coidentify`.
#
#   hub         resolve the name to a hub identifier that CARRIES per-source keys, and read them
#               off it. Wikidata: QID -> CIK, EIN, FIPS. Works where someone curates the
#               crosswalk, which in practice means organizations, places and people.
#   native      the source's own designation IS the identifier. Nothing to resolve; the name from
#               the question is the key. NASA Exoplanet Archive ("Kepler-22 b"), College
#               Scorecard (a school name).
#   resolver    a type-specific naming authority maps the name to an id or a position, but
#               carries no per-source keys. SIMBAD for astronomical objects. NOT IMPLEMENTED.
#   positional  no shared identifier at all; the join is geometric, within a radius. SIMBAD to
#               Gaia or SDSS. Needs a cone-search binding the engine does not have. NOT
#               IMPLEMENTED.
#   structural  the identifier is COMPUTED from the object rather than looked up -- an InChIKey
#               is a hash of a molecular structure, so two sources agree without a registry.
#               NOT IMPLEMENTED.
STRATEGIES = ("hub", "native", "resolver", "positional", "structural")
IMPLEMENTED_STRATEGIES = ("hub", "native")


def coidentify(item_type=None):
    """The co-identification strategy for an item of `item_type`.

    `item_type` is the open noun phrase query understanding returned ("company", "a US city",
    "planet"). It is matched against declared domain names leniently and ONLY here -- never in a
    prompt, and a miss is not an error. That matters: `type` stays a free noun phrase precisely
    so the model is not constrained by a vocabulary, and a domain that fails to match simply
    falls back to the instance default rather than failing the query.
    """
    phrase = (item_type or "").strip().casefold()
    if phrase:
        for domain in domains():
            name = str(domain.get("name") or "").casefold()
            declared = str(domain.get("coidentify") or "").strip().casefold()
            if name and declared and (name == phrase or name in phrase or phrase in name):
                return declared
    return crosswalk()


def crosswalk():
    """The instance-wide DEFAULT strategy, for item types no domain claims.

    Kept as `ard.crosswalk` for backward compatibility and because most deployments have one
    answer. "none" is accepted as a synonym for "native": both mean nothing is resolved.

    Resolution is corpus-shaped, not engine-shaped. Wikidata is a hub that CARRIES per-source
    keys (QID -> CIK, EIN, FIPS), which fits US organizations and places because Wikidata curates
    exactly those. It is the wrong model for a corpus whose own designations are the identifiers
    -- the NASA Exoplanet Archive keys on "Kepler-22 b" -- and for one where the join is
    positional rather than by key, as SIMBAD-to-Gaia is. Only "wikidata" and "none" exist today;
    the point of the setting is that the assumption is written down rather than compiled in.
    """
    declared = str(_section("ard").get("crosswalk") or "hub").strip().lower()
    return {"none": "native", "wikidata": "hub"}.get(declared, declared)


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
    lowercase (cik, ein, fips_place). Real APIs are not so obliging -- many spell parameters in
    camelCase -- and the mismatch failed silently, classifying an identity parameter as a name
    search.
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
