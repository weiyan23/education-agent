"""Synthetic preview and HTTP role gate, with a test identity dependency only.

This does not replace full Cookie/database authorization acceptance.
"""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

DEMO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(DEMO / 'integration'))
sys.path.insert(0, str(DEMO.parent.parent))
from research_preview import build_research_preview


class PreviewTests(unittest.TestCase):
    def test_all_scenarios_are_read_only_synthetic(self):
        result = build_research_preview()
        self.assertEqual(result['data_source'], 'synthetic_demo_data')
        self.assertIs(result['learning_evidence_eligible'], False)
        self.assertIs(result['model_called'], False)
        self.assertEqual(len(result['scenarios']), 3)
        for scenario in result['scenarios']:
            self.assertEqual(scenario['analysis']['total_actions'], len(scenario['actions']))
            self.assertTrue(scenario['analysis']['concept_trajectories'])
            self.assertNotIn('student_id', scenario)

    def test_source_confusion_fails_closed(self):
        with patch('research_preview.generate_synthetic_sequences', return_value=[{
            'data_source': 'real_student_behavior', 'is_synthetic': True,
        }]):
            with self.assertRaises(ValueError):
                build_research_preview()


class PreviewHttpTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi import FastAPI, HTTPException
        security = ModuleType('src.education.security')
        def fail(code, status=400, message=''):
            raise HTTPException(status_code=status, detail={'code': code})
        async def require_actor():
            fail('AUTHENTICATION_REQUIRED', 401)
        security.fail = fail
        security.require_actor = require_actor
        path = DEMO / 'upstream/learnhouse/apps/api/src/education/preview_routes.py'
        spec = importlib.util.spec_from_file_location('_v21_preview_route_test', path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'src.education.security': security}):
            spec.loader.exec_module(module)
        cls.app = FastAPI()
        cls.app.include_router(module.router)
        cls.dependency = staticmethod(require_actor)

    async def asyncSetUp(self):
        import httpx
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test')

    async def asyncTearDown(self):
        self.app.dependency_overrides.clear()
        await self.client.aclose()

    def role(self, role):
        async def current_actor():
            return SimpleNamespace(role=role)
        self.app.dependency_overrides[self.dependency] = current_actor

    async def test_anonymous_rejected(self):
        self.assertEqual((await self.client.get('/research-preview')).status_code, 401)

    async def test_student_rejected(self):
        self.role('student')
        self.assertEqual((await self.client.get('/research-preview')).status_code, 403)

    async def test_teacher_can_read_without_caching(self):
        self.role('teacher')
        response = await self.client.get('/research-preview')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertFalse(response.json()['learning_evidence_eligible'])

    async def test_admin_only_gets_synthetic_examples(self):
        self.role('admin')
        result = (await self.client.get('/research-preview')).json()
        self.assertEqual(result['data_source'], 'synthetic_demo_data')
        self.assertNotIn('evidence_manifest', result)

    async def test_no_write_operation(self):
        self.role('teacher')
        self.assertEqual((await self.client.post('/research-preview', json={})).status_code, 405)


if __name__ == '__main__':
    unittest.main()
