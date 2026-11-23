import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

INTEGRATION = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(INTEGRATION), str(INTEGRATION.parents[1])]

from generation_candidate import CandidateError, run_fixture_candidate


class GenerationCandidateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.storage = Path(directory.name)
        network = patch.object(socket.socket, "connect", side_effect=AssertionError("network is forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def run_candidate(self, active=lambda: True, targets=None):
        return run_fixture_candidate(
            self.storage, active, actor_id="teacher", course_id="course",
            student_id="fixture-student", job_id="job-one",
            targets={"java.condition": "v2-knowledge-point"} if targets is None else targets,
            requirement="练习条件判断", source_id="source-v1", source_hash="source-sha",
        )

    def test_fixture_runs_generation_review_and_revision_without_public_answers(self):
        candidate = self.run_candidate()
        self.assertEqual(len(candidate.versions), 2)
        self.assertEqual(candidate.versions[0].review["checks"][0]["status"], "fail")
        final = candidate.versions[-1]
        self.assertEqual(final.content["questions"][0]["type"], "code_text")
        self.assertEqual(final.content["knowledge_point_ids"], ["v2-knowledge-point"])
        self.assertEqual(len(final.review["checks"]), 6)
        self.assertNotIn("public class Main", json.dumps(final.content))
        self.assertIn("public class Main", final.restricted["answers"][0]["value"])
        self.assertEqual(candidate.provenance, "simulated")
        self.assertEqual(self.run_candidate(), candidate)

    def test_replayed_candidate_requires_current_authorization(self):
        self.run_candidate()
        with self.assertRaisesRegex(CandidateError, "CONTEXT_REVOKED"):
            self.run_candidate(active=lambda: False)

    def test_artifact_modified_after_adapter_acceptance_is_rejected(self):
        self.run_candidate()
        path = self.storage / "job-one" / "output" / "generation.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(CandidateError, "ARTIFACT_MISMATCH"):
            self.run_candidate()

    def test_revocation_while_running_discards_the_candidate(self):
        states = iter((True, True, False))
        with self.assertRaisesRegex(CandidateError, "CONTEXT_REVOKED"):
            self.run_candidate(active=lambda: next(states, False))

    def test_fixture_does_not_claim_unsupported_target(self):
        with self.assertRaisesRegex(CandidateError, "FIXTURE_TARGET_UNSUPPORTED"):
            self.run_candidate(targets={"java.array": "array-id"})
