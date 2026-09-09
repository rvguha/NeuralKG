"""Exceptions shared by the event-loop-native query runtime."""


class QueryCancelled(RuntimeError):
    pass


class Refused(Exception):
    """An ordinary query cannot be answered; unlike SystemExit this never stops the process."""


class QueryBudgetExceeded(Refused):
    """A bounded query exhausted work, not wall-clock time or client cancellation."""


class AccessDenied(Exception):
    """The caller is not authenticated or authorized for the requested operation.

    Deliberately NOT a Refused. Every backtrack and fallback path in the engine catches
    Refused and moves to the next candidate, so while this subclassed Refused an
    authorization failure was silently converted into an answer from the next-best public
    source -- the exact substitution `extensions.candidate_filter` promises never happens.
    It must reach the caller as a terminal refusal, so it stops at the app boundary only.
    """
