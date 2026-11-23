"""Offline Java candidate generation and projection for the V2 business layer."""
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys

from research_adapter import AdapterError, CurrentContext, JobContext, Principal, ResearchAdapter, Snapshot

REPOSITORY = Path(__file__).resolve().parents[3]


class CandidateError(ValueError):
    pass


@dataclass(frozen=True)
class CandidateVersion:
    content: dict
    restricted: dict
    question_hash: str
    review: dict


@dataclass(frozen=True)
class Candidate:
    versions: tuple[CandidateVersion, ...]
    artifact_hash: str
    provenance: str = "simulated"


def _workflow():
    if str(REPOSITORY) not in sys.path:
        sys.path.append(str(REPOSITORY))
    from generation.workflow import run_generation, validate_question, validate_review, DIMENSIONS
    from profiling.run_store import RunStore, fingerprint
    from recommendation.context import prepare_context, build_generation_request
    return run_generation, validate_question, validate_review, DIMENSIONS, RunStore, fingerprint, prepare_context, build_generation_request


class _FixtureResponses:
    def __init__(self, dimensions):
        self.dimensions = dimensions
        self.draft_number = 0

    def complete_validated(self, stage, prompt, validator):
        if stage == "generate_question":
            self.draft_number += 1
            result = {
                "outcome": "question", "reason": "合成样例用于验证条件判断题的流转。",
                "title": "温度提醒", "statement": "温度达到 30 度时输出 HOT，否则输出 OK。",
                "input_format": "输入一个整数温度。", "output_format": "输出 HOT 或 OK。",
                "constraints": "-50 <= 温度 <= 60",
                "samples": [{"input": "30", "output": "OK" if self.draft_number == 1 else "HOT",
                             "explanation": "温度达到 30 度，应输出 HOT。"},
                            {"input": "29", "output": "OK", "explanation": "未达到阈值。"}],
                "reference_code": "import java.util.Scanner;\npublic class Main {\n"
                                  "    public static void main(String[] args) {\n"
                                  "        int t = new Scanner(System.in).nextInt();\n"
                                  '        System.out.println(t >= 30 ? "HOT" : "OK");\n    }\n}',
                "explanation": "比较输入与阈值，等于阈值时进入 HOT 分支。",
                "coverage": [{"concept_id": "java.condition", "location": "阈值条件判断"}],
            }
        else:
            failed = self.draft_number == 1
            result = {"question_sha256": json.loads(prompt[-1]["content"])["question_sha256"],
                      "checks": [{
                          "dimension": dimension,
                          "status": "fail" if failed and index == 0 else (
                              "not_applicable" if dimension == "combination" else "pass"),
                          "reason": "固定模拟检查。初稿的阈值样例输出错误。" if failed and index == 0 else
                                    "固定模拟检查，用于验证版本与审核流转。",
                          "location": "samples[0]" if failed and index == 0 else "整个候选",
                      } for index, dimension in enumerate(self.dimensions)]}
        validator(result)
        return result


def _project(question, review, question_hash, targets, source_id, source_hash):
    ids = list(targets.values())
    samples = "\n\n".join(
        f"样例 {number}\n输入\n{sample['input']}\n输出\n{sample['output']}\n{sample['explanation']}"
        for number, sample in enumerate(question["samples"], 1)
    )
    prompt = "\n\n".join((question["statement"], "输入格式\n" + question["input_format"],
                          "输出格式\n" + question["output_format"], "约束\n" + question["constraints"], samples))
    content = {
        "title": question["title"], "body_text": "合成样例 · 固定模型响应",
        "questions": [{"item_id": "q1", "type": "code_text", "prompt": prompt, "language": "java",
                       "maximum_score": 100, "knowledge_point_ids": ids}],
        "knowledge_point_ids": ids,
        "source_refs": [{"resource_version_id": source_id, "locator": "联调资料版本", "content_hash": source_hash}],
    }
    restricted = {
        "answers": [{"item_id": "q1", "value": question["reference_code"], "rationale": question["explanation"]}],
        "grading_notes": "固定模拟的六维审核\n" + "\n".join(
            f"{check['dimension']} · {check['status']} · {check['location']} · {check['reason']}"
            for check in review["checks"]
        ),
    }
    return CandidateVersion(content, restricted, question_hash, review)


def run_fixture_candidate(storage, active, *, actor_id, course_id, student_id, job_id,
                          targets, requirement, source_id, source_hash):
    if set(targets) != {"java.condition"}:
        raise CandidateError("FIXTURE_TARGET_UNSUPPORTED")
    (generate, validate_question, validate_review, dimensions, Store, fingerprint,
     prepare, build) = _workflow()
    catalog = json.loads((REPOSITORY / "config" / "java-basics.json").read_text(encoding="utf-8"))
    catalog["concepts"] = {code: catalog["concepts"][code] for code in targets}
    catalog.update(prerequisites=[], associations=[], relation_sources={})
    target_ids = tuple(targets)
    principal = Principal(actor_id, course_id, frozenset({student_id}), frozenset(target_ids), frozenset({"generate"}))
    snapshot = Snapshot(student_id, catalog, target_ids, "test_fixture", target_ids,
                        requirements=(requirement, f"资料版本 {source_id}，哈希 {source_hash}"))
    job = JobContext(job_id, job_id, 0, 1)
    context = prepare(catalog, student_id=student_id, allowed_concept_ids=list(target_ids),
                      goal_concept_ids=list(target_ids), requirements=list(snapshot.requirements), profile=None)
    request = build(context, selected_concept_ids=list(target_ids), reason="教师直接指定目标与要求")

    def fixture_transport(argv, *, cwd, timeout):
        output = Path(argv[argv.index("--output") + 1])
        with Store(output, {"kind": "synthetic-v2-generation", "request": fingerprint(request)}, 4) as store:
            generate(store, _FixtureResponses(dimensions), request)
        return subprocess.CompletedProcess(argv, 0)

    try:
        adapter = ResearchAdapter(storage, lambda *_: CurrentContext(0, 1, active()),
                                  repository=REPOSITORY, interpreter=Path(sys.executable),
                                  transport=fixture_transport, fixture_transport=True)
        result = adapter.run("generate", principal, snapshot, job, mode="fixture", max_calls=4)
        if result.status != "needs_review" or result.model_connection != "not_verified_fixture":
            raise CandidateError(result.error_code or "FIXTURE_CANDIDATE_UNAVAILABLE")
        artifact, = result.artifact_refs
        report = adapter.read_generation(principal, snapshot, job, artifact)
        if report["request_sha256"] != fingerprint(request):
            raise CandidateError("ARTIFACT_MISMATCH")
        versions = []
        for version in report["versions"]:
            question, review = version["question"], version["review"]
            validate_question(request, question)
            validate_review(request, question, review)
            if version["version"] != len(versions) + 1 or version["question_sha256"] != fingerprint(question):
                raise CandidateError("ARTIFACT_MISMATCH")
            versions.append(_project(question, review, version["question_sha256"], targets, source_id, source_hash))
        if not 1 <= len(versions) <= 2 or any(
            check["status"] == "fail" for check in versions[-1].review["checks"]
        ):
            raise CandidateError("FIXTURE_CANDIDATE_UNAVAILABLE")
        return Candidate(tuple(versions), artifact.sha256)
    except AdapterError as error:
        raise CandidateError(error.code) from error
