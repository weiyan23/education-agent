"""Offline regressions across profiling, original recommendation and local CLI contracts.

All model outputs below are explicit test stubs. No credentials, network, Java or
LearnHouse database are used; temporary RunStore files are local checkpoints only.
"""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_recommendation import catalog, profile
from profiling.adapter import (export_evidence_for_evaluation, export_profile_for_recommendation,
                               get_learning_state_for_s08)
from profiling.events import create_learning_event, filter_events_for_student, validate_learning_event
from profiling.evidence import create_evidence_packet, validate_effective_grade
from profiling.reconstruction import reconstruct_profile
from profiling.run_store import RunStore
from profiling.sequence import compute_sequence_trajectories, validate_sequence_input
from profiling.student_profile import StudentProfile
from profiling.synthetic_sequence import generate_synthetic_sequences
from recommendation.context import prepare_context
from webapp.artifacts import ArtifactStore
from webapp.commands import build_command
import reconstruct_profile as reconstruction_cli


ROOT = Path(__file__).resolve().parents[1]
TIME = '2026-11-24T00:00:00+00:00'


class OfflineClient:
    model = 'offline-test-stub'
    stage_models = {}
    key = ''
    url = 'https://example.invalid'
    max_tokens = 500
    timeout = 1

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def complete(self, stage, prompt):
        self.calls.append(stage)
        if self.fail:
            raise ValueError('Intentional offline model failure')
        if stage == 'extract':
            return {'observations': [{'concept_id': 'a', 'status': 'Unknown',
                                      'description': '测试桩：保留本次代码表现的不确定性'}]}
        return {'evaluation': '测试桩：仅描述所提供观察，不断言长期掌握。'}


def grade(score=80):
    return {'score': score, 'max_score': 100, 'status': 'effective',
            'graded_by': 'teacher-test', 'graded_at': TIME}


def event(kind, payload=None, **overrides):
    values = dict(event_type=kind, student_id='s1', course_id='course-1',
                  payload=payload or {}, provenance={'operator_id': 'teacher-test'}, timestamp=TIME)
    values.update(overrides)
    return create_learning_event(**values)


def bound_profile():
    return dict(profile(), course_id='course-1', is_active=True)


def live_profile():
    packet = create_evidence_packet('s1', 'q-live', 'int x = 1;', '描述代码',
                                    submission_id='sub-test', received_at=TIME, effective_grade=grade())
    result = StudentProfile('s1', catalog()['concepts'], OfflineClient().complete,
                            model_id='offline-test-stub').build([packet])
    result.update(status='completed', is_active=True, course_id='course-1')
    return result


def context(value):
    return prepare_context(catalog(), student_id='s1', allowed_concept_ids=list(catalog()['concepts']), profile=value)


class ReconstructionIntegrationTests(unittest.TestCase):
    def test_live_factory_to_original_recommendation_contract(self):
        value = live_profile()
        self.assertIsNone(value['evidence'][0]['submission']['submitted_at'])
        self.assertEqual(context(value)['evidence'][0]['record_id'], value['evidence'][0]['record_id'])

    def test_grade_revision_retains_history_and_passes_original_consumer(self):
        base = live_profile()
        before = deepcopy(base)
        correction = event('grade_revised', {'record_id': base['evidence'][0]['record_id'],
                                            'revised_grade': grade(95)})
        client = OfflineClient()
        report = reconstruct_profile(None, client, base, catalog(), correction_event=correction)
        current = report['profile']
        self.assertEqual(base, before)
        self.assertEqual(current['course_id'], 'course-1')
        self.assertEqual(len(current['evidence']), 1)
        self.assertEqual(current['evidence'][0]['record_id'], base['evidence'][0]['record_id'])
        self.assertEqual(current['evidence'][0]['effective_grade']['score'], 95)
        self.assertEqual(current['evidence_history'][0]['effective_grade']['score'], 80)
        self.assertEqual(current['evidence_history'][0]['lifecycle_status'], 'superseded')
        self.assertFalse(report['stale_profile']['is_active'])
        self.assertEqual(len(context(current)['evidence']), 1)
        self.assertEqual(len(context(export_profile_for_recommendation(current, 's1', 'course-1'))['evidence']), 1)
        calls = len(client.calls)
        with self.assertRaisesRegex(ValueError, 'already applied'):
            reconstruct_profile(None, client, current, catalog(), correction_event=correction)
        self.assertEqual(len(client.calls), calls)

    def test_later_correction_keeps_prior_audit_history(self):
        base = live_profile()
        rid = base['evidence'][0]['record_id']
        first = reconstruct_profile(None, OfflineClient(), base, catalog(),
                                    correction_event=event('grade_revised', {'record_id': rid, 'revised_grade': grade(90)}))['profile']
        second = reconstruct_profile(None, OfflineClient(), first, catalog(),
                                     correction_event=event('submission_voided', {'record_id': rid}))
        self.assertEqual(second['profile']['evidence'], [])
        self.assertEqual(len(second['profile']['evidence_history']), 2)
        self.assertEqual(second['failed_steps'], [])
        self.assertEqual(second['revision']['active_evidence_count'], 0)
        self.assertEqual(context(second['profile'])['evidence'], [])

    def test_course_revocation_blocks_exports_without_model_work(self):
        client = OfflineClient()
        result = reconstruct_profile(None, client, bound_profile(), catalog(),
                                     correction_event=event('access_revoked', {'reason': '退班'}))
        current = result['profile']
        self.assertEqual(client.calls, [])
        self.assertEqual(current['course_id'], 'course-1')
        self.assertEqual(current['access_status'], 'revoked')
        self.assertFalse(current['is_active'])
        self.assertEqual(current['evidence'], [])
        self.assertEqual(len(current['evidence_history']), 2)
        self.assertFalse(export_profile_for_recommendation(current, 's1')['is_eligible_for_recommendation'])
        with self.assertRaises(ValueError):
            context(current)
        with self.assertRaises(ValueError):
            reconstruct_profile(None, client, current, catalog())

    def test_foreign_or_tampered_corrections_fail_before_model_work(self):
        invalid = [event('access_revoked', student_id='other'), event('access_revoked', course_id='other')]
        tampered = event('access_revoked')
        tampered['payload']['reason'] = 'changed'
        invalid.append(tampered)
        invalid.append(event('submission_voided', {'record_id': 'missing'}))
        invalid.append(event('grade_revised', {'record_id': 'e1'}))
        invalid.append(event('grade_revised', {'record_id': 'e1', 'revised_grade': dict(grade(), status='under_review')}))
        for correction in invalid:
            client = OfflineClient()
            with self.subTest(correction=correction), self.assertRaises(ValueError):
                reconstruct_profile(None, client, bound_profile(), catalog(), correction_event=correction)
            self.assertEqual(client.calls, [])

    def test_inactive_evidence_cannot_bypass_original_consumer(self):
        for status in ('revoked', 'superseded'):
            value = bound_profile()
            value['evidence'][0]['lifecycle_status'] = status
            with self.subTest(status=status), self.assertRaises(ValueError):
                context(value)

    def test_partial_reconstruction_never_publishes_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'run'
            with RunStore(folder, {'test': 'partial'}, 12) as store:
                report = reconstruct_profile(store, OfflineClient(fail=True), bound_profile(), catalog())
            self.assertEqual(report['status'], 'partial')
            self.assertIsNone(report['profile'])
            self.assertFalse((folder / 'profile.json').exists())
            self.assertFalse(json.loads((folder / 'invalidated-profile.json').read_text())['is_active'])

    def test_resume_reuses_completed_steps_and_identical_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'run'
            with RunStore(folder, {'test': 'resume'}, 12) as store:
                first = reconstruct_profile(store, OfflineClient(), bound_profile(), catalog())
            unused = OfflineClient(fail=True)
            with RunStore(folder, {'test': 'resume'}, 12, resume=True) as store:
                second = reconstruct_profile(store, unused, bound_profile(), catalog())
            self.assertEqual(unused.calls, [])
            self.assertEqual(second, first)


class AdapterBoundaryTests(unittest.TestCase):
    def test_partial_failed_unknown_or_inactive_are_not_eligible(self):
        for status in ('partial', 'failed', 'invalidated', None):
            value = dict(bound_profile(), status=status)
            with self.subTest(status=status):
                self.assertFalse(export_profile_for_recommendation(value, 's1')['is_eligible_for_recommendation'])
        self.assertFalse(export_profile_for_recommendation(dict(bound_profile(), is_active=False), 's1')['is_eligible_for_recommendation'])

    def test_cross_course_rejected_not_relabelled(self):
        for call in (export_profile_for_recommendation, get_learning_state_for_s08):
            with self.subTest(call=call.__name__), self.assertRaises(ValueError):
                call(bound_profile(), 's1', 'course-2')

    def test_evaluation_checks_scope_hash_and_fixture_origin(self):
        forged = event('submission_received')
        forged['event_sha256'] = '0' * 64
        fixture = event('submission_received', provenance={'operator_id': 'test', 'data_source': 'synthetic_demo_data'})
        for candidate in (event('submission_received', student_id='other'),
                          event('submission_received', course_id='other'), forged, fixture):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                export_evidence_for_evaluation(bound_profile(), [candidate], 's1')

    def test_shape_and_hash_checks_never_claim_source_authenticity(self):
        result = export_evidence_for_evaluation(bound_profile(), [event('submission_received')], 's1')
        self.assertFalse(result['integrity_verified'])
        self.assertEqual(result['source_verification_status'], 'unverified_local_input')
        self.assertIsNone(result['active_evidences'][0]['source_hash'])
        exported = export_profile_for_recommendation(bound_profile(), 's1')
        self.assertFalse(exported['integrity_verified'])

    def test_evaluation_rejects_foreign_and_fixture_evidence(self):
        for update in ({'student_id': 'other'}, {'split': 'test_fixture'}):
            value = bound_profile()
            value['evidence'][0].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                export_evidence_for_evaluation(value, student_id='s1')

    def test_stale_view_is_empty_and_observation_counts_do_not_vote(self):
        self.assertEqual(get_learning_state_for_s08(dict(bound_profile(), is_active=False), 's1', 'course-1')['concept_cards'], [])
        card = get_learning_state_for_s08(bound_profile(), 's1', 'course-1')['concept_cards'][0]
        self.assertEqual(card['status'], 'observed')
        self.assertEqual(card['counts'], {'Bad': 1})


class SequenceBoundaryTests(unittest.TestCase):
    def test_synthetic_examples_remain_explicit_demonstrations(self):
        concepts = {'concepts': {'java.condition': '条件', 'java.array': '数组', 'java.loop': '循环'}}
        for sequence in generate_synthetic_sequences():
            result = compute_sequence_trajectories(sequence, concepts)
            self.assertEqual(result['data_source'], 'synthetic_demo_data')
            self.assertTrue(result['is_synthetic'])
            self.assertFalse(result['learning_evidence_eligible'])
            self.assertTrue(all(t['temporal_confidence'] == 'none' for t in result['concept_trajectories'].values()))

    def test_unverified_draft_and_declared_real_inputs_cannot_assert_cognition(self):
        for source in (None, 'real_student_behavior'):
            sequence = generate_synthetic_sequences()[0]
            sequence.pop('is_synthetic')
            sequence.pop('data_source')
            if source:
                sequence['data_source'] = source
            result = compute_sequence_trajectories(sequence, {'concepts': {'java.condition': '条件'}})
            self.assertEqual(result['data_source'], 'unverified')
            self.assertEqual(result['concept_trajectories']['java.condition']['current_state'], 'no_observation')
            self.assertFalse(result['learning_evidence_eligible'])

    def test_draft_result_ignored_and_one_judgment_is_insufficient_for_trend(self):
        sequence = generate_synthetic_sequences()[0]
        draft = deepcopy(sequence['actions'][0])
        draft['action_type'] = 'draft_save'
        draft['behavior_metrics'] = {'result': 'correct', 'evaluation_status': 'synthetic_effective'}
        sequence['actions'] = [draft]
        result = compute_sequence_trajectories(sequence, {'concepts': {'java.condition': '条件'}})
        self.assertEqual(result['concept_trajectories']['java.condition']['current_state'], 'no_observation')
        draft['action_type'] = 'grade_feedback'
        result = compute_sequence_trajectories(sequence, {'concepts': {'java.condition': '条件'}})
        self.assertEqual(result['concept_trajectories']['java.condition']['trend'], 'insufficient_data')

    def test_synthetic_relabelling_and_invalid_sequence_bounds_rejected(self):
        bad = []
        sequence = generate_synthetic_sequences()[0]
        bad.append(dict(sequence, data_source='real_student_behavior'))
        other = deepcopy(sequence)
        other['actions'][1]['step_index'] = other['actions'][0]['step_index']
        bad.append(other)
        other = deepcopy(sequence)
        other['sequence_window']['end_time'] = other['sequence_window']['start_time']
        bad.append(other)
        other = deepcopy(sequence)
        other['actions'][0]['behavior_metrics']['duration_seconds'] = float('nan')
        bad.append(other)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_sequence_input(value)

    def test_event_order_uses_instants_and_rejects_tampering(self):
        earlier = event('submission_received', timestamp='2026-11-24T08:00:00+08:00')
        later = event('submission_received', timestamp='2026-11-24T01:00:00+00:00')
        ordered = filter_events_for_student([later, earlier], 's1', 'course-1')
        self.assertEqual([x['event_id'] for x in ordered], [earlier['event_id'], later['event_id']])
        later['student_id'] = 'changed'
        with self.assertRaises(ValueError):
            validate_learning_event(later)

    def test_invalid_numeric_grades_not_accepted(self):
        for score in (True, float('nan'), float('inf')):
            with self.subTest(score=score), self.assertRaises(ValueError):
                validate_effective_grade(grade(score))


class LocalCliContractTests(unittest.TestCase):
    def test_all_revoked_cli_writes_consistent_report_and_resumes_without_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            base = live_profile()
            (folder / 'base.json').write_text(json.dumps(base))
            (folder / 'catalog.json').write_text(json.dumps(catalog()))
            argv = ['reconstruct_profile.py', '--base-profile', str(folder / 'base.json'),
                    '--catalog', str(folder / 'catalog.json'), '--output', str(folder / 'out'),
                    '--revoke-record-id', base['evidence'][0]['record_id'], '--execute']
            for resume in (False, True):
                client = OfflineClient(fail=True)
                with patch.object(sys, 'argv', argv + (['--resume'] if resume else [])), \
                        patch.object(reconstruction_cli, 'read_env', return_value={}), \
                        patch.object(reconstruction_cli, 'CachedChatClient', return_value=client), \
                        patch.object(reconstruction_cli, 'send_chat', side_effect=AssertionError('No network')), \
                        redirect_stdout(io.StringIO()):
                    reconstruction_cli.main()
                self.assertEqual(client.calls, [])
            report = json.loads((folder / 'out/reconstruction.json').read_text())
            self.assertEqual(report['status'], 'completed')
            self.assertEqual(report['failed_steps'], [])
            self.assertEqual(report['revision']['active_evidence_count'], 0)
            self.assertEqual(report['profile']['evidence'], [])
            self.assertEqual(len(report['profile']['evidence_history']), 1)

    def test_workbench_sequence_preview_does_not_write_execute_uses_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'artifacts').mkdir()
            (folder / 'config').mkdir()
            sequence = generate_synthetic_sequences()[0]
            (folder / 'artifacts/input.json').write_text(json.dumps(sequence))
            (folder / 'config/catalog.json').write_text(json.dumps({'concepts': {'java.condition': '条件'}}))
            artifacts = ArtifactStore(folder)
            for mode in ('preview', 'execute'):
                args, output = build_command(artifacts, {
                    'operation': 'sequence', 'mode': mode, 'confirm': mode == 'execute',
                    'fields': {'sequence_file': 'artifacts/input.json', 'catalog': 'config/catalog.json'},
                }, mode)
                args[1] = str(ROOT / 'run_sequence.py')
                result = subprocess.run([sys.executable, '-B', *args[1:]], cwd=ROOT,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                info = json.loads(result.stdout)
                self.assertEqual(info['mode'], mode)
                self.assertEqual((folder / output / 'sequence.json').exists(), mode == 'execute')


if __name__ == '__main__':
    unittest.main()
