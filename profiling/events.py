"""Unified learning event contract and lifecycle event tracking."""
from copy import deepcopy
from datetime import datetime, timezone
import uuid
from .run_store import fingerprint
from .evidence import validate_timestamp

# Standard event types across the educational multi-agent lifecycle
EVENT_SUBMISSION_RECEIVED = 'submission_received'
EVENT_AUTO_GRADED = 'auto_graded'
EVENT_TEACHER_GRADED = 'teacher_graded'
EVENT_GRADE_REVISED = 'grade_revised'
EVENT_RECHECK_REQUESTED = 'recheck_requested'
EVENT_RECHECK_RESOLVED = 'recheck_resolved'
EVENT_ACCESS_GRANTED = 'access_granted'
EVENT_ACCESS_REVOKED = 'access_revoked'

ALLOWED_EVENT_TYPES = {
    EVENT_SUBMISSION_RECEIVED,
    EVENT_AUTO_GRADED,
    EVENT_TEACHER_GRADED,
    EVENT_GRADE_REVISED,
    EVENT_RECHECK_REQUESTED,
    EVENT_RECHECK_RESOLVED,
    EVENT_ACCESS_GRANTED,
    EVENT_ACCESS_REVOKED,
}


def current_iso_timestamp():
    return datetime.now(timezone.utc).isoformat()


def validate_learning_event(event):
    """Rigorous contract validation for learning events."""
    if not isinstance(event, dict):
        raise ValueError('Learning event must be a dictionary')
    required_fields = ('event_id', 'event_type', 'student_id', 'course_id', 'timestamp', 'payload', 'provenance')
    for field in required_fields:
        if field not in event:
            raise ValueError(f'Missing required learning event field: {field}')
        if field in ('event_id', 'event_type', 'student_id', 'course_id'):
            if not isinstance(event[field], str) or not event[field].strip():
                raise ValueError(f'Event {field} must be a non-empty string')

    if event['event_type'] not in ALLOWED_EVENT_TYPES:
        raise ValueError(f'Unknown event type: {event["event_type"]}')

    validate_timestamp(event['timestamp'])

    if not isinstance(event['payload'], dict):
        raise ValueError('Event payload must be a dictionary')
    if not isinstance(event['provenance'], dict):
        raise ValueError('Event provenance must be a dictionary')

    prov = event['provenance']
    for k in ('resource_id', 'version', 'operator_id'):
        if k in prov and (not isinstance(prov[k], (str, int)) or str(prov[k]).strip() == ''):
            raise ValueError(f'Invalid provenance value for {k}')


def create_learning_event(event_type, student_id, course_id, payload, provenance, timestamp=None, event_id=None):
    """Factory for standard immutable learning events."""
    ts = timestamp or current_iso_timestamp()
    eid = event_id or f'evt_{uuid.uuid4().hex}'
    event = {
        'event_id': eid,
        'event_type': event_type,
        'student_id': student_id,
        'course_id': course_id,
        'timestamp': ts,
        'payload': deepcopy(payload),
        'provenance': deepcopy(provenance),
        'event_sha256': fingerprint([eid, event_type, student_id, course_id, ts, payload, provenance]),
    }
    validate_learning_event(event)
    return event


def filter_events_for_student(events, student_id, course_id=None):
    """Filter and validate events for a specific student and course boundary."""
    filtered = []
    for evt in events:
        validate_learning_event(evt)
        if evt['student_id'] != student_id:
            continue
        if course_id is not None and evt['course_id'] != course_id:
            continue
        filtered.append(deepcopy(evt))
    # Sort chronologically by timestamp
    filtered.sort(key=lambda e: e['timestamp'])
    return filtered
