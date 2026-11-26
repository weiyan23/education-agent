"""Exercise recommendation expiry via the local synthetic Demo's real HTTP API."""
import sys
from datetime import datetime, timezone
from pathlib import Path

from test_v2_demo import Acceptance, ROOT


def verify(qa):
    for who in ("admin", "teacher_a", "teacher_b", "student_a", "student_b"):
        qa.login(who)
    course = qa.course["id"]
    group = qa.course["class_id"]
    student_b = qa.accounts["student_b"]["user_id"]
    qa.course["policy_id"] = qa.get("teacher_a", f"/courses/{course}")["policy_version_id"]
    lookup = f"/courses/{course}/recommendation-exercises?concept_id=java.condition"

    def match_for(who, bid):
        data = qa.get(who, lookup)
        item = next((item for item in data["items"] if item["binding_id"] == bid), None)
        qa.check("new synthetic binding is mapped for " + who, item is not None)
        qa.get(who, item["open_api_path"])
        return data["profile_version_id"], item["open_api_path"]

    # Creates a reviewed, published QA exercise and an effective synthetic grade.
    qa.rule_flow()
    bid = qa.objects["rule_binding_id"]
    source_binding = qa.get("teacher_a", "/bindings/" + bid)
    old_profile, old_path = match_for("student_a", bid)
    qa.check("existing profile before update", old_profile != "none")
    qa.mixed_flow()  # A separate assignment and teacher grade correction.
    qa.call("old URL after profile update", "student_a", "GET", old_path,
            expected=409, code="STALE_PROFILE")
    qa.call("old decision cannot refresh itself", "student_a", "GET",
            lookup + "&profile_version_id=" + old_profile, expected=409, code="STALE_PROFILE")
    new_profile, fresh_path = match_for("student_a", bid)
    qa.check("same exercise opens with new profile", new_profile != old_profile)

    def membership(active, scopes):
        body = {"command": "set_membership", "course_id": course, "usergroup_id": group,
                "student_id": student_b, "active": active, "reason": "任务三合成验收：临时班级资格"}
        qa.call("temporary membership active=" + str(active), "admin", "POST",
                "/admin/scopes/commands", body, etag=scopes["etag"], key=qa.key())

    scopes = qa.get("admin", "/admin/scopes")
    members = next(c["members"] for c in scopes["classes"] if c["usergroup_id"] == group)
    qa.check("student B starts outside A class",
             not any(m["student_id"] == student_b and m["membership_active"] for m in members))
    membership(True, scopes)
    try:
        # Recipients are frozen at assignment time, so assign after granting membership.
        shared = qa.make_binding(source_binding["resource_version_id"],
                                 source_binding["publication_event_id"],
                                 rubric=source_binding["rubric_version_id"], label="invalidation")
        _, student_b_path = match_for("student_b", shared["id"])
    finally:
        membership(False, qa.get("admin", "/admin/scopes"))
    qa.call("old URL after membership revocation", "student_b", "GET", student_b_path,
            expected=403, code="SCOPE_DENIED")

    qa.get("student_a", fresh_path)
    qa.call("withdraw only this run's QA exercise", "teacher_a", "POST",
            f'/resource-versions/{source_binding["resource_version_id"]}/publication-events',
            {"action": "withdraw", "usergroup_id": group, "expected_event_no": 1,
             "previous_event_id": source_binding["publication_event_id"], "validation_id": None,
             "review_id": None, "policy_version_id": qa.course["policy_id"],
             "reason": "任务三验收：撤回本次生成的合成练习。"}, etag='"1"', key=qa.key())
    qa.call("old URL after publication withdrawal", "student_a", "GET", fresh_path,
            expected=404, code="EXERCISE_UNAVAILABLE")
    qa.check("withdrawn exercise absent from new lookup",
             all(i["binding_id"] != bid for i in qa.get("student_a", lookup)["items"]))
    qa.completed = True
    qa.save()


if __name__ == "__main__":
    report = ROOT / ".runtime" / ("invalidation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json")
    qa = Acceptance("http://127.0.0.1:8138", report)
    try:
        verify(qa)
    except Exception as exc:
        print("FAIL", type(exc).__name__, "报告位置", report, file=sys.stderr)
        raise
    print("OK：画像更新、班级撤权、发布撤回后，旧入口均被拒绝。", report)
