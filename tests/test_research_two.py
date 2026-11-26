"""Basic local examples for Research Content 2; integration boundaries have separate regressions."""
import unittest
from datetime import datetime, timezone
from profiling.events import (
    create_learning_event, validate_learning_event, filter_events_for_student,
    EVENT_SUBMISSION_RECEIVED, EVENT_GRADE_REVISED, EVENT_ACCESS_REVOKED
)
from profiling.evidence import (
    validate_learning_evidence, validate_effective_grade, is_evidence_active, invalidate_evidence
)
from profiling.reconstruction import (
    invalidate_profile, apply_evidence_correction, reconstruct_profile
)
from profiling.sequence import (
    validate_sequence_input, compute_sequence_trajectories, SCHEMA_SEQUENCE_INPUT
)
from profiling.synthetic_sequence import generate_synthetic_sequences
from profiling.adapter import (
    export_profile_for_recommendation, export_evidence_for_evaluation,
    get_learning_state_for_s08, get_class_learning_summary_for_t11
)


class DummyClient:
    def __init__(self):
        self.model = 'dummy-model'

    def complete(self, stage, prompt):
        if stage == 'extract':
            return {
                'observations': [
                    {'concept_id': 'java.branch', 'status': 'Good', 'description': '正确使用if语句进行分支判断。'}
                ]
            }
        elif stage == 'summarize':
            return {
                'evaluation': '知识点掌握良好，能够准确根据逻辑分支处理输入。'
            }
        return {}


class TestLearningEvents(unittest.TestCase):
    def test_create_and_validate_event(self):
        evt = create_learning_event(
            event_type=EVENT_SUBMISSION_RECEIVED,
            student_id='student_a',
            course_id='course_java',
            payload={'submission_id': 'sub_001', 'score': 100},
            provenance={'resource_id': 'res_01', 'version': 1, 'operator_id': 'student_a'}
        )
        self.assertEqual(evt['event_type'], EVENT_SUBMISSION_RECEIVED)
        self.assertEqual(evt['student_id'], 'student_a')
        self.assertTrue('event_sha256' in evt)

    def test_invalid_event_type(self):
        with self.assertRaises(ValueError):
            create_learning_event(
                event_type='unknown_illegal_type',
                student_id='student_a',
                course_id='course_java',
                payload={},
                provenance={}
            )

    def test_filter_events(self):
        e1 = create_learning_event(EVENT_SUBMISSION_RECEIVED, 's1', 'c1', {}, {})
        e2 = create_learning_event(EVENT_GRADE_REVISED, 's2', 'c1', {}, {})
        filtered = filter_events_for_student([e1, e2], student_id='s1', course_id='c1')
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]['student_id'], 's1')


class TestEvidenceExtensions(unittest.TestCase):
    def test_effective_grade_validation(self):
        valid_grade = {
            'score': 85,
            'max_score': 100,
            'status': 'effective',
            'graded_by': 'teacher_01',
            'graded_at': datetime.now(timezone.utc).isoformat()
        }
        validate_effective_grade(valid_grade)

        invalid_grade = dict(valid_grade, score=-10)
        with self.assertRaises(ValueError):
            validate_effective_grade(invalid_grade)

        invalid_status = dict(valid_grade, status='unknown_status')
        with self.assertRaises(ValueError):
            validate_effective_grade(invalid_status)

    def test_invalidation_logic(self):
        evidence = {
            'record_id': 'rec_01',
            'student_id': 's1',
            'problem_id': 'p1',
            'split': 'train',
            'evidence_origin': 'historical_student_submission',
            'lifecycle_status': 'active',
            'effective_grade': {
                'score': 100, 'max_score': 100, 'status': 'effective',
                'graded_by': 'teacher_01', 'graded_at': datetime.now(timezone.utc).isoformat()
            }
        }
        self.assertTrue(is_evidence_active(evidence))
        inv = invalidate_evidence(evidence, reason='改分作废', revoked_by='teacher_01')
        self.assertFalse(is_evidence_active(inv))
        self.assertEqual(inv['lifecycle_status'], 'revoked')
        self.assertEqual(inv['effective_grade']['status'], 'revoked')
        self.assertEqual(inv['invalidation']['reason'], '改分作废')


class TestProfileInvalidationAndReconstruction(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            'version': 'test-v1',
            'concepts': {'java.branch': '分支结构', 'java.loop': '循环结构'},
            'prerequisites': [],
            'associations': []
        }
        self.base_profile = {
            'student_id': 's1',
            'course_id': 'java_basics',
            'model_id': 'dummy-model',
            'prompt_version': 'embracing-adapted-v4-execution-evidence',
            'concepts': self.catalog['concepts'],
            'observations': [
                {'concept_id': 'java.branch', 'status': 'Good', 'description': 'desc', 'evidence_id': 'rec_01'}
            ],
            'summaries': {
                'java.branch': {
                    'evaluation': '很好', 'counts': {'Good': 1}, 'evidence_ids': ['rec_01'], 'kind': 'model_inference'
                }
            },
            'evidence': [
                {
                    'record_id': 'rec_01',
                    'student_id': 's1',
                    'problem_id': 'p1',
                    'question': 'Q1',
                    'program': 'class Sol {}',
                    'source': {'file': 'rec_01', 'index': 0},
                    'split': 'train',
                    'evidence_origin': 'historical_student_submission',
                    'lifecycle_status': 'active'
                }
            ],
            'is_active': True,
            'status': 'completed'
        }

    def test_invalidate_profile(self):
        inv = invalidate_profile(self.base_profile, reason='教师修改了评分', operator_id='teacher_01')
        self.assertFalse(inv['is_active'])
        self.assertEqual(inv['status'], 'invalidated')
        self.assertEqual(inv['invalidation']['reason'], '教师修改了评分')
        self.assertTrue(len(inv['revisions']) > 0)

    def test_reconstruct_profile_empty_when_all_revoked(self):
        client = DummyClient()
        correction = create_learning_event('submission_voided', 's1', 'java_basics',
            {'record_id': 'rec_01', 'reason': '作答作废'}, {'operator_id': 'teacher_01'})
        result = reconstruct_profile(None, client, self.base_profile, self.catalog,
                                     correction_event=correction, reason='清零重构')
        self.assertEqual(result['status'], 'completed')
        profile = result['profile']
        self.assertTrue(profile['is_active'])
        self.assertEqual(len(profile['observations']), 0)
        self.assertEqual(len(profile['summaries']), 0)


class TestDynamicSequenceModeling(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            'concepts': {'java.condition': '条件判断', 'java.loop': '循环结构', 'java.array': '数组'},
            'prerequisites': {},
            'relations': {}
        }

    def test_synthetic_generation_and_trajectories(self):
        sequences = generate_synthetic_sequences(student_id='student_synth_test')
        self.assertEqual(len(sequences), 3)

        for seq in sequences:
            validate_sequence_input(seq)
            output = compute_sequence_trajectories(seq, self.catalog)
            self.assertEqual(output['student_id'], 'student_synth_test')
            self.assertTrue('concept_trajectories' in output)

        # Verify Scenario 1 progressive mastery exhibits 'improving' or 'Good'
        prog_output = compute_sequence_trajectories(sequences[0], self.catalog)
        cond_state = prog_output['concept_trajectories']['java.condition']
        self.assertEqual(cond_state['current_state'], 'Good')
        self.assertEqual(cond_state['trend'], 'improving')

        # Verify unobserved concepts stay honest
        loop_state = prog_output['concept_trajectories']['java.loop']
        self.assertEqual(loop_state['current_state'], 'no_observation')
        self.assertEqual(loop_state['trend'], 'insufficient_data')


class TestAdaptersAndHandoffs(unittest.TestCase):
    def setUp(self):
        self.active_profile = {
            'student_id': 's1',
            'course_id': 'java_basics',
            'model_id': 'dummy-model',
            'prompt_version': 'test-v1',
            'concepts': {'java.branch': '分支结构'},
            'summaries': {
                'java.branch': {
                    'evaluation': '良好',
                    'counts': {'Good': 1},
                    'evidence_ids': ['rec_01'],
                    'kind': 'model_inference'
                }
            },
            'evidence': [
                {
                    'record_id': 'rec_01',
                    'student_id': 's1',
                    'problem_id': 'p1',
                    'question': '判断分支', 'program': 'if (x > 0) return 1;',
                    'source': {'file': 'test-only.json', 'index': 0},
                    'split': 'train',
                    'lifecycle_status': 'active'
                }
            ],
            'observations': [{'concept_id': 'java.branch', 'status': 'Good', 'description': '本次代码的分支表现',
                              'evidence_id': 'rec_01', 'student_id': 's1', 'kind': 'model_inference'}],
            'is_active': True,
            'status': 'completed'
        }

    def test_export_for_recommendation_blocks_invalidated(self):
        stale = dict(self.active_profile, is_active=False, status='invalidated')
        delivered = export_profile_for_recommendation(stale, 's1')
        self.assertFalse(delivered['is_eligible_for_recommendation'])
        self.assertEqual(delivered['status'], 'invalidated')

    def test_export_for_recommendation_success(self):
        delivered = export_profile_for_recommendation(self.active_profile, 's1')
        self.assertTrue(delivered['is_eligible_for_recommendation'])
        self.assertEqual(delivered['status'], 'completed')
        self.assertTrue('profile_fingerprint' in delivered)

    def test_s08_and_t11_views(self):
        s08 = get_learning_state_for_s08(self.active_profile, 's1', 'java_basics')
        self.assertEqual(s08['student_id'], 's1')
        self.assertEqual(len(s08['concept_cards']), 1)
        self.assertEqual(s08['concept_cards'][0]['status'], 'observed')

        t11 = get_class_learning_summary_for_t11({'s1': self.active_profile}, 'class_01', ['s1', 's2'])
        self.assertEqual(t11['class_id'], 'class_01')
        self.assertEqual(len(t11['roster']), 2)


if __name__ == '__main__':
    unittest.main()
