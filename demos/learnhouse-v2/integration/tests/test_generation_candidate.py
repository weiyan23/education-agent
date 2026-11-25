import json
import io
from contextlib import redirect_stdout
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

INTEGRATION = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(INTEGRATION), str(INTEGRATION.parents[1])]

from generation_candidate import CandidateError, _FixtureResponses, run_fixture_candidate, build_real_snapshot, run_real_candidate


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

    def test_real_snapshot_contains_teacher_requirement_and_material(self):
        snapshot = build_real_snapshot(
            student_id="student-1", targets={"java.condition": "course-point"},
            requirement="出一道阈值判断题", source_text="输入温度后判断是否结冰",
            source_id="source-v1", source_hash="source-sha", source_kind="real_student",
        )
        self.assertEqual(snapshot.target_concept_ids, ("java.condition",))
        self.assertIn("出一道阈值判断题", snapshot.requirements)
        self.assertTrue(any("输入温度后判断是否结冰" in item for item in snapshot.requirements))
        self.assertEqual(snapshot.source_kind, "real_student")

    def test_real_snapshot_rejects_unknown_concept(self):
        with self.assertRaisesRegex(CandidateError, "UNKNOWN_RESEARCH_CONCEPT"):
            build_real_snapshot(student_id="student-1", targets={"java.unknown": "id"},
                                requirement="练习", source_text="资料",
                                source_id="source", source_hash="hash", source_kind="real_student")

    def test_real_execution_projects_cli_results_and_reuses_the_same_job(self):
        import generate_question
        from generation.workflow import DIMENSIONS

        targets = {"java.condition": "course-point"}
        snapshot = build_real_snapshot(student_id="student-1", targets=targets, requirement="练习结冰阈值",
                                       source_text="课程材料包含边界判断", source_id="source-v1",
                                       source_hash="source-sha", source_kind="real_student")
        config = self.storage / "mock.env"
        config.write_text("ZZZ_API_KEY=FAKE\nZZZ_BASE_URL=https://offline.invalid\nZZZ_MODEL=offline-model\n")
        responses = _FixtureResponses(DIMENSIONS)
        requests = []

        def fake_chat(url, key, payload, timeout):
            messages = payload["messages"]
            job = json.loads(messages[-1]["content"])
            requests.append(job)
            stage = "review_question" if job["candidate"] is not None else "generate_question"
            output = responses.complete_validated(stage, messages, lambda _: None)
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}]}

        def run_cli(argv, **options):
            self.assertEqual(argv[argv.index("--max-calls") + 1], "4")
            self.assertIn("--execute", argv)
            with patch.object(sys, "argv", argv[2:]), patch.object(generate_question, "send_chat", fake_chat):
                with redirect_stdout(io.StringIO()):
                    generate_question.main()
            return subprocess.CompletedProcess(argv, 0)

        kwargs = dict(actor_id="teacher", course_id="course", job_id="real-job", snapshot=snapshot,
                      targets=targets, source_id="source-v1", source_hash="source-sha",
                      env_file=config, max_calls=4)
        with patch("research_adapter.subprocess.run", side_effect=run_cli) as cli:
            candidate = run_real_candidate(self.storage, lambda: True, **kwargs)
            self.assertEqual(run_real_candidate(self.storage, lambda: True, **kwargs), candidate)
        self.assertEqual(cli.call_count, 1)
        self.assertEqual(len(requests), 4)
        self.assertIn("练习结冰阈值", json.dumps(requests[0], ensure_ascii=False))
        self.assertIn("课程材料包含边界判断", json.dumps(requests[0], ensure_ascii=False))
        self.assertEqual(candidate.provenance, "real")
        self.assertEqual(len(candidate.versions), 2)
        self.assertTrue(candidate.versions[-1].restricted["grading_notes"].startswith("模型六维审核"))
