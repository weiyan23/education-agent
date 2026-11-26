"""Offline recommendation eligibility/availability regressions; no DB or model."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID


API = Path(__file__).resolve().parents[2] / 'upstream/learnhouse/apps/api/src/education'


class Rejected(Exception):
    def __init__(self, code, status, message=''):
        self.code, self.status = code, status


def reject(code, status=400, message=''):
    raise Rejected(code, status, message)


def load_modules():
    # Replace infrastructure only. Execute the real mapping/import functions.
    business = ModuleType('src.education.business')
    security = ModuleType('src.education.security')
    security.fail = reject
    security.require_course = AsyncMock()
    security.allowed_class_ids = AsyncMock(return_value=[11])
    src, education = ModuleType('src'), ModuleType('src.education')
    education.business = business
    modules = {'src': src, 'src.education': education,
               'src.education.business': business, 'src.education.security': security}
    with patch.dict(sys.modules, modules):
        loaded = []
        for name in ('recommendation_mapping', 'recommendation_import'):
            spec = importlib.util.spec_from_file_location('src.education.' + name, API / (name + '.py'))
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            loaded.append(module)
    return *loaded, business


mapping, importer, business = load_modules()


class AttemptEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 26, 10, tzinfo=timezone.utc)
        self.data = dict(opens_at=self.now-timedelta(days=1), due_at=self.now+timedelta(days=1),
                         checked_at=self.now, max_attempts=1, attempts=[], grants=[])

    def attempt(self, *, status='submitted', grant=None, slot=None):
        return dict(id='attempt-1', status=status, resubmission_grant_id=grant, grant_slot=slot)

    def grant(self, **overrides):
        return dict(id='grant-1', starts_at=self.now-timedelta(hours=1),
                    expires_at=self.now+timedelta(hours=1), revoked_at=None,
                    allowed_attempts=1, **overrides)

    def option(self):
        return mapping.attempt_option(**self.data)

    def test_normal_window_and_capacity(self):
        self.assertEqual(self.option()['kind'], 'normal')
        self.data['attempts'] = [self.attempt()]
        self.assertIsNone(self.option())

    def test_expired_normal_window_without_grant(self):
        self.data['due_at'] = self.now-timedelta(seconds=1)
        self.assertIsNone(self.option())

    def test_late_grant_remains_available_after_due_date(self):
        self.data.update(due_at=self.now-timedelta(days=1), grants=[self.grant()])
        self.assertEqual(self.option(), dict(kind='grant', attempt_id=None, resubmission_grant_id='grant-1'))

    def test_correction_grant_after_normal_attempts_exhausted(self):
        self.data.update(attempts=[self.attempt()], grants=[self.grant()])
        self.assertEqual(self.option()['kind'], 'grant')

    def test_granted_attempt_does_not_consume_normal_quota(self):
        self.data['attempts'] = [self.attempt(grant='grant-1', slot=1)]
        self.assertEqual(self.option()['kind'], 'normal')

    def test_expired_revoked_future_and_exhausted_grants_are_rejected(self):
        self.data.update(due_at=self.now-timedelta(seconds=1))
        for changes in ({'expires_at': self.now}, {'revoked_at': self.now},
                        {'starts_at': self.now+timedelta(seconds=1)}):
            with self.subTest(changes=changes):
                grant = self.grant()
                grant.update(changes)
                self.data['grants'] = [grant]
                self.assertIsNone(self.option())
        self.data.update(grants=[self.grant()], attempts=[self.attempt(grant='grant-1', slot=1)])
        self.assertIsNone(self.option())

    def test_uses_max_grant_slot_not_row_count_and_skips_exhausted_grants(self):
        grant = self.grant()
        grant['allowed_attempts'] = 3
        self.data.update(due_at=self.now-timedelta(seconds=1), grants=[grant],
                         attempts=[self.attempt(grant='grant-1', slot=3)])
        self.assertIsNone(self.option())
        next_grant = self.grant()
        next_grant['id'] = 'grant-2'
        self.data['grants'].append(next_grant)
        self.assertEqual(self.option()['resubmission_grant_id'], 'grant-2')

    def test_existing_normal_draft_resumes_even_at_attempt_limit(self):
        self.data['attempts'] = [self.attempt(status='draft')]
        self.assertEqual(self.option()['kind'], 'resume')
        self.assertIsNone(self.option()['resubmission_grant_id'])

    def test_existing_grant_draft_resumes_even_when_last_slot_allocated(self):
        self.data.update(due_at=self.now-timedelta(days=1), grants=[self.grant()],
                         attempts=[self.attempt(status='draft', grant='grant-1', slot=1)])
        self.assertEqual(self.option()['kind'], 'resume')
        self.data['grants'][0]['revoked_at'] = self.now
        self.assertIsNone(self.option())

    def test_another_grant_does_not_mask_unusable_existing_draft(self):
        self.data.update(due_at=self.now-timedelta(seconds=1), grants=[self.grant()],
                         attempts=[self.attempt(status='draft')])
        self.assertIsNone(self.option())

    def test_database_timestamp_strings_and_boundaries(self):
        self.data.update(opens_at=self.now.isoformat(), due_at=self.now.isoformat(),
                         checked_at=self.now.isoformat())
        self.assertEqual(self.option()['kind'], 'normal')
        self.data['opens_at'] = (self.now+timedelta(seconds=1)).isoformat()
        self.assertIsNone(self.option())


class AvailabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.actor = SimpleNamespace(role='student', user_id=2, org_id=1, principal_id='actor')
        business.one = AsyncMock(return_value=None)
        business.rows = AsyncMock(return_value=[])
        business.execute = AsyncMock()
        business.write_once = AsyncMock()
        business.must = lambda row: row
        business.clean = lambda value: value

    async def test_registration_is_explicitly_unavailable_without_writing_a_task(self):
        with patch.object(importer, 'require_course', AsyncMock()) as scope:
            with self.assertRaises(Rejected) as error:
                await importer.register(None, self.actor, 10, 2, 'profile', None)
            self.assertEqual((error.exception.code, error.exception.status),
                             ('RECOMMENDATION_EXECUTION_UNAVAILABLE', 503))
            scope.assert_awaited_once()
        business.one.assert_not_awaited()
        business.execute.assert_not_awaited()
        business.write_once.assert_not_awaited()

    async def test_registration_still_rejects_foreign_student(self):
        with self.assertRaises(Rejected) as error:
            await importer.register(None, self.actor, 10, 3, 'profile', None)
        self.assertEqual((error.exception.code, error.exception.status), ('SCOPE_DENIED', 403))

    async def test_gate_does_not_disable_verified_existing_task_receipt(self):
        profile = {'id': 'profile', 'evidence_manifest': []}
        receipt = {'recommendation_version_id': 'decision', 'task_id': 'task'}
        business.one.return_value = {'input_refs': {'student_id': 2, 'profile_version_id': 'profile'},
            'status': 'succeeded', 'execution_epoch': 0, 'result_refs': receipt}
        with patch.object(importer, 'current_profile', AsyncMock(return_value=profile)), \
                patch.object(importer, 'evidence_scope', AsyncMock(return_value=({}, {}))):
            self.assertEqual(await importer.import_result(None, self.actor, 10, 2, 'task'), receipt)
        business.execute.assert_not_awaited()

    async def test_mapping_applies_eligibility_and_strips_internal_attempt_details(self):
        now = datetime(2026, 11, 26, tzinfo=timezone.utc)
        row = {'binding_id': 'binding', 'resource_version_id': 'resource',
               'publication_event_id': 'publication', '_opens_at': now-timedelta(days=2),
               '_due_at': now-timedelta(days=1), '_max_attempts': 1, '_checked_at': now,
               '_attempts': [], '_grants': [{'id': 'grant', 'starts_at': now-timedelta(hours=1),
                   'expires_at': now+timedelta(hours=1), 'revoked_at': None, 'allowed_attempts': 1}]}
        business.rows.return_value = [deepcopy(row)]
        result = await mapping.resolve_exercises(None, self.actor, 10, ['java.condition'])
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(result['items'][0]['start_option']['resubmission_grant_id'], 'grant')
        self.assertFalse(any(key.startswith('_') for key in result['items'][0]))
        row['_grants'][0]['revoked_at'] = now
        business.rows.return_value = [row]
        result = await mapping.resolve_exercises(None, self.actor, 10, ['java.condition'])
        self.assertEqual((result['state'], result['items']), ('no_content', []))


def load_open_routes(dependencies):
    """Run actual route bodies without booting FastAPI/LMS dependencies.

    Decorators and dependency defaults are intentionally not exercised here;
    HTTP authentication and PostgreSQL isolation need integration acceptance.
    """
    source = API / 'business_routes.py'
    names = {'open_decision_exercise', 'open_exercise_match'}
    functions = [node for node in ast.parse(source.read_text()).body
                 if isinstance(node, ast.AsyncFunctionDef) and node.name in names]
    if {node.name for node in functions} != names:
        raise AssertionError('Recommendation route functions are missing')
    for node in functions:
        node.decorator_list = []
        node.returns = None
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            arg.annotation = None
        node.args.defaults = [ast.Constant(None) for _ in node.args.defaults]
        node.args.kw_defaults = [None if value is None else ast.Constant(None)
                                 for value in node.args.kw_defaults]
    tree = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))
    namespace = {'fail': reject, **dependencies}
    exec(compile(tree, str(source), 'exec'), namespace)
    return SimpleNamespace(**{name: namespace[name] for name in names})


class OpenRecommendationRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.binding = UUID('00000000-0000-0000-0000-000000000001')
        self.resource = UUID('00000000-0000-0000-0000-000000000002')
        self.publication = UUID('00000000-0000-0000-0000-000000000003')
        self.version = UUID('00000000-0000-0000-0000-000000000004')
        self.actor, self.db = object(), object()
        self.options = [dict(kind='normal', attempt_id=None, resubmission_grant_id=None),
                        dict(kind='grant', attempt_id=None, resubmission_grant_id='grant-current'),
                        dict(kind='resume', attempt_id='draft-current', resubmission_grant_id='grant-current')]
        self.item = dict(binding_id=str(self.binding), resource_version_id=str(self.resource),
                         publication_event_id=str(self.publication), start_option=self.options[0])
        self.read = AsyncMock(return_value={'state': 'ready', 'items': [self.item]})
        self.resolve = AsyncMock(return_value={'state': 'ready', 'items': [self.item]})
        self.get = AsyncMock(return_value={'id': str(self.binding), 'etag': 'live-etag',
                                          'start_option': {'kind': 'outdated'}})
        self.routes = load_open_routes({'b': SimpleNamespace(get_binding=self.get),
            'ri': SimpleNamespace(read_decision=self.read), 'resolve_exercises': self.resolve})

    async def decision(self):
        return await self.routes.open_decision_exercise(
            10, 2, self.version, self.binding, actor=self.actor, db=self.db)

    async def match(self):
        return await self.routes.open_exercise_match(
            10, self.binding, self.resource, self.publication, ['java.condition'],
            profile_version_id='profile-current', actor=self.actor, db=self.db)

    async def test_decision_passes_current_option_for_exact_binding(self):
        self.read.return_value['items'].insert(0, {**self.item, 'binding_id': 'another-binding',
                                                 'start_option': {'kind': 'wrong-binding'}})
        for option in self.options:
            with self.subTest(option=option):
                self.item['start_option'] = option
                result = await self.decision()
                self.assertEqual(result['start_option'], option)
                self.assertEqual(result['etag'], 'live-etag')
                self.read.assert_awaited_with(self.db, self.actor, 10, 2, self.version)
                self.get.assert_awaited_with(self.db, self.actor, self.binding)

    async def test_decision_stale_evidence_cannot_reveal_binding(self):
        self.read.return_value['state'] = 'stale'
        with self.assertRaises(Rejected) as error:
            await self.decision()
        self.assertEqual((error.exception.code, error.exception.status), ('STALE_EVIDENCE', 409))
        self.get.assert_not_awaited()

    async def test_decision_missing_binding_cannot_fall_back_to_general_binding(self):
        self.item['binding_id'] = 'another-binding'
        with self.assertRaises(Rejected) as error:
            await self.decision()
        self.assertEqual((error.exception.code, error.exception.status), ('EXERCISE_UNAVAILABLE', 404))
        self.get.assert_not_awaited()

    async def test_decision_version_and_scope_rejections_propagate(self):
        for code, status in (('STALE_RECOMMENDATION', 409), ('SCOPE_DENIED', 403)):
            with self.subTest(code=code):
                self.read.side_effect = Rejected(code, status)
                with self.assertRaises(Rejected) as error:
                    await self.decision()
                self.assertEqual((error.exception.code, error.exception.status), (code, status))
        self.get.assert_not_awaited()

    async def test_mapping_passes_current_option_and_frozen_profile_to_resolver(self):
        for option in self.options:
            with self.subTest(option=option):
                self.item['start_option'] = option
                result = await self.match()
                self.assertEqual(result['start_option'], option)
                self.assertEqual(result['etag'], 'live-etag')
                self.resolve.assert_awaited_with(self.db, self.actor, 10, ['java.condition'],
                    binding_id=self.binding, profile_version_id='profile-current')
                self.get.assert_awaited_with(self.db, self.actor, self.binding)

    async def test_mapping_empty_result_cannot_reveal_binding(self):
        self.resolve.return_value = {'state': 'no_content', 'items': []}
        with self.assertRaises(Rejected) as error:
            await self.match()
        self.assertEqual((error.exception.code, error.exception.status), ('EXERCISE_UNAVAILABLE', 404))
        self.get.assert_not_awaited()

    async def test_mapping_resource_or_publication_replacement_is_rejected(self):
        for key in ('resource_version_id', 'publication_event_id'):
            with self.subTest(key=key):
                original = self.item[key]
                self.item[key] = 'replaced'
                with self.assertRaises(Rejected) as error:
                    await self.match()
                self.assertEqual((error.exception.code, error.exception.status),
                                 ('EXERCISE_VERSION_CHANGED', 409))
                self.item[key] = original
        self.get.assert_not_awaited()

    async def test_mapping_profile_or_access_rejection_cannot_fall_back(self):
        for code, status in (('STALE_PROFILE', 409), ('PROFILE_VERSION_REQUIRED', 409),
                             ('SCOPE_DENIED', 403)):
            with self.subTest(code=code):
                self.resolve.side_effect = Rejected(code, status)
                with self.assertRaises(Rejected) as error:
                    await self.match()
                self.assertEqual((error.exception.code, error.exception.status), (code, status))
        self.get.assert_not_awaited()

    async def test_binding_access_is_rechecked_after_both_recommendation_paths(self):
        self.get.side_effect = Rejected('SCOPE_DENIED', 403)
        for route in (self.decision, self.match):
            with self.subTest(route=route.__name__):
                with self.assertRaises(Rejected) as error:
                    await route()
                self.assertEqual((error.exception.code, error.exception.status), ('SCOPE_DENIED', 403))


if __name__ == '__main__':
    unittest.main()
