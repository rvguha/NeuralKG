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
import inspect
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
        self.authorizers = []
        self.budget_hooks = []

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

    def authorizer(self, fn):
        """Register async fn(descriptor, operation, *, context).

        This is defence in depth after discovery filtering. It runs before every plugin or
        built-in source read, including child plans and retries. Raise runtime.AccessDenied.
        """
        self.authorizers.append(fn)
        return fn

    def budget(self, fn):
        """Register async fn(phase, *, context, outcome=None), where phase is start or finish.

        The hook owns persistence/reservation. The engine guarantees finish for every started
        query and shares the same context/ledgers with child operations.
        """
        self.budget_hooks.append(fn)
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
    name = accessor_name(descriptor)
    if not name:
        return None, None
    handler = registry().accessors.get(name)
    if handler is None:
        import runtime
        loaded = ", ".join(sorted(registry().accessors)) or "none"
        raise runtime.Refused(f"this source declares accessor {name!r}, which no loaded "
                              f"extension registers (loaded: {loaded})")
    return name, handler


def accessor_name(descriptor):
    """The installed accessor a self-contained OKF document selects.

    NeuralKG-authored descriptors use the compact top-level ``accessor`` field. Atlas's older
    OKF dialect spells the same contract ``computation.runtime.executor``. Both are catalog data,
    not alternate execution paths: they resolve to the same registered accessor here.
    """
    descriptor = descriptor or {}
    direct = descriptor.get("accessor")
    if direct:
        return direct
    computation = descriptor.get("computation") or {}
    runtime = computation.get("runtime") if isinstance(computation, dict) else {}
    return runtime.get("executor") if isinstance(runtime, dict) else None


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
    await authorize(read.descriptor, read.operation, context=context)
    context.check()
    result = await context.wait(handler(read, context=context))
    if not isinstance(result, Input):
        raise runtime.Refused(f'accessor {name!r} returned {type(result).__name__}, not an answer_synthesizer.Input')
    context.check()
    return result


async def resolve_principal(request):
    """Resolve the trusted caller using the one configured provider; None means public."""
    fn = registry()._principal
    if fn is None:
        return None
    value = fn(request)
    return await value if inspect.isawaitable(value) else value


async def authorize(descriptor, operation, *, context):
    for fn in registry().authorizers:
        value = fn(descriptor, operation, context=context)
        if inspect.isawaitable(value):
            await value


async def budget_event(phase, *, context, outcome=None):
    for fn in registry().budget_hooks:
        value = fn(phase, context=context, outcome=outcome)
        if inspect.isawaitable(value):
            await value


def filter_candidates(candidates, *, context):
    visible, withheld = split_candidates(candidates, context.principal)
    context.memo.setdefault('withheld_resources', []).extend(withheld)
    if withheld:
        def score(item):
            try: return float(item.get('score') or 0)
            except (TypeError, ValueError): return 0.0
        best_hidden = max(withheld, key=score)
        if not visible or score(best_hidden) >= max(map(score, visible)):
            need = best_hidden.get('entitlement') or best_hidden.get('needs') or 'additional access'
            raise __import__('runtime').AccessDenied(
                f"the best matching source is restricted and requires {need}; no public source was substituted")
    return visible


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


def scalar_input(result, preferred=None):
    """Project one accessor row to a scalar while retaining the complete payload as evidence.

    DAG arithmetic needs a number; the final renderer needs the source's complete JSON. This
    adapter provides both and refuses whenever the scalar is not structurally unambiguous.
    """
    from dataclasses import replace
    original = result.data
    if isinstance(original, (str, int, float, bool)):
        return result
    rows = original.get('rows') if isinstance(original, dict) else original
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        # A registered accessor may deliberately expose a full relation even when the logical
        # node says ReadScalar (older plugins did). Preserve that contract; downstream arithmetic
        # will refuse if the chosen template truly requires a scalar.
        return result
    row = rows[0]
    names = []
    if isinstance(preferred, str):
        folded = preferred.casefold().replace(' ', '_')
        names.extend(k for k in row if k.casefold().replace(' ', '_') == folded)
    names.extend(k for k in ('value', 'ratio_pct', 'amount', 'count', 'total') if k in row)
    names.extend(k for k in result.units if k in row)
    names = list(dict.fromkeys(names))
    if len(names) != 1 or not isinstance(row[names[0]], (str, int, float, bool)):
        return result
    field = names[0]
    provenance = {**result.provenance, 'payload': original, 'scalar_field': field,
                  'scalar_row': row}
    return replace(result, data=row[field], provenance=provenance,
                   units={'value': result.units.get(field)} if field in result.units else result.units)


def split_candidates(candidates, principal):
    """Apply every registered filter in order. With none registered, everything is visible."""
    visible, withheld = list(candidates), []
    for fn in registry().candidate_filters:
        visible, hidden = fn(visible, principal)
        withheld.extend(hidden or [])
    return visible, withheld
