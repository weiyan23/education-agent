"""Read-only exercise checks against the local synthetic Demo, using student cookies."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import UUID

from test_v2_demo import Acceptance, Client


def check_mapping(demo):
    course_id = demo.course["id"]
    lookup = f"/courses/{course_id}/recommendation-exercises"
    path = lookup + "?concept_id=java.condition"
    demo.call("anonymous lookup denied", Client(demo.base), "GET", path, expected=401)
    demo.login("student_a")
    demo.login("student_b")
    demo.call("other course student denied", "student_b", "GET", path, expected=403)
    missing = demo.get("student_a", path + "&concept_id=fixture.absent")
    demo.check("unmapped bundle returns no exercises",
               missing.get("state") == "no_content" and missing.get("items") == [])

    matches = demo.get("student_a", path)
    if matches.get("state") == "no_content" and matches.get("items") == []:
        print("SKIP：课程当前没有可访问的 java.condition 练习，未验证打开和版本检查。")
        return 2
    demo.check("received mapped exercises", matches.get("state") == "ready" and bool(matches.get("items")))
    item = matches["items"][0]
    open_path = item["open_api_path"]
    detail = demo.get("student_a", open_path)
    demo.check("opened assigned exercise with pinned versions",
               detail["binding_id"] == item["binding_id"]
               and detail["resource_version_id"] == item["resource_version_id"]
               and detail["publication_event_id"] == item["publication_event_id"]
               and detail["recipient"]["id"] == item["recipient_id"]
               and bool(detail["question_content"].get("questions")))
    demo.call("other student cannot open the same exercise", "student_b", "GET",
              open_path, expected=403)
    parts = urlsplit(open_path)
    params = parse_qsl(parts.query)
    wrong_version = str(UUID(int=UUID(item["resource_version_id"]).int ^ 1))
    params = [(key, wrong_version if key == "resource_version_id" else value)
              for key, value in params]
    demo.call("mismatched version rejected", "student_a", "GET",
              parts.path + "?" + urlencode(params), expected=409, code="EXERCISE_VERSION_CHANGED")
    print("OK：学生 A 能打开匹配练习，跨课程访问和错误版本均被拒绝。")
    return 0


if __name__ == "__main__":
    try:
        with TemporaryDirectory() as temp:
            demo = Acceptance("http://127.0.0.1:8138", Path(temp) / "checks.json")
            sys.exit(check_mapping(demo))
    except FileNotFoundError as exc:
        sys.exit(f"缺少本机 Demo 文件：{exc.filename}。请在已配置的 Demo 目录运行。")
    except (URLError, TimeoutError):
        sys.exit("连接本机 API 失败，请检查 scripts/local_demo.py status 和 8138 端口。")
    except AssertionError as exc:
        last = demo.checks[-1]
        sys.exit(f"FAIL：{exc}。HTTP {last.get('status', '—')}，错误码 {last.get('error_code') or '—'}。")
