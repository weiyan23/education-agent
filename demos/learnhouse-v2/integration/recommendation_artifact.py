"""Read a completed B result from the server-owned adapter store, never from HTTP input."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import UUID

from recommendation.comparison import relation_handoff
from recommendation_input import exercise_lookup_path, prepare_versioned_context
from research_adapter import AdapterError, _digest


def read_result(storage, task, profile, actor_id):
    job = str(UUID(str(task["id"])))
    root = Path(storage).resolve()
    journal = root / "adapter.sqlite3"
    if not journal.is_file():
        raise AdapterError("ADAPTER_RESULT_UNAVAILABLE")
    # Adapter's replay scope binds this exact job to the dispatcher and operation.
    expected_scope = _digest([str(task["course_id"]), str(actor_id), "recommend", job])
    with closing(sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT status,result FROM jobs WHERE job_id=? AND idem_scope=?",
                         (job, expected_scope)).fetchone()
    if not row or row[0] not in {"recommended", "insufficient_evidence"}:
        raise AdapterError("ADAPTER_RESULT_UNAVAILABLE")
    result = json.loads(row[1])
    refs = result["artifact_refs"]
    fixture = result["model_connection"] == "not_verified_fixture"
    if (result["job_id"] != job or result["status"] != row[0] or result["error_code"]
            or len(refs) != 1 or refs[0]["kind"] != "recommendation"
            or result["model_connection"] not in {"response_received", "not_verified_fixture"}):
        raise AdapterError("INVALID_RESULT")
    if fixture and profile["provenance"] not in {"simulated", "demo", "manual_example"}:
        raise AdapterError("FIXTURE_SCOPE_MISMATCH")

    def contents(relative):
        path = root / job / relative
        if (path.resolve() != path or not path.is_file() or path.stat().st_size > 8_000_000):
            raise AdapterError("MISSING_OR_UNSAFE_RESULT")
        return path.read_bytes()

    raw = contents("output/recommendation.json")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != refs[0]["sha256"]:
        raise AdapterError("ARTIFACT_HASH_MISMATCH")
    report = json.loads(raw)
    inputs = task["input_refs"]
    research_profile = json.loads(contents("inputs/profile.json"))
    record_ids = {r.get("record_id", r["evidence_id"]) for r in profile["evidence_manifest"]}
    if any(e["record_id"] not in record_ids for e in research_profile["evidence"]):
        raise AdapterError("UNMAPPED_EVIDENCE")
    prepared = prepare_versioned_context(
        json.loads(contents("inputs/catalog.json")), research_profile,
        profile, org_id=task["org_id"], course_id=task["course_id"], student_id=inputs["student_id"],
        expected_profile_id=inputs["profile_version_id"], expected_version=inputs["version"],
        allowed_concept_ids=report["allowed_concept_ids"], goal_concept_ids=report["goal_concept_ids"],
        requirements=report["requirements"])
    if prepared["profile_ref"]["profile_sha256"] != inputs["profile_sha256"]:
        raise AdapterError("PROFILE_HASH_MISMATCH")
    exercise_lookup_path(prepared, report)
    if report["status"] != row[0] or not report.get("recommendation_id"):
        raise AdapterError("INVALID_RESULT")
    decision = report["decision"]
    return {"research_decision_id": report["recommendation_id"], "run_id": report["run_id"],
            "decision": decision["decision"], **relation_handoff(prepared["context"], decision),
            "reason": decision["reason"], "concept_ids": decision["concept_ids"],
            "research_evidence_ids": decision["evidence_ids"], "uncertainties": decision["uncertainties"],
            "artifact_sha256": digest, "context_sha256": report["context_sha256"],
            "model_connection": result["model_connection"], "prompt_version": report.get("prompt_version"),
            "model_id": (report.get("identity") or {}).get("model"),
            "stage_models": (report.get("identity") or {}).get("stage_models", {}),
            "provenance": "simulated" if fixture else profile["provenance"]}
