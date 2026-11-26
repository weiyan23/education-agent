"""Trusted product-to-research boundary; no web authentication or Java execution.

Do not deserialize Principal, PaidAuthorization or Snapshot from a browser as
trusted objects. The product supplies them after object-level authorization and
snapshot construction. All return values exclude subprocess output and paths.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from typing import Callable, Mapping, Optional


BASE = Path(__file__).resolve().parents[1]
REPOSITORY = BASE / "upstream" / "education-agent"

# 兼容研究代码位于仓库根目录的项目结构。
if not REPOSITORY.is_dir() and BASE.parent.name == "demos":
    REPOSITORY = BASE.parent.parent

INTERPRETER = Path("/usr/bin/python3.10")
if not INTERPRETER.is_file():
    INTERPRETER = Path(sys.executable)
SCRIPTS = {"generate": "generate_question.py", "profile": "run_profile.py",
           "update": "update_profile.py", "recommend": "run_recommendation.py"}
LIMITS = {"generate": 8, "profile": 12, "update": 12, "recommend": 2}
OUTPUTS = {"generate": ("generation.json",), "profile": ("profile.json",),
           "update": ("update.json", "profile.json"), "recommend": ("recommendation.json",)}
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


class AdapterError(ValueError):
    """Stable public error code, never raw upstream exception text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str):
    if not condition:
        raise AdapterError(code)


def _identifier(value: str):
    _require(isinstance(value, str) and ID.fullmatch(value) is not None, "INVALID_IDENTIFIER")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    actor_id: str
    course_id: str
    subject_ids: frozenset[str]
    concept_ids: frozenset[str]
    operations: frozenset[str]


@dataclass(frozen=True)
class JobContext:
    job_id: str
    idempotency_key: str
    epoch: int
    expected_version: int


@dataclass(frozen=True)
class CurrentContext:
    """Fresh state from the product; must include current permission validity."""
    epoch: int
    version: int
    active: bool


@dataclass(frozen=True)
class PaidAuthorization:
    """A product-issued authorization bound to precisely one immutable request."""
    authorization_id: str
    actor_id: str
    job_id: str
    operation: str
    input_sha256: str
    max_calls: int
    expires_at: datetime


@dataclass(frozen=True)
class Snapshot:
    student_id: str
    catalog: dict
    allowed_concept_ids: tuple[str, ...]
    source_kind: str
    target_concept_ids: tuple[str, ...] = ()
    goal_concept_ids: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    profile: Optional[dict] = None
    records: tuple[dict, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping) -> "Snapshot":
        """Strict data-only schema. No command, path, env, output or run flags."""
        _require(isinstance(data, Mapping), "INVALID_SNAPSHOT")
        keys = set(cls.__dataclass_fields__)
        _require(set(data) <= keys, "UNSUPPORTED_SNAPSHOT_FIELD")
        _require({"student_id", "catalog", "allowed_concept_ids", "source_kind"} <= set(data),
                 "INCOMPLETE_SNAPSHOT")
        copied = json.loads(json.dumps(dict(data), allow_nan=False))
        for key in ("allowed_concept_ids", "target_concept_ids", "goal_concept_ids", "requirements", "records"):
            if key in copied:
                _require(isinstance(copied[key], list), "INVALID_SNAPSHOT_ARRAY")
                copied[key] = tuple(copied[key])
        return cls(**copied)


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    kind: str
    sha256: str


@dataclass(frozen=True)
class AdapterResult:
    job_id: str
    status: str
    error_code: Optional[str] = None
    artifact_refs: tuple[ArtifactRef, ...] = ()
    model_connection: str = "not_verified"
    ready_for_student: bool = False


def subprocess_transport(argv: list[str], *, cwd: Path, timeout: int):
    """Fixed argv execution, never shell text. Output is intentionally private."""
    return subprocess.run(argv, cwd=str(cwd), shell=False, capture_output=True,
                          text=True, timeout=timeout,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


class ResearchAdapter:
    def __init__(self, storage: Path, current_context: Callable[[Principal, str, str], CurrentContext],
                 *, env_file: Optional[Path] = None,
                 transport: Callable = subprocess_transport, fixture_transport: bool = False,
                 timeout_seconds: int = 600, repository: Path = REPOSITORY,
                 interpreter: Path = INTERPRETER):
        """Constructor configuration belongs to deployment code, never a request.

        current_context must recheck current object permissions and version/epoch.
        This library never creates users, grants scopes or changes product records.
        """
        _require(callable(current_context), "CURRENT_CONTEXT_REQUIRED")
        self.repository = repository.resolve()
        self.interpreter = interpreter
        _require(self.interpreter.is_file() and (self.repository / "AGENTS.md").is_file(), "RUNTIME_UNAVAILABLE")
        _require(type(timeout_seconds) is int and 1 <= timeout_seconds <= 1800, "INVALID_TIMEOUT")
        _require(not fixture_transport or transport is not subprocess_transport, "FIXTURE_TRANSPORT_REQUIRED")
        self.storage = Path(storage).resolve()
        _require(not self.storage.is_relative_to(self.repository) or fixture_transport, "STORAGE_INSIDE_UPSTREAM")
        self.storage.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.storage.chmod(0o700)
        self.env_file = Path(env_file).resolve() if env_file else None
        self.current_context = current_context
        self.transport = transport
        self.fixture_transport = fixture_transport
        self.timeout = timeout_seconds
        source_files = [self.repository / name for name in SCRIPTS.values()]
        for module in ("profiling", "generation", "recommendation", "submissions"):
            source_files.extend(sorted((self.repository / module).glob("*.py")))
        self.implementation = _digest({str(p.relative_to(self.repository)): hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in source_files})
        self.db = self.storage / "adapter.sqlite3"
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY, idem_scope TEXT NOT NULL UNIQUE,
                identity TEXT NOT NULL, status TEXT NOT NULL, result TEXT)""")
        self.db.chmod(0o600)

    @staticmethod
    def input_fingerprint(operation: str, principal: Principal, snapshot: Snapshot,
                          job: JobContext, max_calls: Optional[int]) -> str:
        return _digest({"operation": operation, "actor": principal.actor_id,
                        "course": principal.course_id, "snapshot": asdict(snapshot),
                        "job": asdict(job), "max_calls": max_calls})

    def _current(self, principal, operation, snapshot, job):
        state = self.current_context(principal, snapshot.student_id, operation)
        _require(isinstance(state, CurrentContext) and state.active, "CONTEXT_REVOKED")
        _require(state.epoch == job.epoch and state.version == job.expected_version, "STALE_CONTEXT")

    def _validate(self, operation, principal, snapshot, job):
        _require(operation in SCRIPTS, "UNSUPPORTED_OPERATION")
        _require(isinstance(principal, Principal) and isinstance(snapshot, Snapshot)
                 and isinstance(job, JobContext), "TRUSTED_CONTEXT_REQUIRED")
        for value in (principal.actor_id, principal.course_id, snapshot.student_id, job.job_id):
            _identifier(value)
        _require(isinstance(job.idempotency_key, str) and 1 <= len(job.idempotency_key) <= 256,
                 "INVALID_IDEMPOTENCY_KEY")
        _require(type(job.epoch) is int and type(job.expected_version) is int
                 and job.epoch >= 0 and job.expected_version >= 0, "INVALID_CONTEXT_VERSION")
        _require(operation in principal.operations and snapshot.student_id in principal.subject_ids,
                 "OUTSIDE_TRUSTED_SCOPE")
        _require(snapshot.source_kind in ("real_student", "historical_student", "test_fixture"),
                 "INVALID_SOURCE_KIND")
        for values in (snapshot.allowed_concept_ids, snapshot.target_concept_ids, snapshot.goal_concept_ids):
            _require(isinstance(values, tuple) and len(set(values)) == len(values), "INVALID_CONCEPT_IDS")
            for value in values:
                _identifier(value)
        _require(bool(snapshot.allowed_concept_ids)
                 and set(snapshot.allowed_concept_ids) <= principal.concept_ids
                 and set(snapshot.target_concept_ids + snapshot.goal_concept_ids)
                 <= set(snapshot.allowed_concept_ids), "OUTSIDE_TRUSTED_SCOPE")
        _require(isinstance(snapshot.catalog, dict) and isinstance(snapshot.catalog.get("concepts"), dict),
                 "INVALID_CATALOG")
        _require(set(snapshot.allowed_concept_ids) <= set(snapshot.catalog["concepts"])
                 and set(snapshot.catalog["concepts"]) <= principal.concept_ids, "OUTSIDE_TRUSTED_SCOPE")
        _require(all(isinstance(x, str) and 0 < len(x) <= 4000 for x in snapshot.requirements),
                 "INVALID_REQUIREMENTS")
        if snapshot.profile is not None:
            _require(isinstance(snapshot.profile, dict) and snapshot.profile.get("student_id") == snapshot.student_id,
                     "PROFILE_SUBJECT_MISMATCH")
            _require(snapshot.profile.get("status") == "completed" and not snapshot.profile.get("failed_steps"),
                     "INCOMPLETE_PROFILE")
        if operation == "generate":
            _require(bool(snapshot.target_concept_ids) and bool(snapshot.requirements), "GENERATION_TARGETS_REQUIRED")
        if operation in ("profile", "update"):
            _require(1 <= len(snapshot.records) <= 5, "INVALID_RECORD_COUNT")
            _require(operation != "update" or snapshot.profile is not None, "BASE_PROFILE_REQUIRED")
            for row in snapshot.records:
                _require(isinstance(row, dict) and row.get("student_id") == snapshot.student_id,
                         "EVIDENCE_SUBJECT_MISMATCH")
                _require(row.get("split") in (("train",) if operation == "profile" else ("train", "live")),
                         "INELIGIBLE_EVIDENCE")
                _require(row.get("evidence_origin") in (None, "historical_student_submission", "student_submission"),
                         "INELIGIBLE_EVIDENCE")
                _require(all(isinstance(row.get(k), str) and row[k].strip()
                             for k in ("record_id", "problem_id", "question", "program")), "INVALID_EVIDENCE")
        _require(len(json.dumps(asdict(snapshot), ensure_ascii=False).encode()) <= 2_000_000,
                 "SNAPSHOT_TOO_LARGE")
        self._current(principal, operation, snapshot, job)

    def _authorize_execution(self, operation, principal, snapshot, job, calls, authorization, fingerprint):
        _require(isinstance(authorization, PaidAuthorization), "PAID_AUTHORIZATION_REQUIRED")
        _require(type(calls) is int and 1 <= calls <= LIMITS[operation], "EXPLICIT_BUDGET_REQUIRED")
        _require(authorization.actor_id == principal.actor_id and authorization.job_id == job.job_id
                 and authorization.operation == operation and authorization.input_sha256 == fingerprint
                 and authorization.max_calls == calls, "AUTHORIZATION_MISMATCH")
        _identifier(authorization.authorization_id)
        _require(isinstance(authorization.expires_at, datetime) and authorization.expires_at.tzinfo is not None
                 and authorization.expires_at > datetime.now(timezone.utc), "AUTHORIZATION_EXPIRED")
        _require(self.env_file is not None and self.env_file.is_file(), "MODEL_NOT_CONFIGURED")
        _require(snapshot.source_kind != "test_fixture" or self.fixture_transport, "FIXTURE_EXECUTION_FORBIDDEN")

    def _reserve(self, job, scope, identity):
        with closing(sqlite3.connect(self.db, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT job_id,identity,status,result FROM jobs WHERE job_id=? OR idem_scope=?",
                             (job.job_id, scope)).fetchall()
            if row:
                _require(len(row) == 1 and row[0][0] == job.job_id and row[0][1] == identity,
                         "IDEMPOTENCY_CONFLICT")
                _require(row[0][2] != "running", "JOB_OUTCOME_UNKNOWN")
                _require(row[0][3] is not None, "JOB_OUTCOME_UNKNOWN")
                data = json.loads(row[0][3])
                data["artifact_refs"] = tuple(ArtifactRef(**a) for a in data["artifact_refs"])
                return AdapterResult(**data)
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,NULL)", (job.job_id, scope, identity, "running"))
        return None

    def _finish(self, result):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE jobs SET status=?,result=? WHERE job_id=?",
                       (result.status, json.dumps(asdict(result)), result.job_id))
        return result

    @staticmethod
    def _write(path, value):
        with path.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
        path.chmod(0o600)

    def _command(self, operation, snapshot, job_dir, mode, calls):
        inputs = job_dir / "inputs"
        inputs.mkdir(mode=0o700)
        catalog = inputs / "catalog.json"
        self._write(catalog, snapshot.catalog)
        argv = [str(self.interpreter), "-B", str(self.repository / SCRIPTS[operation]),
                "--output", str(job_dir / "output"), "--cache", str(job_dir / "cache"),
                "--env-file", str(self.env_file) if mode == "execute" else str(job_dir / "unused.env")]
        if operation == "profile":
            argv += ["--concepts", str(catalog), "--student", snapshot.student_id,
                     "--limit", str(len(snapshot.records))]
        else:
            argv += ["--catalog", str(catalog)]
        if operation in ("generate", "recommend"):
            argv += ["--student", snapshot.student_id, "--scope", *snapshot.allowed_concept_ids]
            if operation == "generate":
                argv += ["--targets", *snapshot.target_concept_ids]
            elif snapshot.goal_concept_ids:
                argv += ["--goals", *snapshot.goal_concept_ids]
            argv += ["--requirement=" + text for text in snapshot.requirements]
        if snapshot.profile is not None and operation != "profile":
            profile = inputs / "profile.json"
            self._write(profile, snapshot.profile)
            argv += ["--base-profile" if operation == "update" else "--profile", str(profile)]
        if operation in ("profile", "update"):
            records = inputs / "records.json"
            self._write(records, snapshot.records)
            argv += ["--records", str(records)]
        argv += ["--max-calls", str(calls if calls is not None else LIMITS[operation])]
        if mode == "execute":
            argv.append("--execute")
        return argv

    def _artifacts(self, operation, snapshot, job, job_dir):
        refs, report = [], None
        output = job_dir / "output"
        for filename in OUTPUTS[operation]:
            path = output / filename
            _require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(job_dir),
                     "MISSING_OR_UNSAFE_RESULT")
            _require(path.stat().st_size <= 8_000_000, "RESULT_TOO_LARGE")
            raw = path.read_bytes()
            data = json.loads(raw)
            _require(isinstance(data, dict), "INVALID_RESULT")
            if report is None:
                report = data
            digest = hashlib.sha256(raw).hexdigest()
            refs.append(ArtifactRef(_digest([job.job_id, filename, digest]), filename.removesuffix(".json"), digest))
        if operation == "generate":
            _require(report.get("schema") == "generation-result-v1"
                     and report.get("student_id") == snapshot.student_id
                     and report.get("ready_for_student") is False
                     and report.get("execution_status") == "completed"
                     and report.get("status") in ("needs_review", "needs_split", "rejected"), "INVALID_RESULT")
        elif operation == "recommend":
            _require(report.get("schema") == "recommendation-round-v1"
                     and report.get("student_id") == snapshot.student_id
                     and report.get("strategy") == "B" and report.get("decision_kind") == "model_inference"
                     and report.get("execution_status") == "completed"
                     and report.get("status") in ("recommended", "insufficient_evidence"), "INVALID_RESULT")
        else:
            profile = report if operation == "profile" else report.get("profile")
            _require(report.get("status") == "completed" and not report.get("failed_steps")
                     and isinstance(profile, dict) and profile.get("status") == "completed"
                     and profile.get("student_id") == snapshot.student_id, "INVALID_RESULT")
            if operation == "update":
                _require(data == profile, "PROFILE_RESULT_MISMATCH")
        return report["status"], tuple(refs)

    def run(self, operation: str, principal: Principal, snapshot: Snapshot, job: JobContext, *,
            mode: str = "preview", max_calls: Optional[int] = None,
            authorization: Optional[PaidAuthorization] = None) -> AdapterResult:
        _require(mode in ("preview", "execute", "fixture"), "UNSUPPORTED_MODE")
        # Detach mutable nested data before hashing or writing it.
        snapshot = Snapshot.from_mapping(asdict(snapshot)) if isinstance(snapshot, Snapshot) else snapshot
        self._validate(operation, principal, snapshot, job)
        if mode == "fixture":
            _require(self.fixture_transport and operation == "generate"
                     and snapshot.source_kind == "test_fixture" and authorization is None,
                     "FIXTURE_EXECUTION_FORBIDDEN")
        _require(max_calls is None or type(max_calls) is int and 1 <= max_calls <= LIMITS[operation],
                 "INVALID_BUDGET")
        fingerprint = self.input_fingerprint(operation, principal, snapshot, job, max_calls)
        if mode == "execute":
            self._authorize_execution(operation, principal, snapshot, job, max_calls, authorization, fingerprint)
        # A preview is a separate immutable job; it cannot silently become execute.
        try:
            config_hash = hashlib.sha256(self.env_file.read_bytes()).hexdigest() if mode == "execute" else None
        except OSError:
            raise AdapterError("MODEL_CONFIGURATION_UNREADABLE") from None
        identity = _digest([fingerprint, mode, str(self.interpreter), str(self.repository),
                            self.implementation, config_hash, self.timeout, self.fixture_transport])
        scope = _digest([principal.course_id, principal.actor_id, operation, job.idempotency_key])
        previous = self._reserve(job, scope, identity)
        if previous is not None:
            return previous
        job_dir = self.storage / job.job_id
        try:
            job_dir.mkdir(mode=0o700)
            argv = self._command(operation, snapshot, job_dir, mode, max_calls)
            # Recheck after materialization and immediately before dispatch.
            self._current(principal, operation, snapshot, job)
            completed = self.transport(argv, cwd=self.repository, timeout=self.timeout)
            self._current(principal, operation, snapshot, job)
            if completed.returncode != 0:
                return self._finish(AdapterResult(job.job_id, "failed", "UPSTREAM_FAILED"))
            if mode == "preview":
                preview = json.loads(completed.stdout)
                _require(preview.get("mode") == "preview", "INVALID_PREVIEW")
                return self._finish(AdapterResult(job.job_id, "previewed"))
            status, refs = self._artifacts(operation, snapshot, job, job_dir)
            self._current(principal, operation, snapshot, job)
            return self._finish(AdapterResult(job.job_id, status, artifact_refs=refs,
                                model_connection="not_verified_fixture" if self.fixture_transport else "response_received"))
        except subprocess.TimeoutExpired:
            # Do not rerun: an external request may have reached the provider.
            return self._finish(AdapterResult(job.job_id, "unknown", "JOB_OUTCOME_UNKNOWN"))
        except AdapterError as exc:
            return self._finish(AdapterResult(job.job_id, "discarded" if exc.code in ("CONTEXT_REVOKED", "STALE_CONTEXT")
                                              else "failed", exc.code))
        except (OSError, ValueError, TypeError, KeyError):
            return self._finish(AdapterResult(job.job_id, "unknown", "ADAPTER_RESULT_UNAVAILABLE"))

    def read_generation(self, principal, snapshot, job, artifact):
        """Resolve an accepted generation reference for an authorized server caller."""
        self._validate("generate", principal, snapshot, job)
        with closing(sqlite3.connect(self.db)) as db:
            scope = _digest([principal.course_id, principal.actor_id, "generate", job.idempotency_key])
            row = db.execute("SELECT result FROM jobs WHERE job_id=? AND idem_scope=? AND status='needs_review'",
                             (job.job_id, scope)).fetchone()
        _require(row is not None and asdict(artifact) in json.loads(row[0])["artifact_refs"],
                 "ARTIFACT_MISMATCH")
        path = self.storage / job.job_id / "output" / "generation.json"
        _require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(self.storage)
                 and path.stat().st_size <= 8_000_000, "ARTIFACT_MISMATCH")
        raw = path.read_bytes()
        _require(hashlib.sha256(raw).hexdigest() == artifact.sha256, "ARTIFACT_MISMATCH")
        result = json.loads(raw)
        _require(result.get("student_id") == snapshot.student_id, "ARTIFACT_MISMATCH")
        self._current(principal, "generate", snapshot, job)
        return result


@dataclass(frozen=True)
class LearningEvidenceProjection:
    """Product event projection; deliberately not StudentProfile/model output."""
    course_id: str
    student_id: str
    effective_result_ids: tuple[str, ...] = ()
    research_status: str = "not_run"
    schema: str = field(default="learning-evidence-projection-v1", init=False)
    projection_kind: str = field(default="deterministic_rule", init=False)
    mastery_state: str = field(default="Unknown", init=False)
    model_id: None = field(default=None, init=False)

    def __post_init__(self):
        _identifier(self.course_id)
        _identifier(self.student_id)
        _require(self.research_status in ("not_run", "current", "stale"), "INVALID_RESEARCH_STATUS")
        _require(isinstance(self.effective_result_ids, tuple)
                 and len(set(self.effective_result_ids)) == len(self.effective_result_ids), "INVALID_RESULT_IDS")
        for result_id in self.effective_result_ids:
            _identifier(result_id)

    def correct_result(self, old_result_id: str, new_result_id: str) -> "LearningEvidenceProjection":
        _identifier(old_result_id)
        _identifier(new_result_id)
        _require(old_result_id != new_result_id, "RESULT_VERSION_UNCHANGED")
        _require(old_result_id in self.effective_result_ids, "RESULT_NOT_EFFECTIVE")
        _require(new_result_id not in self.effective_result_ids, "DUPLICATE_RESULT_VERSION")
        return LearningEvidenceProjection(self.course_id, self.student_id,
            tuple(new_result_id if x == old_result_id else x for x in self.effective_result_ids), "stale")
