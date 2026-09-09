import unittest
from unittest.mock import AsyncMock, patch

import httpx

import app
import extensions
import runtime
from plugins import atlas_policy as policy
from query_context import QueryContext


class Request:
    def __init__(self, authorization='Bearer token'):
        self.headers = {'authorization': authorization}


class PolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_authentication_returns_backend_entitlements(self):
        with patch.object(policy, '_verify_sync', return_value={'uid': 'u', 'email': 'x@example.org'}), \
             patch.object(policy, '_load_user_sync', return_value={'uid': 'u', 'entitlements': ['private.read']}):
            principal = await policy.authenticate(Request())
        self.assertEqual(principal['uid'], 'u')
        self.assertEqual(principal['entitlements'], ['private.read'])
        with self.assertRaises(runtime.AccessDenied): await policy.authenticate(Request(''))

    async def test_private_missing_policy_fails_closed(self):
        for descriptor in ({'visibility': 'private'},
                           {'visibility': 'private', 'access': {'entitlement': 'private.read'}}):
            context = QueryContext(principal={'entitlements': []})
            with self.assertRaises(runtime.AccessDenied):
                await policy.authorize(descriptor, 'read', context=context)
        await policy.authorize({'visibility': 'private', 'access': {'entitlement': 'private.read'}},
                               'read', context=QueryContext(principal={'entitlements': ['private.read']}))

    def test_filter_hides_payload_and_never_substitutes_best_private(self):
        candidates = [
            {'identifier': 'private', 'title': 'Private', 'score': .9,
             'metadata': {'visibility': 'private', 'access': {'entitlement': 'private.read'}, 'sql': 'secret'}},
            {'identifier': 'public', 'title': 'Public', 'score': .8, 'metadata': {}}]
        visible, hidden = policy.filter_candidates(candidates, {'entitlements': []})
        self.assertEqual([x['identifier'] for x in visible], ['public'])
        self.assertNotIn('metadata', hidden[0]); self.assertNotIn('sql', hidden[0])
        reg = extensions.Registry(); reg.candidate_filter(policy.filter_candidates)
        with patch.object(extensions, 'registry', return_value=reg):
            context = QueryContext(principal={'entitlements': []})
            with self.assertRaises(runtime.AccessDenied):
                extensions.filter_candidates(candidates, context=context)
            self.assertNotIn('metadata', context.memo['withheld_resources'][0])

    async def test_budget_reservation_always_reconciles_actual_attempts(self):
        context = QueryContext(principal={'uid': 'u'})
        context.operation_events = [{'statistics': {'totalBytesBilled': str(1024**4)}}]
        from unittest.mock import Mock
        reserve, reconcile = Mock(), Mock()
        with patch.object(policy, 'configuration', return_value={
                'monthly_budget_usd': 100, 'reservation_usd': 2, 'bigquery_usd_per_tib': 6.25}), \
             patch.object(policy, '_reserve_sync', reserve), patch.object(policy, '_reconcile_sync', reconcile):
            await policy.budget('start', context=context)
            await policy.budget('finish', context=context, outcome='failed')
        self.assertNotIn('_atlas_budget_reservation', context.memo)
        self.assertEqual(reconcile.call_args.args[:2], ('u', 2.0))
        self.assertAlmostEqual(reconcile.call_args.args[2], 6.25)


class Http:
    async def post(self, *args, **kwargs): pass


class Clients:
    descriptor_count = 1; grants = None; sec = None
    def __init__(self):
        from query_context import ProviderPermits
        self.http = Http(); self.permits = ProviderPermits({'llm': 2, 'finder': 2})
    async def start(self): return self
    async def close(self): pass
    def bind(self, context): context.http_client = self.http; context.permits = self.permits; return context


class AppPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_principal_reaches_engine_and_missing_token_is_403(self):
        seen = []
        async def engine(question, *, context, **kwargs):
            seen.append(context.principal)
            return {'question': question, 'answer': 'ok', 'shape': 'point', 'plan': '', 'usage': {},
                    'discovery_usage': {}, 'intent': {}, 'attempts': [], 'evidence': {},
                    'answer_renderer': 'x', 'source': {}, 'candidates': [], 'data': {}}
        reg = extensions.Registry()
        @reg.principal
        async def principal(request):
            if request.headers.get('authorization') != 'Bearer good': raise runtime.AccessDenied('missing')
            return {'uid': 'u', 'entitlements': ['x']}
        application = app.create_app(engine=engine, clients_factory=Clients)
        life = application.router.lifespan_context(application)
        with patch.object(extensions, 'registry', return_value=reg), patch('harness._sources_catalog', return_value=[]):
            await life.__aenter__()
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url='http://test')
            try:
                denied = await client.post('/ask', json={'query': 'q', 'streaming': False})
                allowed = await client.post('/ask', headers={'Authorization': 'Bearer good'},
                                            json={'query': 'q', 'streaming': False})
            finally:
                await client.aclose(); await life.__aexit__(None, None, None)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(seen, [{'uid': 'u', 'entitlements': ['x']}])
