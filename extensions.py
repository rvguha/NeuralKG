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

  executor            how a source is fetched. A guarded BigQuery runner, a multi-step composite,
                      an API accessor. Selected by the OKF document's `executor:` field.
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


class Registry:
    """What an extension's `setup()` is handed."""

    def __init__(self):
        self.executors = {}
        self.candidate_filters = []
        self.coidentify_strategies = {}
        self._principal = None

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


def split_candidates(candidates, principal):
    """Apply every registered filter in order. With none registered, everything is visible."""
    visible, withheld = list(candidates), []
    for fn in registry().candidate_filters:
        visible, hidden = fn(visible, principal)
        withheld.extend(hidden or [])
    return visible, withheld
