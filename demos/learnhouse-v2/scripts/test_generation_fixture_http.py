"""Exercise the synthetic candidate through the actual local V2 HTTP service."""
import argparse
from pathlib import Path
from urllib.parse import urlparse

from test_v2_demo import Acceptance, ROOT


def run(qa):
    for actor in ("teacher_a", "teacher_b", "student_a", "student_b"):
        qa.login(actor)
    course_id = qa.course["id"]
    course = qa.get("teacher_a", f"/courses/{course_id}")
    qa.check("fixture enabled for the isolated course", any(
        entry["key"] == "generation_fixture" for entry in course["capabilities"]
    ))
    generation = f"/courses/{course_id}/generation-runs"
    source = next(point["id"] for point in course["knowledge_points"] if point["code"] == "java.condition")
    request = {
        "source_version_id": qa.course["source_version_id"], "policy_version_id": course["policy_version_id"],
        "knowledge_point_ids": [source], "mode": "research_generator", "run_mode": "fixture",
        "target_student_id": qa.accounts["student_a"]["user_id"], "max_calls": 4, "budget_limit": 0,
        "max_attempts": 1, "requirement": "合成验收：练习 Java 条件判断",
    }
    qa.call("student cannot generate", "student_a", "POST", generation, request, key=qa.key(),
            etag=course["etag"], expected=403)
    qa.call("another teacher cannot generate", "teacher_b", "POST", generation, request, key=qa.key(),
            etag=course["etag"], expected=403)
    qa.call("generation cannot target an out-of-scope student", "teacher_a", "POST", generation,
            {**request, "target_student_id": qa.accounts["student_b"]["user_id"]},
            key=qa.key(), etag=course["etag"], expected=403)
    key = qa.key()
    task = qa.call("adapter produces a simulated candidate", "teacher_a", "POST", generation, request,
                   key=key, etag=course["etag"])
    same_task = qa.call("idempotent replay", "teacher_a", "POST", generation, request,
                        key=key, etag=course["etag"])
    qa.check("same immutable task and candidate", task["id"] == same_task["id"]
             and task["result_refs"] == same_task["result_refs"] and task["run_mode"] == "fixture")
    qa.objects.update(task["result_refs"])
    resource = "/resources/" + task["result_refs"]["resource_id"]
    candidate = qa.get("teacher_a", resource)
    version = candidate["version"]
    qa.check("candidate has original and revised versions", version["version"] == 2
             and version["forced_review"] and version["provenance"] == "simulated"
             and [old["version"] for old in candidate["candidate_history"]] == [1])
    qa.call("student cannot read unpublished candidate", "student_a", "GET", resource, expected=403)
    version_path = "/resource-versions/" + version["id"]
    validation = qa.call("deterministic validation", "teacher_a", "POST", version_path + "/checks",
                         {"check_kind": "deterministic", "policy_version_id": course["policy_version_id"]},
                         etag=version["etag"], key=qa.key())
    qa.check("candidate passed structural validation", validation["verdict"] == "pass")
    publication = {
        "action": "publish", "usergroup_id": qa.course["class_id"], "expected_event_no": 0,
        "previous_event_id": None, "validation_id": validation["id"], "review_id": None,
        "policy_version_id": course["policy_version_id"], "reason": "合成联调验收",
    }
    qa.call("unreviewed candidate is blocked", "teacher_a", "POST", version_path + "/publication-events",
            publication, etag=version["etag"], key=qa.key(), expected=409, code="HUMAN_REVIEW_REQUIRED")
    review = qa.call("authorized teacher approves exact version", "teacher_a", "POST", version_path + "/checks",
                     {"check_kind": "human_review", "policy_version_id": course["policy_version_id"],
                      "validation_id": validation["id"], "verdict": "approve", "reason": "人工验收合成候选"},
                     etag=version["etag"], key=qa.key())
    published = qa.call("teacher publishes reviewed version", "teacher_a", "POST", version_path + "/publication-events",
                        {**publication, "review_id": review["id"]}, etag=version["etag"], key=qa.key())
    qa.objects["publication_event_id"] = published["id"]
    student_view = qa.get("student_a", resource)
    qa.check("student receives public Java question only", student_view["version"]["id"] == version["id"]
             and student_view["content"]["questions"][0]["type"] == "code_text"
             and "restricted_content" not in student_view and "candidate_history" not in student_view)
    qa.call("another class cannot read published exercise", "student_b", "GET", resource, expected=403)
    qa.completed = True
    qa.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8138")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    url = urlparse(args.base_url)
    if url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost"):
        parser.error("Use the isolated local V2 demo")
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / ".runtime"):
        parser.error("Specify a new output file under .runtime")
    run(Acceptance(args.base_url, output))


if __name__ == "__main__":
    main()
