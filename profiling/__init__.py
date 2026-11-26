"""Evidence-based profiling adapted from Embracing Imperfection.

Uses lazy imports via __getattr__ to prevent circular import dependencies
between profiling and generation during package initialization.
"""

def __getattr__(name):
    if name in ('StudentProfile', 'PROMPT_VERSION'):
        from .student_profile import StudentProfile, PROMPT_VERSION
        return StudentProfile if name == 'StudentProfile' else PROMPT_VERSION
    if name in ('validate_learning_evidence', 'validate_timestamp', 'validate_effective_grade',
                'is_evidence_active', 'invalidate_evidence', 'create_evidence_packet'):
        from . import evidence
        return getattr(evidence, name)
    if name in ('create_learning_event', 'validate_learning_event', 'filter_events_for_student',
                'EVENT_SUBMISSION_RECEIVED', 'EVENT_AUTO_GRADED', 'EVENT_TEACHER_GRADED',
                'EVENT_GRADE_REVISED', 'EVENT_RECHECK_REQUESTED', 'EVENT_RECHECK_RESOLVED',
                'EVENT_ACCESS_GRANTED', 'EVENT_ACCESS_REVOKED'):
        from . import events
        return getattr(events, name)
    if name in ('reconstruct_profile', 'rebuild_profile', 'invalidate_profile', 'apply_evidence_correction'):
        from . import reconstruction
        return getattr(reconstruction, name)
    if name in ('validate_sequence_input', 'compute_sequence_trajectories',
                'SCHEMA_SEQUENCE_INPUT', 'SCHEMA_SEQUENCE_OUTPUT'):
        from . import sequence
        return getattr(sequence, name)
    if name == 'generate_synthetic_sequences':
        from .synthetic_sequence import generate_synthetic_sequences
        return generate_synthetic_sequences
    if name in ('export_profile_for_recommendation', 'export_evidence_for_evaluation',
                'get_learning_state_for_s08', 'get_class_learning_summary_for_t11'):
        from . import adapter
        return getattr(adapter, name)
    raise AttributeError(f"module 'profiling' has no attribute '{name}'")
