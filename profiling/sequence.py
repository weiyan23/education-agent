"""Dynamic behavioral sequence modeling specification, validation, and trajectory aggregation.

Models student learning processes as ordered temporal interactions rather than static aggregations.
Strictly adheres to:
1. No pseudo-probabilities (uses Good/Bad/Unknown/no_observation).
2. Explicit trend tracking (improving, regressing, fluctuating, stable, insufficient_data).
3. Clear isolation between synthetic demo sequences and real research evidence.
"""
from copy import deepcopy
from datetime import datetime, timezone
from collections import defaultdict
import math
from .evidence import validate_timestamp
from .run_store import fingerprint

SCHEMA_SEQUENCE_INPUT = 'dynamic-learning-sequence-input-v1'
SCHEMA_SEQUENCE_OUTPUT = 'dynamic-learning-sequence-output-v1'

ALLOWED_ACTION_TYPES = {
    'material_read',
    'draft_save',
    'submission_attempt',
    'grade_feedback',
    'recheck_request',
    'correction_revision',
    'practice_exercise'
}

ALLOWED_QUALITATIVE_STATES = {'Good', 'Bad', 'Unknown', 'no_observation'}
ALLOWED_TRENDS = {'improving', 'regressing', 'stable', 'fluctuating', 'insufficient_data'}


def validate_sequence_input(seq):
    """Rigorous schema validation for dynamic behavioral sequence inputs."""
    if not isinstance(seq, dict):
        raise ValueError('Sequence input must be a dictionary')
    if seq.get('schema') != SCHEMA_SEQUENCE_INPUT:
        raise ValueError(f'Expected schema {SCHEMA_SEQUENCE_INPUT}')

    for k in ('sequence_id', 'student_id', 'course_id', 'sequence_window', 'actions'):
        if k not in seq:
            raise ValueError(f'Missing required field in sequence input: {k}')
    for k in ('sequence_id', 'student_id', 'course_id'):
        if not isinstance(seq[k], str) or not seq[k].strip():
            raise ValueError(f'{k} must be a non-empty string')
    source = seq.get('data_source', 'unverified')
    if source not in ('unverified', 'synthetic_demo_data', 'real_student_behavior'):
        raise ValueError('Unknown sequence data_source')
    if seq.get('is_synthetic') is True and source != 'synthetic_demo_data':
        raise ValueError('Synthetic sequence cannot be relabelled as real or unverified')
    if source == 'synthetic_demo_data' and seq.get('is_synthetic') is not True:
        raise ValueError('Synthetic sequence must explicitly set is_synthetic=true')

    window = seq['sequence_window']
    if not isinstance(window, dict) or 'start_time' not in window or 'end_time' not in window:
        raise ValueError('sequence_window requires start_time and end_time')
    validate_timestamp(window['start_time'])
    validate_timestamp(window['end_time'])
    start = datetime.fromisoformat(window['start_time'].replace('Z', '+00:00'))
    end = datetime.fromisoformat(window['end_time'].replace('Z', '+00:00'))
    if start > end:
        raise ValueError('Sequence window starts after its end')

    actions = seq['actions']
    if not isinstance(actions, list):
        raise ValueError('actions must be a list')

    last_ts = None
    last_index = None
    for idx, act in enumerate(actions):
        if not isinstance(act, dict):
            raise ValueError(f'Action at index {idx} must be a dict')
        for k in ('step_index', 'timestamp', 'action_type', 'concept_id', 'behavior_metrics'):
            if k not in act:
                raise ValueError(f'Action at index {idx} missing field: {k}')
        if act['action_type'] not in ALLOWED_ACTION_TYPES:
            raise ValueError(f'Action at index {idx} has invalid action_type: {act["action_type"]}')
        validate_timestamp(act['timestamp'])

        # Enforce temporal ordering
        curr_dt = datetime.fromisoformat(act['timestamp'].replace('Z', '+00:00'))
        if curr_dt < start or curr_dt > end:
            raise ValueError('Action lies outside its declared sequence window')
        if last_ts is not None and curr_dt < last_ts:
            raise ValueError(f'Actions must be chronologically ordered at index {idx}')
        last_ts = curr_dt
        if (type(act['step_index']) is not int or act['step_index'] < 0
                or (last_index is not None and act['step_index'] <= last_index)):
            raise ValueError('step_index must be a strictly increasing nonnegative integer')
        last_index = act['step_index']

        metrics = act['behavior_metrics']
        if not isinstance(metrics, dict):
            raise ValueError(f'Action at index {idx} behavior_metrics must be a dict')
        duration = metrics.get('duration_seconds', 0)
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
            raise ValueError('duration_seconds must be finite and nonnegative')


def compute_sequence_trajectories(seq_input, concept_catalog):
    """Transform an ordered sequence of behavioral actions into concept trajectory summaries."""
    validate_sequence_input(seq_input)
    concepts = concept_catalog.get('concepts', {})
    synthetic = seq_input.get('data_source') == 'synthetic_demo_data'

    # Group actions by concept_id
    concept_actions = defaultdict(list)
    for act in seq_input['actions']:
        cid = act.get('concept_id')
        if cid and cid in concepts:
            concept_actions[cid].append(act)

    trajectories = {}
    for cid, cname in concepts.items():
        acts = concept_actions.get(cid, [])
        if not acts:
            trajectories[cid] = {
                'concept_id': cid,
                'concept_name': cname,
                'current_state': 'no_observation',
                'trend': 'insufficient_data',
                'temporal_confidence': 'none',
                'events_count': 0,
                'history': [],
                'limitations': '当前时间窗口内无该知识点的动态行为记录。'
            }
            continue

        # Extract evaluated states along the timeline
        state_history = []
        for act in acts:
            # Real ingestion/authentication has not been integrated. Unverified
            # JSON cannot assert a learner's state, even if it claims to be real.
            if (not synthetic or act['action_type'] not in ('grade_feedback', 'correction_revision')
                    or act['behavior_metrics'].get('evaluation_status') != 'synthetic_effective'):
                continue
            res = act['behavior_metrics'].get('result')
            # Map raw result to qualitative state
            if res in ('correct', 'Good'):
                st = 'Good'
            elif res in ('incorrect', 'Bad'):
                st = 'Bad'
            elif res in ('partial', 'Unknown'):
                st = 'Unknown'
            else:
                st = None

            if st is not None:
                state_history.append({
                    'timestamp': act['timestamp'],
                    'step_index': act['step_index'],
                    'state': st,
                    'action_type': act['action_type'],
                    'duration_seconds': act['behavior_metrics'].get('duration_seconds', 0)
                })

        if not state_history:
            trajectories[cid] = {
                'concept_id': cid,
                'concept_name': cname,
                'current_state': 'no_observation',
                'trend': 'insufficient_data',
                'temporal_confidence': 'none',
                'events_count': len(acts),
                'history': [],
                'limitations': '仅有行为记录或来源未核验，本版不能由此断言知识掌握状态。'
            }
            continue

        current_state = state_history[-1]['state']
        # Determine qualitative trend
        if len(state_history) == 1:
            trend = 'insufficient_data'
        else:
            first_state = state_history[0]['state']
            last_state = state_history[-1]['state']
            has_fluctuation = any(s['state'] != last_state for s in state_history[:-1])

            if first_state == 'Bad' and last_state == 'Good':
                trend = 'improving'
            elif first_state == 'Good' and last_state == 'Bad':
                trend = 'regressing'
            elif has_fluctuation and len(set(s['state'] for s in state_history)) > 1:
                trend = 'fluctuating'
            else:
                trend = 'stable'

        trajectories[cid] = {
            'concept_id': cid,
            'concept_name': cname,
            'current_state': current_state,
            'trend': trend,
            'temporal_confidence': 'none',
            'events_count': len(acts),
            'evaluated_actions_count': len(state_history),
            'history': state_history,
            'limitations': '合成评分结果的规则变化示意；未校准置信度，不代表真实认知状态或研究效果。'
        }

    total_duration = sum(a['behavior_metrics'].get('duration_seconds', 0) for a in seq_input['actions'])

    output = {
        'schema': SCHEMA_SEQUENCE_OUTPUT,
        'sequence_id': seq_input['sequence_id'],
        'student_id': seq_input['student_id'],
        'course_id': seq_input['course_id'],
        'data_source': 'synthetic_demo_data' if synthetic else 'unverified',
        'declared_data_source': seq_input.get('data_source'),
        'is_synthetic': synthetic,
        'source_verification_status': 'synthetic_fixture' if synthetic else 'unverified_local_input',
        'learning_evidence_eligible': False,
        'method_kind': 'synthetic_rule_illustration' if synthetic else 'unverified_behavior_summary',
        'sequence_window': deepcopy(seq_input['sequence_window']),
        'total_actions': len(seq_input['actions']),
        'total_duration_seconds': total_duration,
        'concept_trajectories': trajectories,
        'fingerprint': fingerprint([seq_input, concepts, trajectories]),
        'generated_at': datetime.now(timezone.utc).isoformat()
    }
    return output
