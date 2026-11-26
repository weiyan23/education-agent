"""Bind a research recommendation context to an explicit product profile version.

The backend must authorize access and load the latest profile row before calling
this function. Both row and profile must come from trusted server-side storage.
This module does not read the database or invoke a model.
"""
from copy import deepcopy
from urllib.parse import urlencode

from profiling.run_store import fingerprint
from recommendation.comparison import validate_decision
from recommendation.context import prepare_context
from research_adapter import AdapterError


def prepare_versioned_context(
    catalog,
    profile,
    current_profile_row,
    *,
    org_id,
    course_id,
    student_id,
    expected_profile_id,
    expected_version,
    allowed_concept_ids,
    goal_concept_ids=(),
    requirements=(),
):
    """Validate a current product version and its linked research profile.

    current_profile_row is the latest row for this org/course/student, loaded by
    an authorized backend reader. profile is the research artifact linked to that
    exact row. The storage format/link is owned by the profile integration.
    Call again with freshly read state before importing a recommendation result.
    """
    row = current_profile_row
    if row is None:
        raise AdapterError("PROFILE_NOT_AVAILABLE")
    if (row["org_id"], row["course_id"], row["student_id"]) != (
        org_id, course_id, student_id
    ):
        raise AdapterError("PROFILE_SCOPE_MISMATCH")
    if (
        not expected_profile_id
        or type(expected_version) is not int
        or expected_version < 1
        or str(row["id"]) != str(expected_profile_id)
        or row["version"] != expected_version
    ):
        raise AdapterError("STALE_PROFILE")
    if row["method_kind"] != "research_prototype":
        raise AdapterError("RESEARCH_PROFILE_REQUIRED")
    if row["capability_status"] not in {"connected", "unvalidated"}:
        raise AdapterError("PROFILE_NOT_AVAILABLE")
    if row["provenance"] not in {"real", "demo", "manual_example", "simulated", "mixed"}:
        raise AdapterError("INVALID_PROFILE_PROVENANCE")
    if not isinstance(profile, dict) or profile.get("status") != "completed":
        raise AdapterError("INCOMPLETE_PROFILE")

    # Freeze the same payload that is validated and fingerprinted.
    profile_snapshot = deepcopy(profile)
    try:
        context = prepare_context(
            catalog,
            student_id=str(student_id),
            allowed_concept_ids=allowed_concept_ids,
            goal_concept_ids=goal_concept_ids,
            requirements=requirements,
            profile=profile_snapshot,
        )
    except ValueError as exc:
        raise AdapterError("INVALID_RESEARCH_PROFILE_CONTEXT") from exc

    return {
        "profile_ref": {
            "org_id": org_id,
            "course_id": course_id,
            "student_id": student_id,
            "profile_version_id": str(row["id"]),
            "version": row["version"],
            "method_version": row["method_version"],
            "capability_status": row["capability_status"],
            "provenance": row["provenance"],
            "profile_sha256": fingerprint(profile_snapshot),
        },
        "context": context,
    }

def exercise_lookup_path(prepared, report):
    """Build the authenticated lookup for a trusted B artifact and its frozen input."""
    context = prepared["context"]
    ref = prepared["profile_ref"]
    if (report.get("schema") != "recommendation-round-v1"
            or report.get("strategy") != "B" or report.get("execution_status") != "completed"
            or report.get("decision_kind") != "model_inference"
            or report.get("student_id") != context["student_id"]
            or str(ref["student_id"]) != context["student_id"]
            or report.get("context_sha256") != fingerprint(context)):
        raise AdapterError("RECOMMENDATION_CONTEXT_MISMATCH")
    decision = report.get("decision")
    validate_decision(context, decision)
    if decision["decision"] == report.get("status") == "insufficient_evidence":
        return None
    if decision["decision"] != "recommend" or report.get("status") != "recommended":
        raise AdapterError("INVALID_RECOMMENDATION_STATUS")
    query = urlencode([("concept_id", code) for code in decision["concept_ids"]]
                      + [("profile_version_id", ref["profile_version_id"])])
    return f"/courses/{ref['course_id']}/recommendation-exercises?{query}"
