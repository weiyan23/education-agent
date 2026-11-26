# 研究内容二：基于实时学情与动态行为序列的学生画像 · 接口与契约规范

## 1. 概述与版本定义
本文档定义了教育智能体项目中**研究内容二**（学生学情画像与动态时序建模）的核心数据结构、生命周期状态机、算法实现以及向下游（推荐模块 C、质量评估模块 D）和前端（S08/T11）的交付契约。

- **规范版本**：`contract-v2-profiling-sequence`
- **基线版本**：基于 `education-agent-main` 与产品说明书 V2.0
- **测试覆盖**：200/200 项自动化测试通过

---

## 2. 阶段一：统一学习事件与证据契约规范

### 2.1 学习事件（LearningEvent）
模块：`profiling.events`
涵盖教育智能体全生命周期的 8 类核心事件：
- `submission_received`：学生提交作答已接收
- `auto_graded`：客观题系统自动评分
- `teacher_graded`：教师在 T10 完成人工批阅
- `grade_revised`：教师复核后修改更正评分
- `recheck_requested`：学生在 S07 提出成绩复核申请
- `recheck_resolved`：教师对复核做出保留或修订裁决
- `access_granted`：管理员授权学生加入课程/班级
- `access_revoked`：学生退班或撤销课程权限

**数据格式**：
```json
{
  "event_id": "evt_019283...",
  "event_type": "grade_revised",
  "student_id": "student_01",
  "course_id": "java_basics",
  "timestamp": "2026-11-24T12:00:00+08:00",
  "payload": {
    "submission_id": "sub_101",
    "record_id": "submission:abc...",
    "reason": "订正逻辑符合题意，补充给分",
    "revised_grade": {
      "score": 100,
      "max_score": 100,
      "status": "effective",
      "graded_by": "teacher_01",
      "graded_at": "2026-11-24T12:00:00+08:00"
    }
  },
  "provenance": {
    "resource_id": "res_java_01",
    "version": 1,
    "operator_id": "teacher_01"
  },
  "event_sha256": "..."
}
```

### 2.2 扩展证据模型（EvidencePacket）
模块：`profiling.evidence`
支持多题型与多源评分，扩展字段：
- `evidence_kind`: `code_submission` | `boolean_choice` | `teacher_grade` | `tool_observation`
- `lifecycle_status`: `active`（有效） | `superseded`（被更正替代） | `revoked`（已撤销作废）
- `effective_grade`: 包含 `score`、`max_score`、`status` (`effective` / `under_review` / `revoked`)、`graded_by`、`graded_at`

---

## 3. 阶段二：画像失效与显式重建逻辑

### 3.1 核心原则
- **增量更新不等于纠错**：原有 `incremental.py` 仅向前追加证据。当教师改分或学生撤权时，必须先将原画像标记为失效，再基于纯净的有效证据全集执行重建。
- **历史留痕不覆盖**：旧画像保留完整只读审计链，新画像通过 `revisions` 记录 `supersedes_profile_sha256`。

### 3.2 核心函数
- `invalidate_profile(base_profile, reason, operator_id)`: 将画像置为 `is_active: false`, `status: "invalidated"`。
- `reconstruct_profile(store, client, base_profile, catalog, correction_event, ...)`: 过滤被作废的证据，重新运行提取与聚合，产出版本化新画像。
- `rebuild_profile(student_id, course_id, valid_evidences_only, catalog, client)`: 算法入口，剔除全部失效证据直接重建。
- CLI 入口：`python reconstruct_profile.py --base-profile <path> --revoke-record-id <id> --reason <text>`

---

## 4. 阶段三：动态行为序列建模与合成样例

### 4.1 输入与输出架构
模块：`profiling.sequence`
- **输入 Schema**: `dynamic-learning-sequence-input-v1`
  记录按时间排序的动作流：`material_read`（阅读资料）、`draft_save`（保存草稿）、`submission_attempt`（提交作答）、`grade_feedback`（成绩反馈）、`recheck_request`（复核）、`correction_revision`（订正）。
- **输出 Schema**: `dynamic-learning-sequence-output-v1`
  按知识点计算轨迹：
  - `current_state`: `Good` | `Bad` | `Unknown` | `no_observation`（**绝无伪造概率**）
  - `trend`: `improving` | `regressing` | `stable` | `fluctuating` | `insufficient_data`
  - `temporal_confidence`: `high` | `medium` | `none`

### 4.2 交付的 3 组标准合成数据集
模块：`profiling.synthetic_sequence`
命令行一键生成：`python run_sequence.py --generate-synthetic`
产物位于 `runs/synthetic-sequences/`：
1. `scenario_1_seq_synth_prog_student_synth_01.json`：**常规递进掌握**（阅读 -> 初次试错 -> 练习巩固 -> 掌握，趋势 `improving`）。
2. `scenario_2_seq_synth_misconception_student_synth_01.json`：**持续试错误解**（边界条件反复试错，缺乏纠错反馈，状态 `Bad`）。
3. `scenario_3_seq_synth_revision_student_synth_01.json`：**改分订正恢复**（格式误判申请复核，教师在 T10 人工复核改分后恢复有效状态）。

---

## 5. 阶段四：下游交接与前端对接契约

模块：`profiling.adapter`

### 5.1 向推荐模块 C（盛瑞轩）交接
- **接口**：`export_profile_for_recommendation(profile, student_id, course_id)`
- **边界**：当画像 `is_active == false` 时返回 `is_eligible_for_recommendation: false`，阻断推荐；仅向 C 提供有效、带指纹和知识点微观计数的画像。

### 5.2 向评估模块 D 交接
- **接口**：`export_evidence_for_evaluation(profile, events, student_id)`
- **边界**：仅交付经过校验的有效证据列表，包含来源哈希 `source_hash` 与接收时间戳。

### 5.3 前端页面
- **S08（我的学习状态）**：`get_learning_state_for_s08()` 提供知识点状态标签卡（良好/需巩固/待评估/无观察）与证据抽屉数据。
- **T11（班级学情与学生详情）**：`get_class_learning_summary_for_t11()` 严格执行班级范围隔离与 P15 权限过滤。

---

## 6. 验证报告
运行全量测试套件：
```bash
python -m unittest discover tests
```
- 测试结果：`Ran 200 tests in 24.393s, OK (skipped=3)`
- 结论：原有 189 项基线测试 100% 保持通过，新增 11 项研究二模块测试全部通过，无任何破坏性变更。
