"""Synthetic dynamic learning sequence generator for reproducible testing and demonstration.

Generates three realistic educational sequence archetypes:
1. progressive_mastery: Read material -> initial bad attempt -> practice -> good final attempt.
2. repeated_trial_misconception: Repetitive failed attempts on boundary conditions with low revision response.
3. grade_revision_recovery: Initial negative assessment corrected by teacher revision in T10, showing state restoration.

All outputs are explicitly flagged as 'synthetic_demo_data' to prevent contamination of real research datasets.
"""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from .sequence import SCHEMA_SEQUENCE_INPUT


def create_iso_offset(base_dt, minutes_offset):
    return (base_dt + timedelta(minutes=minutes_offset)).isoformat()


def generate_synthetic_sequences(student_id='student_synth_01', course_id='java_basics'):
    """Generate 3 distinct synthetic behavioral sequences for evaluation and demos."""
    base_time = datetime(2026, 11, 24, 9, 0, 0, tzinfo=timezone.utc)

    # Archetype 1: Progressive Mastery on 'java.condition'
    seq1 = {
        'schema': SCHEMA_SEQUENCE_INPUT,
        'sequence_id': f'seq_synth_prog_{student_id}',
        'student_id': student_id,
        'course_id': course_id,
        'data_source': 'synthetic_demo_data',
        'is_synthetic': True,
        'description': '常规递进掌握：从阅读资料、初次试错，到针对性练习后掌握知识点。',
        'sequence_window': {
            'start_time': create_iso_offset(base_time, 0),
            'end_time': create_iso_offset(base_time, 120)
        },
        'actions': [
            {
                'step_index': 1,
                'timestamp': create_iso_offset(base_time, 5),
                'action_type': 'material_read',
                'concept_id': 'java.condition',
                'behavior_metrics': {
                    'duration_seconds': 450,
                    'resource_id': 'mat_condition_intro',
                    'scroll_depth_ratio': 1.0
                }
            },
            {
                'step_index': 2,
                'timestamp': create_iso_offset(base_time, 25),
                'action_type': 'draft_save',
                'concept_id': 'java.condition',
                'behavior_metrics': {
                    'duration_seconds': 180,
                    'diff_chars': 120,
                    'attempt_count': 1
                }
            },
            {
                'step_index': 3,
                'timestamp': create_iso_offset(base_time, 35),
                'action_type': 'submission_attempt',
                'concept_id': 'java.condition',
                'behavior_metrics': {
                    'duration_seconds': 600,
                    'result': 'incorrect',
                    'error_category': 'logic_operator',
                    'attempt_count': 1
                }
            },
            {
                'step_index': 4,
                'timestamp': create_iso_offset(base_time, 40),
                'action_type': 'grade_feedback',
                'concept_id': 'java.condition',
                'behavior_metrics': {
                    'duration_seconds': 120,
                    'result': 'Bad',
                    'raw_score': 40,
                    'max_score': 100
                }
            },
            {
                'step_index': 5,
                'timestamp': create_iso_offset(base_time, 70),
                'action_type': 'practice_exercise',
                'concept_id': 'java.condition',
                'behavior_metrics': {
                    'duration_seconds': 540,
                    'result': 'correct',
                    'attempt_count': 2
                }
            },
            {
                'step_index': 6,
                'timestamp': create_iso_offset(base_time, 85),
                'action_type': 'submission_attempt',
                'concept_id': 'java.condition',
                'behavior_metrics': {
                    'duration_seconds': 400,
                    'result': 'correct',
                    'raw_score': 100,
                    'max_score': 100,
                    'attempt_count': 3
                }
            }
        ]
    }

    # Archetype 2: Repeated Trial Misconception on 'java.loop'
    base_time2 = datetime(2026, 11, 24, 13, 0, 0, tzinfo=timezone.utc)
    seq2 = {
        'schema': SCHEMA_SEQUENCE_INPUT,
        'sequence_id': f'seq_synth_misconception_{student_id}',
        'student_id': student_id,
        'course_id': course_id,
        'data_source': 'synthetic_demo_data',
        'is_synthetic': True,
        'description': '持续误解与反复试错：死循环与边界条件频繁出错，缺乏有效订正思考。',
        'sequence_window': {
            'start_time': create_iso_offset(base_time2, 0),
            'end_time': create_iso_offset(base_time2, 60)
        },
        'actions': [
            {
                'step_index': 1,
                'timestamp': create_iso_offset(base_time2, 10),
                'action_type': 'submission_attempt',
                'concept_id': 'java.loop',
                'behavior_metrics': {
                    'duration_seconds': 120,
                    'result': 'incorrect',
                    'error_category': 'infinite_loop',
                    'attempt_count': 1
                }
            },
            {
                'step_index': 2,
                'timestamp': create_iso_offset(base_time2, 15),
                'action_type': 'submission_attempt',
                'concept_id': 'java.loop',
                'behavior_metrics': {
                    'duration_seconds': 80,
                    'result': 'incorrect',
                    'error_category': 'off_by_one',
                    'attempt_count': 2
                }
            },
            {
                'step_index': 3,
                'timestamp': create_iso_offset(base_time2, 22),
                'action_type': 'submission_attempt',
                'concept_id': 'java.loop',
                'behavior_metrics': {
                    'duration_seconds': 60,
                    'result': 'incorrect',
                    'error_category': 'off_by_one',
                    'attempt_count': 3
                }
            }
        ]
    }

    # Archetype 3: Grade Revision Recovery on 'java.array'
    base_time3 = datetime(2026, 11, 24, 16, 0, 0, tzinfo=timezone.utc)
    seq3 = {
        'schema': SCHEMA_SEQUENCE_INPUT,
        'sequence_id': f'seq_synth_revision_{student_id}',
        'student_id': student_id,
        'course_id': course_id,
        'data_source': 'synthetic_demo_data',
        'is_synthetic': True,
        'description': '改分订正与教学干预：初次评分因测试用例格式误判，教师在T10人工复核改分后恢复。',
        'sequence_window': {
            'start_time': create_iso_offset(base_time3, 0),
            'end_time': create_iso_offset(base_time3, 90)
        },
        'actions': [
            {
                'step_index': 1,
                'timestamp': create_iso_offset(base_time3, 10),
                'action_type': 'submission_attempt',
                'concept_id': 'java.array',
                'behavior_metrics': {
                    'duration_seconds': 720,
                    'result': 'incorrect',
                    'error_category': 'format_mismatch',
                    'attempt_count': 1
                }
            },
            {
                'step_index': 2,
                'timestamp': create_iso_offset(base_time3, 20),
                'action_type': 'recheck_request',
                'concept_id': 'java.array',
                'behavior_metrics': {
                    'duration_seconds': 90,
                    'reason': '输出末尾换行符被测试脚本误判'
                }
            },
            {
                'step_index': 3,
                'timestamp': create_iso_offset(base_time3, 50),
                'action_type': 'correction_revision',
                'concept_id': 'java.array',
                'behavior_metrics': {
                    'duration_seconds': 0,
                    'result': 'correct',
                    'revised_by': 'teacher_01',
                    'raw_score': 100,
                    'max_score': 100
                }
            }
        ]
    }

    return [seq1, seq2, seq3]
