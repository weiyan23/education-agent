"""Synthetic, in-memory profile inputs. No database, model or student execution."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from uuid import UUID

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from profiling.run_store import fingerprint
from recommendation_input import exercise_lookup_path, prepare_versioned_context
from research_adapter import AdapterError


class RecommendationInputTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            "version": "synthetic-java-v1",
            "concepts": {"java.condition": "Java 条件判断"},
            "prerequisites": [],
            "associations": [],
            "relation_sources": {},
        }
        self.profile = {
            "student_id": "101",
            "status": "completed",
            "failed_steps": [],
            "model_id": "offline-fixture",
            "prompt_version": "offline-fixture-v1",
            "concepts": deepcopy(self.catalog["concepts"]),
            "evidence": [{
                "record_id": "synthetic-e1", "student_id": "101",
                "split": "train", "problem_id": "synthetic-q1",
                "question": "解释条件 x > 0 的分支。",
                "program": "class Example {}",
                "source": {"file": "synthetic-in-memory", "index": 0},
            }],
            "observations": [{
                "concept_id": "java.condition", "student_id": "101",
                "evidence_id": "synthetic-e1", "kind": "model_inference",
                "status": "Unknown", "description": "离线合成观察，仅验证输入契约。",
            }],
            "summaries": {},
        }
        self.row = {
            "id": UUID("00000000-0000-0000-0000-000000000001"),
            "org_id": 1, "course_id": 10, "student_id": 101, "version": 2,
            "method_kind": "research_prototype",
            "capability_status": "unvalidated",
            "method_version": "synthetic-profile-v1", "provenance": "simulated",
        }
        self.options = {
            "org_id": 1, "course_id": 10, "student_id": 101,
            "expected_profile_id": str(self.row["id"]), "expected_version": 2,
            "allowed_concept_ids": ["java.condition"],
        }

    def prepare(self):
        return prepare_versioned_context(
            self.catalog, self.profile, self.row, **self.options
        )

    def assert_code(self, code):
        with self.assertRaises(AdapterError) as found:
            self.prepare()
        self.assertEqual(found.exception.code, code)

    def test_context_binds_version_hash_and_simulated_source(self):
        result = self.prepare()
        ref = result["profile_ref"]
        self.assertEqual(ref["profile_version_id"], str(self.row["id"]))
        self.assertEqual(ref["version"], 2)
        self.assertEqual(ref["provenance"], "simulated")
        self.assertEqual(ref["capability_status"], "unvalidated")
        self.assertEqual(len(ref["profile_sha256"]), 64)
        context = result["context"]
        self.assertEqual(context["student_id"], "101")
        self.assertEqual(context["concept_context"]["java.condition"]["counts"],
                         {"Unknown": 1})
        self.assertNotIn("decision", context)

    def test_b_artifact_selects_targets_only_for_its_frozen_context(self):
        prepared = self.prepare()
        decision = {"decision": "recommend", "selected_candidate_id": None,
                    "concept_ids": ["java.condition"], "reason": "合成测试",
                    "evidence_ids": ["synthetic-e1"], "relation_ids": [], "uncertainties": []}
        report = {"schema": "recommendation-round-v1", "strategy": "B",
                  "execution_status": "completed", "decision_kind": "model_inference",
                  "student_id": "101", "context_sha256": fingerprint(prepared["context"]),
                  "status": "recommended", "decision": decision}
        self.assertEqual(exercise_lookup_path(prepared, report),
                         f"/courses/10/recommendation-exercises?concept_id=java.condition&profile_version_id={self.row['id']}")
        decision.update(decision="insufficient_evidence", concept_ids=[], evidence_ids=[])
        report["status"] = "insufficient_evidence"
        self.assertIsNone(exercise_lookup_path(prepared, report))
        report["context_sha256"] = "wrong"
        with self.assertRaises(AdapterError):
            exercise_lookup_path(prepared, report)

    def test_missing_profile_is_explicit(self):
        self.row = None
        self.assert_code("PROFILE_NOT_AVAILABLE")

    def test_cross_org_course_and_student_are_rejected(self):
        for field in ("org_id", "course_id", "student_id"):
            with self.subTest(field=field):
                self.row[field] += 1
                self.assert_code("PROFILE_SCOPE_MISMATCH")
                self.row[field] -= 1

    def test_changed_version_or_id_is_rejected_on_recheck(self):
        self.prepare()
        self.row["version"] = 3
        self.assert_code("STALE_PROFILE")
        self.row["version"] = 2
        self.row["id"] = UUID("00000000-0000-0000-0000-000000000002")
        self.assert_code("STALE_PROFILE")

    def test_rule_statistics_cannot_be_used_as_research_profile(self):
        self.row["method_kind"] = "rule_statistics"
        self.assert_code("RESEARCH_PROFILE_REQUIRED")

    def test_disconnected_profile_is_rejected(self):
        self.row["capability_status"] = "not_connected"
        self.assert_code("PROFILE_NOT_AVAILABLE")

    def test_partial_or_foreign_research_profile_is_rejected(self):
        self.profile["status"] = "partial"
        self.assert_code("INCOMPLETE_PROFILE")
        self.profile["status"] = "completed"
        self.profile["student_id"] = "102"
        self.assert_code("INVALID_RESEARCH_PROFILE_CONTEXT")

    def test_test_split_and_invalid_evidence_links_are_rejected(self):
        self.profile["evidence"][0]["split"] = "test"
        self.assert_code("INVALID_RESEARCH_PROFILE_CONTEXT")
        self.profile["evidence"][0]["split"] = "train"
        self.profile["observations"][0]["evidence_id"] = "missing"
        self.assert_code("INVALID_RESEARCH_PROFILE_CONTEXT")

    def test_snapshot_isolated_from_later_input_changes(self):
        before = deepcopy(self.profile)
        result = self.prepare()
        self.assertEqual(self.profile, before)
        self.profile["observations"][0]["description"] = "changed"
        self.assertNotEqual(
            result["context"]["concept_context"]["java.condition"]
                  ["observations"][0]["description"],
            "changed",
        )
        self.assertNotEqual(
            result["profile_ref"]["profile_sha256"],
            self.prepare()["profile_ref"]["profile_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
