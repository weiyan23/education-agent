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

    window = seq['sequence_window']
    if not isinstance(window, dict) or 'start_time' not in window or 'end_time' not in window:
        raise ValueError('sequence_window requires start_time and end_time')
    validate_timestamp(window['start_time'])
    validate_timestamp(window['end_time'])

    actions = seq['actions']
    if not isinstance(actions, list):
        raise ValueError('actions must be a list')

    last_ts = None
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
        if last_ts is not None and curr_dt < last_ts:
            raise ValueError(f'Actions must be chronologically ordered at index {idx}')
        last_ts = curr_dt

        metrics = act['behavior_metrics']
        if not isinstance(metrics, dict):
            raise ValueError(f'Action at index {idx} behavior_metrics must be a dict')


def compute_sequence_trajectories(seq_input, concept_catalog):
    """Transform an ordered sequence of behavioral actions into concept trajectory summaries."""
    validate_sequence_input(seq_input)
    concepts = concept_catalog.get('concepts', {})

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
                'limitations': '存在浏览或草稿记录，但无已生效的掌握评测依据。'
            }
            continue

        current_state = state_history[-1]['state']
        # Determine qualitative trend
        if len(state_history) == 1:
            trend = 'stable'
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
            'temporal_confidence': 'high' if len(state_history) >= 3 else 'medium',
            'events_count': len(acts),
            'evaluated_actions_count': len(state_history),
            'history': state_history,
            'limitations': '基于有限时序事件推断，未引入掌握概率或长期预测。'
        }

    total_duration = sum(a['behavior_metrics'].get('duration_seconds', 0) for a in seq_input['actions'])

    output = {
        'schema': SCHEMA_SEQUENCE_OUTPUT,
        'sequence_id': seq_input['sequence_id'],
        'student_id': seq_input['student_id'],
        'course_id': seq_input['course_id'],
        'data_source': seq_input.get('data_source', 'real_student_behavior'),
        'sequence_window': deepcopy(seq_input['sequence_window']),
        'total_actions': len(seq_input['actions']),
        'total_duration_seconds': total_duration,
        'concept_trajectories': trajectories,
        'fingerprint': fingerprint([seq_input['sequence_id'], trajectories]),
        'generated_at': datetime.now(timezone.utc).isoformat()
    }
    return output
