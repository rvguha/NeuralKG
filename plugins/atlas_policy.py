"""Optional Atlas Firebase identity, entitlement and Firestore budget plugin.

All cloud imports are lazy. Instances that do not select this extension need none
of its dependencies. Configuration contains policy and secret references, never
tokens. The in-process plugin is trusted administrator-installed code.
"""
import asyncio
import datetime
import os

import instance
import runtime

_DB = None


def configuration():
    return instance.config().get('plugin_config', {}).get('atlas_policy', {})


def setup(registry):
    registry.principal(authenticate)
    registry.candidate_filter(filter_candidates)
    registry.authorizer(authorize)
    registry.budget(budget)


def _metadata(value):
    if isinstance(value, dict) and isinstance(value.get('metadata'), dict):
        return value['metadata']
    return value or {}


def _field(meta, name, default=None):
    return meta.get('okf:' + name, meta.get(name, default))


def _access(meta):
    return _field(meta, 'access', {}) or {}


def requirement(value):
    meta = _metadata(value)
    import extensions
    if extensions.is_public(meta):
        return None
    need = _field(meta, 'entitlement') or _access(meta).get('entitlement')
    if not isinstance(need, str) or not need.strip():
        raise runtime.AccessDenied('private resource has no configured entitlement')
    return need.strip()


def filter_candidates(candidates, principal):
    entitlements = set((principal or {}).get('entitlements') or ())
    visible, withheld = [], []
    for candidate in candidates:
        try:
            need = requirement(candidate)
        except runtime.AccessDenied:
            need = 'misconfigured-private-resource'
        if need and need not in entitlements:
            withheld.append({'identifier': candidate.get('identifier'), 'title': candidate.get('title'),
                             'score': candidate.get('score'), 'entitlement': need})
        else:
            visible.append(candidate)
    return visible, withheld


async def authorize(descriptor, operation, *, context):
    need = requirement(descriptor)
    if need and need not in set((context.principal or {}).get('entitlements') or ()):
        raise runtime.AccessDenied('source requires entitlement: ' + need)


def _db():
    global _DB
    if _DB is None:
        try:
            import firebase_admin
            from firebase_admin import firestore
        except ImportError as exc:
            raise runtime.AccessDenied('Atlas authentication plugin dependencies are not installed') from exc
        try:
            firebase_admin.get_app()
        except ValueError:
            firebase_admin.initialize_app()
        _DB = firestore.client()
    return _DB


def _verify_sync(token):
    try:
        from firebase_admin import auth
        return auth.verify_id_token(token)
    except Exception as exc:
        raise runtime.AccessDenied('invalid or expired bearer token') from exc


def _load_user_sync(decoded):
    from firebase_admin import firestore
    cfg = configuration()
    uid, email = decoded.get('uid'), str(decoded.get('email') or '')
    if not uid: raise runtime.AccessDenied('verified token has no uid')
    preapproved = email.casefold() in {str(e).casefold() for e in cfg.get('preapproved_emails', [])}
    ref = _db().collection(cfg.get('users_collection', 'users')).document(uid)
    snap = ref.get()
    if not snap.exists:
        status = 'approved' if preapproved else 'pending'
        ref.set({'uid': uid, 'email': email, 'display_name': decoded.get('name', ''),
                 'status': status, 'entitlements': [], 'created_at': firestore.SERVER_TIMESTAMP})
        if status != 'approved': raise runtime.AccessDenied('account created; waiting for approval')
        data = {}
    else:
        data = snap.to_dict() or {}
        status = data.get('status')
        if status != 'approved' and preapproved:
            ref.set({'status': 'approved'}, merge=True); status = 'approved'
        if status != 'approved': raise runtime.AccessDenied('account is not approved')
    return {'uid': uid, 'email': email, 'display_name': decoded.get('name', ''),
            'entitlements': sorted({str(x) for x in data.get('entitlements', []) if x})}


async def authenticate(request):
    header = request.headers.get('authorization', '')
    if not header.lower().startswith('bearer '):
        raise runtime.AccessDenied('missing bearer token')
    token = header.split(' ', 1)[1].strip()
    decoded = await asyncio.to_thread(_verify_sync, token)
    return await asyncio.to_thread(_load_user_sync, decoded)


def _month():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m')


def _reserve_sync(uid, amount, ceiling):
    from firebase_admin import firestore
    cfg = configuration(); ref = _db().collection(cfg.get('usage_collection', 'usage')).document(uid + ':' + _month())
    transaction = _db().transaction()

    @firestore.transactional
    def update(tx):
        snap = ref.get(transaction=tx); data = snap.to_dict() if snap.exists else {}
        spent, reserved = float(data.get('cost_usd', 0)), float(data.get('reserved_usd', 0))
        if spent + reserved + amount > ceiling:
            raise runtime.QueryBudgetExceeded('monthly usage budget exceeded')
        tx.set(ref, {'uid': uid, 'month': _month(), 'reserved_usd': reserved + amount,
                     'cost_usd': spent}, merge=True)
    update(transaction)


def _reconcile_sync(uid, reserved_amount, actual):
    from firebase_admin import firestore
    cfg = configuration(); ref = _db().collection(cfg.get('usage_collection', 'usage')).document(uid + ':' + _month())
    transaction = _db().transaction()

    @firestore.transactional
    def update(tx):
        snap = ref.get(transaction=tx); data = snap.to_dict() if snap.exists else {}
        reserved = max(0, float(data.get('reserved_usd', 0)) - reserved_amount)
        spent = float(data.get('cost_usd', 0)) + max(0, actual)
        tx.set(ref, {'reserved_usd': reserved, 'cost_usd': spent, 'updated_at': firestore.SERVER_TIMESTAMP}, merge=True)
    update(transaction)


def actual_cost(context):
    usage = context.usage_ledger.snapshot() if context.usage_ledger is not None else {}
    cost = float(usage.get('cost_usd') or 0)
    price = float(configuration().get('bigquery_usd_per_tib', 6.25))
    billed = 0
    for event in context.operation_events:
        stats = event.get('statistics') or {}
        try: billed += int(stats.get('totalBytesBilled') or stats.get('total_bytes_billed') or 0)
        except (TypeError, ValueError): pass
    return cost + billed / 1024**4 * price


async def budget(phase, *, context, outcome=None):
    cfg = configuration()
    if not cfg.get('monthly_budget_usd'): return
    principal = context.principal or {}
    uid = principal.get('uid')
    if not uid: raise runtime.AccessDenied('budgeted instance requires an authenticated principal')
    key = '_atlas_budget_reservation'
    if phase == 'start':
        amount = float(cfg.get('reservation_usd', 1.0))
        if amount <= 0: raise runtime.QueryBudgetExceeded('budget reservation must be positive')
        await asyncio.to_thread(_reserve_sync, uid, amount, float(cfg['monthly_budget_usd']))
        context.memo[key] = amount
    elif phase == 'finish' and key in context.memo:
        amount = context.memo.pop(key)
        await asyncio.to_thread(_reconcile_sync, uid, amount, actual_cost(context))
