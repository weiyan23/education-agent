"""Real HTTP acceptance checks for the isolated, synthetic V2 Demo.

No auth shortcuts, direct DB test writes, external model calls or code execution.
Each run creates clearly labelled QA assignments and keeps immutable history.
Private credentials/cookies/answer bodies never appear in the report or stdout.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
from http.cookiejar import CookieJar
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import build_opener, HTTPCookieProcessor, ProxyHandler, Request
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "/api/v1/education"
ORIGIN = "http://localhost:3300"


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.cookies = CookieJar()
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(self.cookies))

    def request(self, method, path, body=None, *, etag=None, key=None, origin=ORIGIN, form=False, headers=None):
        h = dict(headers or {})
        if method not in ("GET", "HEAD") and origin is not None:
            h["Origin"] = origin
        data = None
        if body is not None:
            data = (urlencode(body) if form else json.dumps(body, ensure_ascii=False)).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        if etag is not None:
            h["If-Match"] = etag
        if key is not None:
            h["Idempotency-Key"] = key
        request = Request(self.base + path, data=data, headers=h, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                status, raw = response.status, response.read()
        except HTTPError as exc:
            status, raw = exc.code, exc.read()
        try:
            value = json.loads(raw) if raw else None
        except (ValueError, UnicodeError):
            value = {"non_json_response": True}
        return status, value


class Acceptance:
    def __init__(self, base, output):
        self.base, self.output = base, output.resolve()
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        private = ROOT / ".runtime/demo-accounts.local.json"
        if private.stat().st_mode & 0o077:
            raise RuntimeError("Private credential file must have mode 0600")
        self.seed = json.loads(private.read_text())
        self.accounts = {a["key"]: a for a in self.seed["accounts"]}
        self.course = self.seed["objects"]["courses"][0]
        self.other_course = self.seed["objects"]["courses"][1]
        self.clients = {key: Client(base) for key in self.accounts}
        self.checks, self.objects = [], {}

    def key(self):
        return "qa-" + self.run_id + "-" + uuid4().hex[:12]

    @staticmethod
    def code(value):
        detail = value.get("detail") if isinstance(value, dict) else None
        return detail.get("code") if isinstance(detail, dict) else None

    def check(self, name, condition, **metadata):
        self.checks.append({"name": name, "passed": bool(condition), **metadata})
        print(("PASS " if condition else "FAIL ") + name, flush=True)
        self.save()
        if not condition:
            raise AssertionError(name)

    def call(self, name, who, method, path, body=None, *, expected=200, code=None, **options):
        client = who if isinstance(who, Client) else self.clients[who]
        status, value = client.request(method, path if path.startswith("/api/") else PREFIX + path, body, **options)
        expected = (expected,) if isinstance(expected, int) else tuple(expected)
        actual_code = self.code(value)
        self.check(name, status in expected and (code is None or actual_code == code), status=status,
                   expected_status=list(expected), error_code=actual_code, method=method, path=path)
        return value

    def get(self, who, path):
        return self.call("read " + path, who, "GET", path)

    def save(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        report = {"run_id": self.run_id, "timestamp": datetime.now(timezone.utc).isoformat(),
                  "base_url": self.base, "provenance": "demo", "authentication": "real upstream cookie login",
                  "external_model_calls": 0, "student_code_executions": 0,
                  "passed": sum(c["passed"] for c in self.checks), "failed": sum(not c["passed"] for c in self.checks),
                  "completed": getattr(self, "completed", False), "checks": self.checks, "created_objects": self.objects}
        self.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    def login(self, key, target=None):
        account = self.accounts[key]
        return self.call("real login " + key, target or key, "POST", "/api/v1/auth/login",
                         {"username": account["email"], "password": account["password"], "org_slug": "default"}, form=True)

    def baseline(self):
        anon = Client(self.base)
        self.call("anonymous session blocked", anon, "GET", "/session", expected=401)
        for key, account in self.accounts.items():
            self.login(key)
            value = self.call("session role " + key, key, "GET", "/session")
            expected = "administrator" if account["role"] == "admin" else account["role"]
            self.check("server assigned role " + key, value["principal"]["role_views"] == [expected])
            self.check("capability honesty " + key, all(c["status"] == "unconfigured" for c in value["capabilities"]))
        self.call("write without Origin rejected", "student_a", "DELETE", "/api/v1/auth/logout", origin=None, expected=403)
        self.call("write foreign Origin rejected", "student_a", "DELETE", "/api/v1/auth/logout", origin="https://evil.example", expected=403)
        for key, course in (("student_a", self.course), ("student_b", self.other_course), ("teacher_a", self.course), ("teacher_b", self.other_course)):
            result = self.call("course visibility " + key, key, "GET", "/courses")
            self.check("only assigned course " + key, [r["id"] for r in result["items"]] == [course["id"]])
        for key in ("student_b", "teacher_b", "admin"):
            self.call("course body isolation " + key, key, "GET", f'/courses/{self.course["id"]}', expected=403)
            self.call("resource body isolation " + key, key, "GET", f'/resources/{self.course["source_resource_id"]}', expected=403)
        self.call("legacy course API blocked", "teacher_a", "GET", f'/api/v1/courses/{self.course["course_uuid"]}', expected=403)
        self.call("legacy activities API blocked", "teacher_a", "GET", f'/api/v1/activities/{self.course["activity_uuid"]}', expected=403)
        self.call("student cannot access administration", "student_a", "GET", "/admin/scopes", expected=403)
        self.call("teacher cannot access administration", "teacher_a", "GET", "/admin/scopes", expected=403)
        for route in ("scopes", "policies", "operations", "audit"):
            result = self.call("admin metadata " + route, "admin", "GET", "/admin/" + route)
            forbidden = ('"answer_snapshot"', '"restricted_content"', '"body_text"', '"draft"')
            self.check("no teaching bodies in admin " + route, not any(s in json.dumps(result) for s in forbidden))
        original = self.get("student_a", "/bindings/" + self.course["binding_id"])
        self.check("answer key hidden before effective grade", "answer_key" not in original)
        self.call("client identity header cannot escalate", "student_b", "GET", f'/courses/{self.course["id"]}',
                  expected=403, headers={"X-Actor-Id": self.accounts["teacher_a"]["principal_id"], "X-Role": "teacher"})

    def make_binding(self, resource_version, publication, rubric=None, new_rubric=None, label="mixed"):
        course = self.get("teacher_a", f'/courses/{self.course["id"]}')
        now = datetime.now(timezone.utc)
        body = {"assignment_id": None, "title": f"验收演示 {label} {self.run_id}", "description": "真实 API 验收创建；仅含合成演示数据。",
                "resource_version_id": resource_version, "rubric_version_id": rubric, "new_rubric": new_rubric,
                "policy_version_id": self.course["policy_id"], "publication_event_id": publication,
                "usergroup_id": self.course["class_id"], "opens_at": (now - timedelta(minutes=5)).isoformat(),
                "due_at": (now + timedelta(days=7)).isoformat(), "max_attempts": 2, "answer_release": "after_effective", "allow_review": True}
        result = self.call("create " + label + " assignment", "teacher_a", "POST", f'/courses/{self.course["id"]}/bindings', body, etag=course["etag"], key=self.key())
        self.objects[label + "_binding_id"] = result["id"]
        return result

    def mixed_flow(self):
        resource = self.get("teacher_a", "/resources/" + self.course["exercise_resource_id"])
        publication = next(p["id"] for p in resource["publication_events"] if p["action"] == "publish" and p["usergroup_id"] == self.course["class_id"])
        binding = self.make_binding(self.course["exercise_version_id"], publication, rubric=self.course["rubric_id"])
        bid = binding["id"]
        self.call("student B cannot read A assignment", "student_b", "GET", "/bindings/" + bid, expected=403)
        own = self.get("student_a", "/bindings/" + bid)
        start = {"recipient_id": own["recipient"]["id"]}
        self.call("teacher cannot start private attempt", "teacher_a", "POST", f"/bindings/{bid}/attempts", start, key=self.key(), etag=binding["etag"], expected=403)
        attempt = self.call("student starts attempt", "student_a", "POST", f"/bindings/{bid}/attempts", start, key=self.key(), etag=own["etag"])
        aid = attempt["id"]
        self.objects["mixed_attempt_id"] = aid
        self.call("teacher cannot read private draft", "teacher_a", "GET", "/attempts/" + aid, expected=403)
        self.call("student B cannot read A draft", "student_b", "GET", "/attempts/" + aid, expected=403)
        draft = {"expected_version": attempt["draft_revision"], "draft": {"answers": [{"item_id": "q1", "value": True}, {"item_id": "q2", "value": "演示解释：条件为真时执行对应分支。", "language": "plain_text"}]}}
        self.call("missing concurrency header rejected", "student_a", "PUT", f"/attempts/{aid}/draft", draft, key=self.key(), expected=428)
        key, old_etag = self.key(), attempt["etag"]
        saved = self.call("save draft", "student_a", "PUT", f"/attempts/{aid}/draft", draft, key=key, etag=old_etag)
        replay = self.call("replay draft idempotently", "student_a", "PUT", f"/attempts/{aid}/draft", draft, key=key, etag=old_etag)
        self.check("draft replay keeps same revision", replay["draft_revision"] == saved["draft_revision"])
        altered = {**draft, "draft": {"answers": [{"item_id": "q1", "value": False}]}}
        self.call("idempotency key payload conflict rejected", "student_a", "PUT", f"/attempts/{aid}/draft", altered, key=key, etag=old_etag, expected=409, code="IDEMPOTENCY_CONFLICT")
        self.call("stale draft rejected", "student_a", "PUT", f"/attempts/{aid}/draft", draft, key=self.key(), etag=old_etag, expected=409, code="VERSION_CONFLICT")
        submit = {"expected_version": saved["draft_revision"], "answer_hash": saved["draft_hash"]}
        self.call("incorrect answer hash rejected", "student_a", "POST", f"/attempts/{aid}/submissions", {**submit, "answer_hash": "0" * 64}, key=self.key(), etag=saved["etag"], expected=409)
        submit_key = self.key()
        receipt = self.call("submit mixed answers", "student_a", "POST", f"/attempts/{aid}/submissions", submit, key=submit_key, etag=saved["etag"])
        replay = self.call("submit replay returns receipt", "student_a", "POST", f"/attempts/{aid}/submissions", submit, key=submit_key, etag=saved["etag"])
        self.check("only one immutable submission receipt", replay["submission_id"] == receipt["submission_id"] and replay["receipt_id"] == receipt["receipt_id"])
        self.call("new key cannot resubmit closed attempt", "student_a", "POST", f"/attempts/{aid}/submissions", submit, key=self.key(), etag=saved["etag"], expected=409)
        self.call("submitted draft cannot be overwritten", "student_a", "PUT", f"/attempts/{aid}/draft", {**draft, "expected_version": saved["draft_revision"]}, key=self.key(), etag=saved["etag"], expected=409)
        sid = receipt["submission_id"]
        self.objects["mixed_submission_id"] = sid
        for who in ("student_b", "teacher_b", "admin"):
            self.call("submission body isolation " + who, who, "GET", "/submissions/" + sid, expected=403)
        student = self.get("student_a", "/submissions/" + sid)
        self.check("mixed answers await teacher", student["effective_grade"] is None and student["receipt"]["evaluation_state"] == "needs_teacher")
        snapshot_digest = hashlib.sha256(json.dumps(student["answer_snapshot"], sort_keys=True).encode()).hexdigest()
        teacher = self.get("teacher_a", "/submissions/" + sid)
        self.call("unconfigured AI evaluation fails closed", "teacher_a", "POST", f"/submissions/{sid}/evaluations", {"source": "ai_suggestion", "budget_limit": 0}, key=self.key(), etag=teacher["etag"], expected=503, code="MODEL_NOT_CONFIGURED")
        grade1 = self.evaluate_and_apply(sid, teacher, 80)
        current = self.get("teacher_a", "/submissions/" + sid)
        self.check("teacher grade becomes revision one", current["effective_grade"]["revision"] == 1 and current["effective_grade"]["score"] == 80)
        eval_a = self.evaluate(sid, current, 85)
        eval_b = self.evaluate(sid, current, 90)
        duplicate_teacher = Client(self.base)
        self.login("teacher_a", duplicate_teacher)
        commands = [{"evaluation_id": evaluation["id"], "expected_version": 1, "supersedes_id": grade1["id"], "reason": "验收：并发修改成绩，确认仅一条新版本生效。"} for evaluation in (eval_a, eval_b)]
        clients = [self.clients["teacher_a"], duplicate_teacher]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(c.request, "POST", PREFIX + f"/submissions/{sid}/grade-revisions", body,
                                   key=self.key(), etag=current["etag"]) for c, body in zip(clients, commands)]
            responses = [future.result() for future in futures]
        self.check("concurrent grade correction has one winner", sorted(s for s, _ in responses) == [200, 409], statuses=[s for s, _ in responses], error_codes=[self.code(v) for _, v in responses])
        after = self.get("student_a", "/submissions/" + sid)
        history = after["grade_history"]
        self.check("grade history retained as supersession chain", len(history) == 2 and history[0]["supersedes_id"] == history[1]["id"] and history[1]["score"] == 80)
        self.check("grading leaves submission answers immutable", hashlib.sha256(json.dumps(after["answer_snapshot"], sort_keys=True).encode()).hexdigest() == snapshot_digest)
        own_binding = self.get("student_a", "/bindings/" + bid)
        self.check("answer released after effective result", "answer_key" in own_binding)
        review = self.call("student requests grade review", "student_a", "POST", f"/submissions/{sid}/reviews", {"grade_revision_id": history[0]["id"], "request_text": "验收演示：请核对解释题评分依据。"}, key=self.key(), etag=after["etag"])
        resolved = self.call("teacher resolves review retaining grade", "teacher_a", "POST", f'/reviews/{review["id"]}/resolution', {"decision": "retain", "reason": "验收演示：已核对量规，维持本次有效成绩。", "evaluation_id": None, "expected_grade_revision": history[0]["revision"]}, key=self.key(), etag=after["etag"])
        self.check("review resolution stored", resolved["status"] == "retained")
        state = self.get("student_a", f'/courses/{self.course["id"]}/students/{self.accounts["student_a"]["user_id"]}/learning-state')
        self.check("profile honestly identifies rule statistics", state["method_kind"] == "rule_statistics" and state["runtime_capability_status"] == "unconfigured")
        self.check("grade correction triggers fresh profile", state["trigger_type"] == "grade_corrected" and state["trigger_grade_id"] == history[0]["id"])
        self.call("student cannot read another student learning state", "student_b", "GET", f'/courses/{self.course["id"]}/students/{self.accounts["student_a"]["user_id"]}/learning-state', expected=403)

    def evaluate(self, sid, current, score):
        payload = {"source": "teacher", "score": score, "maximum_score": 100,
                   "item_scores": [{"item_id": "q1", "score": 50, "feedback": "判断正确。"}, {"item_id": "q2", "score": score - 50, "feedback": "按量规核对解释。"}],
                   "feedback": "验收演示人工评分。", "reason": "按同一版本量规核对合成答案。", "based_on_ai_evaluation_id": None}
        return self.call("teacher evaluation " + str(score), "teacher_a", "POST", f"/submissions/{sid}/evaluations", payload, key=self.key(), etag=current["etag"])

    def evaluate_and_apply(self, sid, current, score):
        evaluation = self.evaluate(sid, current, score)
        prior = current["effective_grade"]
        return self.call("apply teacher grade " + str(score), "teacher_a", "POST", f"/submissions/{sid}/grade-revisions", {"evaluation_id": evaluation["id"], "expected_version": prior["revision"] if prior else 0, "supersedes_id": prior["id"] if prior else None, "reason": "验收演示：教师明确确认生效。"}, key=self.key(), etag=current["etag"])

    def rule_flow(self):
        c = self.get("teacher_a", f'/courses/{self.course["id"]}')
        request = {"source_version_id": self.course["source_version_id"], "policy_version_id": self.course["policy_id"], "knowledge_point_ids": [k["id"] for k in self.course["knowledge_points"]], "mode": "rule_template", "requirement": "验收演示：根据资料生成两个判断题。", "budget_limit": 0, "max_attempts": 1}
        research = {**request, "mode": "research_generator", "target_student_id": self.accounts["student_a"]["user_id"], "max_calls": 1, "run_mode": "preview"}
        self.call("research generation fails closed before model", "teacher_a", "POST", f'/courses/{self.course["id"]}/generation-runs', research, key=self.key(), etag=c["etag"], expected=503, code="RESEARCH_NOT_CONNECTED")
        task = self.call("deterministic template generation", "teacher_a", "POST", f'/courses/{self.course["id"]}/generation-runs', request, key=self.key(), etag=c["etag"])
        self.check("rule template completes without model", task["status"] == "succeeded")
        self.objects["rule_task_id"] = task["id"]
        refs = task["result_refs"]
        resource = self.get("teacher_a", "/resources/" + refs["resource_id"])
        version = resource["version"]
        self.check("generated resource labelled demo rules", version["generation_mode"] == "rule_template" and version["provenance"] == "demo")
        check = self.call("server deterministic validation", "teacher_a", "POST", f'/resource-versions/{version["id"]}/checks', {"check_kind": "deterministic", "policy_version_id": self.course["policy_id"]}, key=self.key(), etag=version["etag"])
        self.check("generated rule content passes validation", check["verdict"] == "pass")
        unpublished = {"action": "publish", "usergroup_id": self.course["class_id"], "expected_event_no": 0, "previous_event_id": None, "validation_id": check["id"], "review_id": None, "policy_version_id": self.course["policy_id"], "reason": "验收：强制人工审核策略应拒绝未审核发布。"}
        self.call("current review policy blocks unreviewed rule publication", "teacher_a", "POST", f'/resource-versions/{version["id"]}/publication-events', unpublished, key=self.key(), etag=version["etag"], expected=409)
        review = self.call("teacher approves rule material under review policy", "teacher_a", "POST", f'/resource-versions/{version["id"]}/checks', {"check_kind": "human_review", "policy_version_id": self.course["policy_id"], "validation_id": check["id"], "verdict": "approve", "reason": "验收演示：教师依据资料审核题目及答案。"}, key=self.key(), etag=version["etag"])
        publication = self.call("publish validated and reviewed rule content", "teacher_a", "POST", f'/resource-versions/{version["id"]}/publication-events', {"action": "publish", "usergroup_id": self.course["class_id"], "expected_event_no": 0, "previous_event_id": None, "validation_id": check["id"], "review_id": review["id"], "policy_version_id": self.course["policy_id"], "reason": "验收演示规则资料发布。"}, key=self.key(), etag=version["etag"])
        criteria = [{"criterion_id": "c" + str(i), "item_id": q["item_id"], "description": "判断题确定性比对", "maximum_score": q["maximum_score"], "knowledge_point_ids": q["knowledge_point_ids"]} for i, q in enumerate(resource["content"]["questions"], 1)]
        binding = self.make_binding(version["id"], publication["id"], new_rubric={"criteria": criteria, "maximum_score": 100}, label="rule")
        lookup = f'/courses/{self.course["id"]}/recommendation-exercises'
        matches = self.get("student_a", lookup + "?concept_id=java.condition")
        match = next(item for item in matches["items"] if item["binding_id"] == binding["id"])
        self.check("target mapping pins the published exercise version",
                   match["resource_version_id"] == version["id"] and
                   match["publication_event_id"] == publication["id"])
        self.call("second student cannot open mapped exercise", "student_b", "GET",
                  match["open_api_path"], expected=403)
        missing = self.get("student_a", lookup + "?concept_id=java.condition&concept_id=absent")
        self.check("unmapped combination has no exercises",
                   missing["state"] == "no_content" and not missing["items"])
        own = self.get("student_a", match["open_api_path"])
        attempt = self.call("student starts rule attempt", "student_a", "POST", f'/bindings/{binding["id"]}/attempts', {"recipient_id": own["recipient"]["id"]}, key=self.key(), etag=own["etag"])
        answers = [{"item_id": q["item_id"], "value": True} for q in resource["content"]["questions"]]
        saved = self.call("save rule answers", "student_a", "PUT", f'/attempts/{attempt["id"]}/draft', {"expected_version": attempt["draft_revision"], "draft": {"answers": answers}}, key=self.key(), etag=attempt["etag"])
        receipt = self.call("submit rule answers", "student_a", "POST", f'/attempts/{attempt["id"]}/submissions', {"expected_version": saved["draft_revision"], "answer_hash": saved["draft_hash"]}, key=self.key(), etag=saved["etag"])
        self.objects["rule_submission_id"] = receipt["submission_id"]
        submission = self.get("student_a", "/submissions/" + receipt["submission_id"])
        self.check("objective answers automatically get rule grade", submission["effective_grade"]["judgment_source"] == "rule" and submission["effective_grade"]["score"] == 100)
        self.check("rule submission provenance remains demo", receipt["provenance"] == "demo")

    def note_flow(self):
        path = f'/courses/{self.course["id"]}/notes'
        notes = self.get("student_a", path)
        self.call("teacher cannot enumerate private notes", "teacher_a", "GET", path, expected=403)
        self.call("student B cannot enumerate A course notes", "student_b", "GET", path, expected=403)
        self.call("create private synthetic note", "student_a", "POST", path,
                  {"activity_id": self.course["activity_id"], "content_text": "验收演示笔记：条件判断先于分支执行。"}, etag=notes["etag"], key=self.key())
        notes = self.get("student_a", path)
        note_id = notes["items"][0]["id"]
        self.objects["private_note_id"] = note_id
        self.call("teacher cannot read unshared note", "teacher_a", "GET", f"/notes/{note_id}/shared-content", expected=403, code="NOTE_NOT_SHARED")
        session = self.get("student_a", "/session")
        command = {"command": "grant", "purpose": "teacher_read", "grantee_id": self.accounts["teacher_a"]["principal_id"], "expected_version": 0, "permission_id": None, "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
        self.call("explicit teacher note sharing", "student_a", "POST", f"/notes/{note_id}/permissions", command, etag=session["own_note_permissions_etag"], key=self.key())
        shared = self.get("teacher_a", f"/notes/{note_id}/shared-content")
        self.check("shared note includes consent reference", bool(shared["permission_id"]) and shared["consent_revision"] == 1)
        for who in ("teacher_b", "student_b", "admin"):
            self.call("shared note remains scoped " + who, who, "GET", f"/notes/{note_id}/shared-content", expected=403)
        session = self.get("student_a", "/session")
        active = [p for p in session["own_note_permissions"] if p["note_id"] == note_id and not p["revoked_at"]]
        self.check("teacher read consent does not grant profile use", len(active) == 1 and active[0]["purpose"] == "teacher_read")
        revoke = {**command, "command": "revoke", "expected_version": 1, "permission_id": shared["permission_id"]}
        self.call("revoke note permission", "student_a", "POST", f"/notes/{note_id}/permissions", revoke, etag=session["own_note_permissions_etag"], key=self.key())
        self.call("revocation immediately hides note from teacher", "teacher_a", "GET", f"/notes/{note_id}/shared-content", expected=403, code="NOTE_NOT_SHARED")

    def source_gates(self):
        resource = self.get("teacher_a", "/resources/" + self.course["exercise_resource_id"])
        path = "/resources/" + self.course["exercise_resource_id"] + "/versions"
        base = {"expected_version": resource["version"]["version"], "policy_version_id": self.course["policy_id"],
                "content": resource["content"], "restricted_content": resource["restricted_content"], "reason": "验收演示：拒绝非法引用或客观题答案。"}
        other_source = deepcopy(base)
        other_source["content"]["source_refs"] = [{"resource_version_id": self.other_course["source_version_id"], "locator": "验收引用", "content_hash": "0" * 64}]
        self.call("resource rejects cross-course source reference", "teacher_a", "POST", path, other_source, etag=resource["version"]["etag"], key=self.key(), expected=403)
        wrong_hash = deepcopy(base)
        wrong_hash["content"]["source_refs"] = [{"resource_version_id": self.course["source_version_id"], "locator": "验收引用", "content_hash": "0" * 64}]
        self.call("resource rejects source hash mismatch", "teacher_a", "POST", path, wrong_hash, etag=resource["version"]["etag"], key=self.key(), expected=409, code="SOURCE_HASH_MISMATCH")
        wrong_boolean = deepcopy(base)
        wrong_boolean["restricted_content"]["answers"][0]["value"] = "true"
        self.call("resource rejects string boolean answer key", "teacher_a", "POST", path, wrong_boolean, etag=resource["version"]["etag"], key=self.key(), expected=422, code="INVALID_ANSWER_KEY")
        wrong_choice = deepcopy(base)
        wrong_choice["content"]["questions"][0].update(type="single_choice", choices=[{"key": "a", "label": "选项一"}, {"key": "b", "label": "选项二"}])
        wrong_choice["restricted_content"]["answers"][0]["value"] = "not-an-option"
        self.call("resource rejects answer absent from choices", "teacher_a", "POST", path, wrong_choice, etag=resource["version"]["etag"], key=self.key(), expected=422, code="INVALID_ANSWER_KEY")
        # Regression check for the successful branch affected by answer validation.
        self.rule_flow()

    def admin_flow(self):
        scoped = self.get("admin", "/admin/scopes")
        now = datetime.now(timezone.utc)
        grant = {"command": "grant", "principal_id": self.accounts["teacher_b"]["principal_id"], "course_id": self.course["id"],
                 "usergroup_id": self.course["class_id"], "student_id": None, "action": "content.read",
                 "starts_at": (now - timedelta(seconds=2)).isoformat(), "expires_at": (now + timedelta(hours=1)).isoformat(), "reason": "验收演示临时跨班授权，测试结束撤回。"}
        self.call("administrator cannot grant own teaching access", "admin", "POST", "/admin/scopes/commands", {**grant, "principal_id": self.accounts["admin"]["principal_id"]}, etag=scoped["etag"], key=self.key(), expected=403, code="SELF_ESCALATION_DENIED")
        grants = []
        try:
            result = self.call("administrator grants temporary course reading", "admin", "POST", "/admin/scopes/commands", grant, etag=scoped["etag"], key=self.key())
            grants.extend(result["result_ids"])
            self.call("stale administrative scope update rejected", "admin", "POST", "/admin/scopes/commands", {**grant, "action": "content.edit"}, etag=scoped["etag"], key=self.key(), expected=409, code="VERSION_CONFLICT")
            scoped = self.get("admin", "/admin/scopes")
            result = self.call("administrator grants temporary content editing", "admin", "POST", "/admin/scopes/commands", {**grant, "action": "content.edit"}, etag=scoped["etag"], key=self.key())
            grants.extend(result["result_ids"])
            course = self.call("existing teacher cookie observes live grant", "teacher_b", "GET", f'/courses/{self.course["id"]}')
            source = {"kind": "source_text", "title": "验收临时授权材料 " + self.run_id, "text": "合成验收资料，不用于真实课程。", "policy_version_id": self.course["policy_id"], "knowledge_point_ids": [k["id"] for k in self.course["knowledge_points"]]}
            source_key = self.key()
            self.call("temporarily authorized teacher can create source", "teacher_b", "POST", f'/courses/{self.course["id"]}/resources', source, etag=course["etag"], key=source_key)
            for grant_id in list(grants):
                scoped = self.get("admin", "/admin/scopes")
                self.call("administrator revokes temporary teaching grant", "admin", "POST", "/admin/scopes/commands", {"command": "revoke", "grant_id": grant_id, "reason": "验收结束恢复原授权范围。"}, etag=scoped["etag"], key=self.key())
                grants.remove(grant_id)
            self.call("old teacher cookie loses revoked course immediately", "teacher_b", "GET", f'/courses/{self.course["id"]}', expected=403)
            self.call("revoked teacher cannot replay successful write", "teacher_b", "POST", f'/courses/{self.course["id"]}/resources', source, etag=course["etag"], key=source_key, expected=403)
        finally:
            for grant_id in grants:
                scoped = self.get("admin", "/admin/scopes")
                self.call("cleanup temporary teaching grant", "admin", "POST", "/admin/scopes/commands", {"command": "revoke", "grant_id": grant_id, "reason": "异常退出安全清理验收临时授权。"}, etag=scoped["etag"], key=self.key())
        principal = {"command": "disable_v2", "principal_id": self.accounts["student_b"]["principal_id"], "reason": "验收演示账号停用与会话撤销，结束恢复。"}
        scoped = self.get("admin", "/admin/scopes")
        self.call("administrator cannot disable own principal", "admin", "POST", "/admin/principals/commands", {**principal, "principal_id": self.accounts["admin"]["principal_id"]}, etag=scoped["etag"], key=self.key(), expected=403, code="SELF_ESCALATION_DENIED")
        disabled = False
        try:
            self.call("administrator disables student B", "admin", "POST", "/admin/principals/commands", principal, etag=scoped["etag"], key=self.key())
            disabled = True
            self.call("disabled account existing session rejected", "student_b", "GET", "/session", expected=(401, 403))
            self.call("principal command rejects stale administrative version", "admin", "POST", "/admin/principals/commands", {**principal, "command": "enable_v2"}, etag=scoped["etag"], key=self.key(), expected=409, code="VERSION_CONFLICT")
            scoped = self.get("admin", "/admin/scopes")
            self.call("administrator re-enables student B", "admin", "POST", "/admin/principals/commands", {**principal, "command": "enable_v2"}, etag=scoped["etag"], key=self.key())
            disabled = False
            self.call("re-enable does not restore old session", "student_b", "GET", "/session", expected=401)
            time.sleep(2.1)  # JWT issued-at seconds must be after the persisted cutoff.
            self.login("student_b")
            self.call("re-enabled student can obtain fresh session", "student_b", "GET", "/session")
            scoped = self.get("admin", "/admin/scopes")
            self.call("administrator revokes active student B sessions", "admin", "POST", "/admin/principals/commands", {**principal, "command": "revoke_sessions"}, etag=scoped["etag"], key=self.key())
            self.call("revoked cookie immediately rejected", "student_b", "GET", "/session", expected=401)
            time.sleep(2.1)
            self.login("student_b")
            self.call("student B fresh login restored after revocation", "student_b", "GET", "/session")
        finally:
            if disabled:
                scoped = self.get("admin", "/admin/scopes")
                self.call("cleanup restores student B enabled state", "admin", "POST", "/admin/principals/commands", {**principal, "command": "enable_v2"}, etag=scoped["etag"], key=self.key())
        own = self.get("teacher_b", "/courses")
        self.check("teacher B ends with original course scope", [x["id"] for x in own["items"]] == [self.other_course["id"]])

    @staticmethod
    def standard_rules():
        return {"allowed_generation_modes": ["rule_template", "research_generator"],
                "required_checks": ["schema", "source_ready", "scope", "answer_consistency", "rubric_bounds", "boolean_truth_table"],
                "require_human_review": True, "allow_rule_auto_publish": True,
                "maximum_task_budget": 0, "maximum_task_attempts": 1, "allow_review": True}

    def policy_bootstrap(self):
        """Append corrected policy versions; never rewrite frozen seed history."""
        policies = self.get("admin", "/admin/policies")
        global_policy = max((p for p in policies["items"] if p["policy_type"] == "global"), key=lambda p: p["version"])
        if set(global_policy["rules"]) != set(self.standard_rules()):
            request = {"policy_type": "global", "course_id": None, "family_id": global_policy["family_id"], "expected_version": global_policy["version"], "parent_policy_id": None, "rules": self.standard_rules(), "reason": "修正初始演示策略字段，追加标准版本；原绑定保留原策略。"}
            created = self.call("append corrected global policy version", "admin", "POST", "/admin/policies", request, etag=policies["etag"], key=self.key())
            policies = self.get("admin", "/admin/policies")
            global_policy = next(p for p in policies["items"] if p["id"] == created["result_ids"][0])
        self.objects["current_global_policy_id"] = global_policy["id"]
        for course in (self.course, self.other_course):
            current = max((p for p in policies["items"] if p["course_id"] == course["id"]), key=lambda p: p["version"])
            if set(current["rules"]) != set(self.standard_rules()) or current["parent_policy_id"] != global_policy["id"]:
                request = {"policy_type": "course", "course_id": course["id"], "family_id": current["family_id"], "expected_version": current["version"], "parent_policy_id": global_policy["id"], "rules": self.standard_rules(), "reason": "演示课程追加标准策略版本，父引用当前全局策略；历史不变。"}
                created = self.call("append corrected course policy " + str(course["id"]), "admin", "POST", "/admin/policies", request, etag=policies["etag"], key=self.key())
                policies = self.get("admin", "/admin/policies")
                current = next(p for p in policies["items"] if p["id"] == created["result_ids"][0])
            course["policy_id"] = current["id"]
            self.objects["current_course_policy_" + str(course["id"])] = current["id"]

    def policy_flow(self):
        policies = self.get("admin", "/admin/policies")
        original = max((p for p in policies["items"] if p["course_id"] == self.other_course["id"]), key=lambda p: p["version"])
        parent = max((p for p in policies["items"] if p["policy_type"] == "global"), key=lambda p: p["version"])
        rules = original["rules"]
        child = {"policy_type": "course", "course_id": self.other_course["id"], "family_id": original["family_id"], "expected_version": original["version"], "parent_policy_id": parent["id"], "rules": {**rules, "maximum_task_budget": parent["rules"]["maximum_task_budget"] + 1}, "reason": "验收演示沿原家族追加版本；测试结束恢复原允许规则。"}
        self.call("course policy cannot widen parent budget", "admin", "POST", "/admin/policies", child, etag=policies["etag"], key=self.key(), expected=422, code="POLICY_CANNOT_WIDEN_PARENT")
        child["rules"] = {**rules, "require_human_review": False}
        self.call("course policy cannot remove parent review gate", "admin", "POST", "/admin/policies", child, etag=policies["etag"], key=self.key(), expected=422, code="POLICY_CANNOT_WIDEN_PARENT")
        child["rules"] = {**rules, "allowed_generation_modes": []}
        changed = False
        try:
            revised = self.call("administrator appends stricter course policy version", "admin", "POST", "/admin/policies", child, etag=policies["etag"], key=self.key())
            changed = True
            self.call("stale policy list version rejected", "admin", "POST", "/admin/policies", child, etag=policies["etag"], key=self.key(), expected=409, code="VERSION_CONFLICT")
            course = self.get("teacher_b", f'/courses/{self.other_course["id"]}')
            request = {"source_version_id": self.other_course["source_version_id"], "policy_version_id": revised["result_ids"][0], "knowledge_point_ids": [k["id"] for k in self.other_course["knowledge_points"]], "mode": "rule_template", "requirement": "验收：已禁用的生成模式不能执行。", "budget_limit": 0, "max_attempts": 1}
            self.call("business execution honors disabled generation mode", "teacher_b", "POST", f'/courses/{self.other_course["id"]}/generation-runs', request, key=self.key(), etag=course["etag"], expected=(403, 409, 422))
            request["policy_version_id"] = original["id"]
            self.call("new command cannot bypass current policy using old version", "teacher_b", "POST", f'/courses/{self.other_course["id"]}/generation-runs', request, key=self.key(), etag=course["etag"], expected=409)
        finally:
            if changed:
                current = self.get("admin", "/admin/policies")
                latest = max((p for p in current["items"] if p["family_id"] == original["family_id"]), key=lambda p: p["version"])
                restore = {**child, "expected_version": latest["version"], "rules": rules, "reason": "验收结束，追加新版本恢复原始允许规则；保留测试版本。"}
                self.call("restore course policy through append-only version", "admin", "POST", "/admin/policies", restore, etag=current["etag"], key=self.key())
        current = self.get("admin", "/admin/policies")
        family = [p for p in current["items"] if p["family_id"] == original["family_id"]]
        self.check("policy history retains original restricted and restored versions", all(v in [p["version"] for p in family] for v in [original["version"], original["version"] + 1, original["version"] + 2]))

    def supplemental(self):
        for key in self.accounts:
            self.login(key)
        self.policy_bootstrap()
        self.source_gates()
        self.admin_flow()
        self.policy_flow()
        self.completed = True
        self.save()
        print(json.dumps({"status": "passed", "checks": len(self.checks), "report": str(self.output.relative_to(ROOT))}, ensure_ascii=False))

    def final_gates(self):
        """Focused regression for the final parent-policy and task-ETag fixes."""
        self.login("admin")
        self.login("teacher_a")
        self.policy_bootstrap()
        policies = self.get("admin", "/admin/policies")
        global_policy = max((p for p in policies["items"] if p["policy_type"] == "global"), key=lambda p: p["version"])
        course_policy = max((p for p in policies["items"] if p["course_id"] == self.course["id"]), key=lambda p: p["version"])
        request = {"policy_type": "global", "course_id": None, "family_id": global_policy["family_id"], "expected_version": global_policy["version"], "parent_policy_id": None, "rules": global_policy["rules"], "reason": "验收最终父策略版本门禁：规则不变，仅追加新全局版本。"}
        self.call("append same-rules global version for stale-parent test", "admin", "POST", "/admin/policies", request, etag=policies["etag"], key=self.key())
        source = {"kind": "source_text", "title": "验收父策略对齐资料 " + self.run_id, "text": "仅为本地父策略门禁验收使用。", "policy_version_id": course_policy["id"], "knowledge_point_ids": [k["id"] for k in self.course["knowledge_points"]]}
        try:
            course = self.get("teacher_a", f'/courses/{self.course["id"]}')
            self.call("new content rejects stale global parent", "teacher_a", "POST", f'/courses/{self.course["id"]}/resources', source, etag=course["etag"], key=self.key(), expected=409, code="POLICY_PARENT_STALE")
            policies = self.get("admin", "/admin/policies")
            wrong_parent = {"policy_type": "course", "course_id": self.course["id"], "family_id": course_policy["family_id"], "expected_version": course_policy["version"], "parent_policy_id": global_policy["id"], "rules": course_policy["rules"], "reason": "验收：不能追加仍引用旧全局版本的课程策略。"}
            self.call("administration rejects outdated global parent reference", "admin", "POST", "/admin/policies", wrong_parent, etag=policies["etag"], key=self.key(), expected=409, code="POLICY_PARENT_STALE")
        finally:
            # Always align both courses to the new immutable global head.
            self.policy_bootstrap()
        course = self.get("teacher_a", f'/courses/{self.course["id"]}')
        source["policy_version_id"] = self.course["policy_id"]
        self.call("aligned parent policy permits normal content creation", "teacher_a", "POST", f'/courses/{self.course["id"]}/resources', source, etag=course["etag"], key=self.key())
        operations = self.get("admin", "/admin/operations")
        tasks = operations["tasks"]
        self.check("admin task list exposes execution epoch ETags", bool(tasks) and all(t["etag"] == '"' + str(t["execution_epoch"]) + '"' for t in tasks))
        task = self.get("admin", "/tasks/" + tasks[0]["id"])
        self.check("admin task list and detail share command ETag", task["etag"] == tasks[0]["etag"] and task["execution_epoch"] == tasks[0]["execution_epoch"])
        self.check("admin task detail omits result references", task["result_refs"] == {})
        self.completed = True
        self.save()
        print(json.dumps({"status": "passed", "checks": len(self.checks), "report": str(self.output.relative_to(ROOT))}, ensure_ascii=False))

    def run(self):
        self.login("admin")
        self.policy_bootstrap()
        self.baseline()
        self.mixed_flow()
        self.rule_flow()
        self.note_flow()
        self.completed = True
        self.save()
        print(json.dumps({"status": "passed", "checks": len(self.checks), "report": str(self.output.relative_to(ROOT))}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8138")
    parser.add_argument("--output", type=Path, default=ROOT / "deliverables/05-demo-acceptance/http-results.json")
    parser.add_argument("--section", choices=("full", "supplemental", "final-gates"), default="full")
    args = parser.parse_args()
    url = urlparse(args.base_url)
    if url.hostname not in ("127.0.0.1", "localhost") or url.scheme != "http":
        raise SystemExit("Acceptance runner is restricted to the local Demo service")
    qa = Acceptance(args.base_url, args.output)
    try:
        if args.section == "supplemental":
            qa.supplemental()
        elif args.section == "final-gates":
            qa.final_gates()
        else:
            qa.run()
    except Exception as exc:
        if not qa.checks or qa.checks[-1]["passed"]:
            qa.checks.append({"name": "runner completed without unexpected exception", "passed": False, "error_type": type(exc).__name__})
        qa.save()
        # Only labels and exception classes: never dump HTTP bodies or credentials.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "last_check": qa.checks[-1]["name"] if qa.checks else None}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
