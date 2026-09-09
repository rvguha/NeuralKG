"""Instance extensions: connector, auth and planning code an instance brings with it.

`instance.yaml` says what a deployment answers over. This says what a deployment can *do* that
the core engine does not ship. The two together are what makes "one repository, many instances"
possible: a fork exists because there was nowhere to put code, and every fork then diverges on
everything else too.

An extension is an ordinary importable module with a `setup(registry)` function:

    # atlas_ext/executors.py
    def setup(registry):
        @registry.executor("bigquery_guarded")
        async def run(frame, *, context):
            ...

    # instance.yaml
    extensions:
      - atlas_ext.executors
      - atlas_ext.access

Nothing is registered unless an instance asks for it, so the default deployment behaves exactly
as it did before this file existed. Extensions load once, at first use, in declared order.

Four kinds of thing can be registered today. They were chosen by looking at what an actual
downstream fork had to replace, rather than by guessing at generality:

  accessor            how a source is fetched. A guarded BigQuery runner, a multi-step composite,
                      an API accessor. Selected by the OKF document's `accessor:` field, and
                      reached from BOTH dispatch paths -- the template/DAG reader and the scalar
                      fetch -- so one registration is all a plugin author writes. This is the
                      documented seam; `executor` and `template_reader` below predate it.
  executor            LEGACY, scalar path only. An `accessor` is preferred: an `executor` is
                      invisible to the template path, which is what production runs, so a
                      registration that looks correct silently never fires.
  template_reader     LEGACY, DAG path only. Same asymmetry, the other way round.
  principal           who is asking. Returns an opaque object the other hooks receive; core has
                      no notion of a user, and an instance with authentication needs one.
  candidate_filter    which discovered sources this principal may be OFFERED. Runs before the
                      planner, so a filtered source cannot be routed to at all.
  coidentify_strategy how a name becomes an identifier for a kind of thing (see instance.py).
                      `hub` and `native` are built in; `resolver`, `positional` and `structural`
                      are named but unimplemented, and are exactly what an instance would add.
"""
import importlib
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Read:
    """What an accessor is asked for. Carries everything BOTH dispatch paths can supply, so a
    plugin never has to know which one invoked it.

    `frame` is the scalar path's `_F` and is None on the DAG path; `node`/`dependencies` are the
    DAG path's and are None/() on the scalar path. Everything above them -- descriptor, source,
    operation, parameters -- is present either way, and a plugin that reads only those works on
    both. A plugin needing `frame` or `node` is declaring itself path-specific; that is allowed,
    but it must say so by checking rather than by crashing.
    """
    descriptor: dict                 # the OKF frontmatter, as delivered (ARD or disk)
    source: str                      # the resource identifier, treated as OPAQUE
    operation: str | None = None     # declared operation being invoked, when the caller knows
    parameters: dict = field(default_factory=dict)
    dependencies: tuple = ()         # resolved upstream results, DAG path only
    node: dict | None = None
    frame: Any = None


class Registry:
    """What an extension's `setup()` is handed."""

    def __init__(self):
        self.accessors = {}
        self.executors = {}
        self.template_readers = {}
        self.candidate_filters = []
        self.coidentify_strategies = {}
        self._principal = None

    def accessor(self, name):
        """Register `async fn(read, *, context) -> answer_synthesizer.Input` under a name an OKF
        document declares as `accessor:`.

        Return the COMPLETE payload in `Input.data` -- never a scalar projection, because the
        scalar path narrows it and the DAG path does not, and a plugin cannot know which ran.
        Set `complete`, `provenance`, `grain`, `units` and `period_basis` from what the source
        actually reported; they are what downstream checks and citations are built from.

        Raise runtime.Refused to reject this source and let the engine backtrack to the next
        candidate; anything else propagates as a real error. Honour `context` for cancellation
        and accounting -- an accessor that makes its own model or data calls must make them
        through the shared services on `context`, or its usage is invisible to the ledger.
        """
        def register(fn):
            if name in self.accessors:
                raise ValueError(f"accessor {name!r} is already registered")
            self.accessors[name] = fn
            return fn
        return register

    def executor(self, name):
        """Register `async fn(frame, *, context)` under a name an OKF document can declare.

        The frame carries (fm, ident, key, period, attribute, mention, state, ctx). Raise
        runtime.Refused to reject this source and let the engine backtrack to the next candidate;
        anything else propagates as a real error.
        """
        def register(fn):
            if name in self.executors:
                raise ValueError(f"executor {name!r} is already registered")
            self.executors[name] = fn
            return fn
        return register

    def template_reader(self, name):
        """Register async fn(node, parameters, dependencies, *, source, context).

        Return answer_synthesizer.Input with actual coverage, identity, units and
        provenance. Selected by the descriptor's template_reader, never by the LLM.
        Dependent reads must use dependency data (resolved dates, scopes, actors).
        """
        def register(fn):
            if name in self.template_readers:
                raise ValueError(f'template reader {name!r} is already registered')
            self.template_readers[name] = fn
            return fn
        return register

    def principal(self, fn):
        """Register `fn(request) -> principal|None`. At most one; last registration wins is a
        silent-conflict bug waiting to happen, so a second one is an error."""
        if self._principal is not None:
            raise ValueError("a principal provider is already registered")
        self._principal = fn
        return fn

    def candidate_filter(self, fn):
        """Register `fn(candidates, principal) -> (visible, withheld)`.

        Filters run before planning, deliberately. A source the planner never sees cannot be
        routed to, which is a much stronger guarantee than checking at fetch time -- and it lets
        the engine REFUSE rather than quietly answering from the next-best source, which would
        substitute a different thing for the one that was asked about.
        """
        self.candidate_filters.append(fn)
        return fn

    def coidentify_strategy(self, name):
        """Register `async fn(mention, item_type, *, context) -> entity|None` for a strategy
        named in a domain's `coidentify`."""
        def register(fn):
            self.coidentify_strategies[name] = fn
            return fn
        return register


_REGISTRY = None
_LOCK = threading.Lock()


def registry():
    """The loaded registry, loading the instance's extensions on first use."""
    global _REGISTRY
    if _REGISTRY is None:
        with _LOCK:
            if _REGISTRY is None:
                _REGISTRY = _load()
    return _REGISTRY


def _load():
    import instance
    reg = Registry()
    for name in instance.extensions():
        module = importlib.import_module(name)
        setup = getattr(module, "setup", None)
        if not callable(setup):
            # Loudly: a module listed as an extension that registers nothing is almost certainly
            # a typo or a half-finished port, and silently ignoring it means the instance runs
            # with core behaviour while appearing to be extended.
            raise TypeError(f"extension {name!r} has no callable setup(registry)")
        setup(reg)
    return reg


def reset():
    """Drop the loaded registry. For tests, and for a process that swaps instances."""
    global _REGISTRY
    with _LOCK:
        _REGISTRY = None


def executor(name):
    """The registered executor called `name`, or None."""
    return registry().executors.get(name) if name else None


def accessor(name):
    """The registered accessor called `name`, or None."""
    return registry().accessors.get(name) if name else None


def accessor_for(descriptor):
    """(name, handler) for a descriptor's declared accessor, or (None, None) if it declares none.

    Raises runtime.Refused when a descriptor names an accessor no loaded extension registers --
    a descriptor may SELECT an installed name, never supply one, and a missing one must fail
    loudly rather than fall through to a built-in that happens to match another marker.
    """
    name = (descriptor or {}).get("accessor")
    if not name:
        return None, None
    handler = registry().accessors.get(name)
    if handler is None:
        import runtime
        loaded = ", ".join(sorted(registry().accessors)) or "none"
        raise runtime.Refused(f"this source declares accessor {name!r}, which no loaded "
                              f"extension registers (loaded: {loaded})")
    return name, handler


async def invoke_accessor(read, *, context):
    """The single validated invocation boundary for all accessor callers."""
    import runtime
    from answer_synthesizer import Input
    name, handler = accessor_for(read.descriptor)
    if handler is None:
        raise runtime.Refused('No accessor declared')
    operations = (read.descriptor.get('access') or {}).get('operations') or {}
    if operations:
        selected = read.operation or read.descriptor.get('default_operation')
        if selected is None and len(operations) == 1:
            selected = next(iter(operations))
        if selected not in operations:
            raise runtime.Refused('Missing or undeclared accessor operation: ' + str(selected))
        read.operation = selected
    context.check()
    result = await context.wait(handler(read, context=context))
    if not isinstance(result, Input):
        raise runtime.Refused(f'accessor {name!r} returned {type(result).__name__}, not an answer_synthesizer.Input')
    context.check()
    return result


def scalar_payload(result):
    """Retain the full source payload AND its independently reported evidence envelope."""
    from dataclasses import asdict
    payload = dict(result.data) if isinstance(result.data, dict) else {'results': result.data}
    payload['_accessor_evidence'] = asdict(result)
    payload['complete'] = result.complete
    payload['grain'] = result.grain
    if result.period_basis:
        payload['period_basis'] = result.period_basis
    if result.units:
        payload['units'] = result.units
        if 'value' in result.units:
            payload['unit'] = result.units['value']
    return payload


def split_candidates(candidates, principal):
    """Apply every registered filter in order. With none registered, everything is visible."""
    visible, withheld = list(candidates), []
    for fn in registry().candidate_filters:
        visible, hidden = fn(visible, principal)
        withheld.extend(hidden or [])
    return visible, withheld
