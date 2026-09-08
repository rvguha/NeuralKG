import unittest
from unittest.mock import patch
import harness
import app
import httpx


class ExistingUITests(unittest.IsolatedAsyncioTestCase):
    async def test_flow_returns_to_existing_chat_ui(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app),base_url='http://test') as client:
            response=await client.get('/flow')
            self.assertEqual(response.status_code,307)
            self.assertEqual(response.headers['location'],'/')
            page=await client.get('/')
            self.assertIn('chat-history.js',page.text)
            self.assertIn('id="history-list"',page.text)
            self.assertNotIn('From question to evidence-backed answer',page.text)
            self.assertNotIn('Test the new flow',page.text)

    def test_new_stages_use_existing_text_progress(self):
        for event in [
            {'kind':'shape_candidates','candidates':[{'shape':'join.double-change','status':'ok'}]},
            {'kind':'discovery_started','queries':['stock prices','complaints']},
            {'kind':'discovery_completed','resources':[]},
            {'kind':'plan_ready','plan':{'candidate':'join.double-change'}},
            {'kind':'input_started','read':{'question':'prices'}},
            {'kind':'input_completed','evidence':{'value':0}},
            {'kind':'synthesis_started','shape':'join.double-change'},
        ]:
            with self.subTest(event=event):self.assertTrue(harness._nlweb_text(event))

    def test_template_details_are_in_existing_answer_disclosure(self):
        self.assertIn('How this answer was produced',harness.PAGE)
        self.assertIn('Template candidates and bindings',harness.PAGE)
        self.assertIn('Computed result and input evidence',harness.PAGE)
        self.assertIn('Saved query trace',harness.PAGE)
