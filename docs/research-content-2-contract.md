# 研究内容二 · V2.1 本地接口与阶段契约

更新：2026-11-26。文档修订号：`contract-v2.1-profiling-sequence`。序列 JSON 的原有 `dynamic-learning-sequence-input-v1` / `output-v1` schema 名称保留，来源与置信度语义按本修订执行。

范围：Python 研究函数、CLI、旧本地工作台和离线测试。**不表示真实 S08/T11、HTTP 鉴权、LearnHouse 数据库画像版本或事件订阅已接入。** V2.1 的合成预览只能用于演示。

## 1. 事件与证据

`profiling.events` 定义 9 种事件：`submission_received`、`auto_graded`、`teacher_graded`、`grade_revised`、`recheck_requested`、`recheck_resolved`、`access_granted`、`access_revoked`、`submission_voided`。枚举和校验器不等于真实事件生产/消费链。

LearningEvent 必须含 `event_id`、`event_type`、`student_id`、`course_id`、带时区的 `timestamp`、对象形式的 `payload`/`provenance`、`event_sha256`。哈希按上述前 7 个字段的固定顺序通过项目 `fingerprint` 计算；使用 `create_learning_event` 构建。缺哈希或内容变动即拒绝，排序按时间瞬间而非字符串比较。内容哈希不是签名或操作者身份认证。

修订入口额外要求：

| 事件 | 约束与行为 |
|---|---|
| `grade_revised` | 定位当前 record_id 或 submission_id，二者同时提供时均需匹配；必须有 status=effective 的 revised_grade |
| `submission_voided` | 定位单条当前作答并作废；不能用缺少 revised_grade 的改分事件代替 |
| `access_revoked` | 按学生和课程撤销当前整份画像，不要求提交 ID；结果保持失效，无重新授权流程不得自动激活 |

修订须有非空 `provenance.operator_id`，事件学生/课程必须与输入画像已绑定范围一致。未知目标、重复应用过的事件及内容哈希不符均拒绝。操作者认证仍须由未来的受认证事件适配器完成。

`profiling.evidence` 扩展字段：

- `evidence_kind`：`code_submission` / `boolean_choice` / `teacher_grade` / `tool_observation`；当前模型仍主要处理 Java 作答，枚举不代表算法适配完成。
- `lifecycle_status`：`active` / `superseded` / `revoked`；当前学习证据拒绝非 active 修订。
- `effective_grade`：有限数值 score/max_score、status、graded_by、带时区 graded_at。存在评分时仅 effective 可进入当前画像；under_review/revoked 不能进入。
- live 证据必须绑定学生、题目版本和提交哈希。工厂包含 `submission.submitted_at`，未知时为 null；不能把服务器 received_at 当成学生提交时间。
- 仅原有合规 train/live 学习证据可进入画像，test/test_fixture 拒绝。来源声明与字段校验仍不能证明数据真实。

## 2. 本地失效与重建

`invalidate_profile` 返回旧画像的失效副本，不修改传入对象或在线数据库。

`reconstruct_profile` 先校验事件范围和候选证据，再显式重建。输出当前 `profile.evidence` 仅保留有效修订；旧修订放在 `evidence_history`，保留 `stale_profile`、`supersedes_profile_sha256` 和修订事件指纹。live submission 的 record_id 保持稳定，避免旧新证据在当前集合重复。

报告统一包含 `status`、`failed_steps`、`revision`、`stale_profile`、`profile`：

- 正常及全作答作废后的空画像：completed；空画像没有掌握观察。
- 课程撤权：invalidated、is_active=false、access_status=revoked，不调用模型。
- 模型步骤失败：partial、profile=null，不发布有效替代画像。

使用 RunStore 时另保存 `invalidated-profile.json`、`profile.json`（产生结果时）、`reconstruction.json` 和检查点；恢复复用原失效快照及已完成步骤。旧输入不覆盖。没有数据库当前版本指针切换；调用方不能把局部函数执行当成在线推荐已全面阻断。

CLI 默认预览。`--revoke-record-id` 对应 submission_voided；`--correction-event` 对应完整事件。缺课程的旧本地画像须显式 `--course-id` 绑定，已有课程不得重标。`--execute` 才运行，非空重建可能产生模型调用；遵循项目预算和执行授权。`rebuild_profile` 是本地证据重建入口，不是撤权后的重新授权接口。

## 3. 序列与模拟数据

输入须绑定 sequence_id/student_id/course_id、带时区窗口和有序动作；动作时间须在窗口内，step_index 严格递增，时长为有限非负值。

本版仅对明确 `data_source=synthetic_demo_data` 且 `is_synthetic=true` 的样例生成规则轨迹；只采纳标为 `evaluation_status=synthetic_effective` 的 grade_feedback/correction_revision。草稿、阅读、提交动作本身不产生掌握判断。生成器将提交与模拟评分分开。

| 输出 | V2.1 语义 |
|---|---|
| `current_state` | 合成评分映射的 Good/Bad/Unknown/no_observation，不是实际学生认知结论 |
| `trend` | 合成结果的规则变化：improving/regressing/stable/fluctuating；单点或无结果为 insufficient_data |
| `temporal_confidence` | 固定 none，尚未校准；不再按事件条数输出 high/medium |
| `data_source` / `is_synthetic` | 合成样例明确为 synthetic_demo_data / true；缺来源或自称 real 的本地输入返回 unverified |
| `source_verification_status` | synthetic_fixture 或 unverified_local_input |
| `learning_evidence_eligible` | 本版序列输出均为 false，不进入真实画像/推荐证据集 |
| `method_kind` | synthetic_rule_illustration 或 unverified_behavior_summary |

真实/未标来源 JSON 在可信事件适配器完成前，只返回未验证行为摘要，知识点为 no_observation；不接受仅凭调用方声明就生成 Good/Bad。合成标记不能直接改成 real。

3 组合成样例分别演示结果由负转正、连续负结果、模拟改分；不构成掌握提升、稳定误解或真实教师操作的验证。`python run_sequence.py --generate-synthetic` 仅预览，追加 `--execute` 才输出 `runs/synthetic-sequences/`。分析 `--sequence-file` 同样默认预览，显式执行时目录输出为 `sequence.json`；也兼容指定 `.json` 文件。

## 4. C/D 交接与视图格式化

`export_profile_for_recommendation(profile, student_id, course_id=None)`：核对学生；传入课程时必须与画像一致。只导出 completed、有效、无失败步骤且未撤权的画像，否则返回 is_eligible_for_recommendation=false。成功返回 **completed 状态的完整当前画像**，保留 evidence/observations/summaries，可交给原根 `prepare_context` 再校验。profile_fingerprint 不是数据库 profile_version_id；尚未完成新版 C 的数据库版本、method_kind 和研究字段绑定。

`export_evidence_for_evaluation(profile, events=None, student_id=None, course_id=None)`：只接受完整有效画像，检查逐条证据、事件哈希及学生/课程，拒绝外部范围和 fixture 事件。允许历史引用缺 source_hash，但不会把缺失值伪装成验证通过。

**两种接口成功导出时均返回 `integrity_verified=false`、`source_verification_status=unverified_local_input`，validation_scope 为 structure_and_evidence_binding_only。** 这是未接权威来源验证的实际状态。本地推荐 eligibility 仅表示结构可供离线消费，不能代替来源认证、在线权限或数据库版本验收。

`get_learning_state_for_s08` 仅格式化数据：正常卡片保留观察计数/解释，状态为 observed 或 no_observation；不以 Good/Bad 次数投票掌握状态。失效画像返回空卡片，跨课程请求拒绝。

`get_class_learning_summary_for_t11` 仅格式化调用方已授权的名单。教师身份、课程/班级归属和 P15 权限须在真实 API 层实现；当前函数不是鉴权接口。S08/T11 真页面接线与用户权限测试仍是后续交付。

## 5. 验证记录

V2.1 已实测 59 项离线测试：研究二 11、集成回归 22、原推荐 13、原画像 8、原增量画像 5，均通过。包含新 live 工厂→重建→原推荐链、撤权、跨课程、来源声明、CLI 与恢复；未使用付费模型、Java 或业务数据库。运行命令见 [模块说明](../README_RESEARCH_TWO.md)。

历史作者记录：修复前 `Ran 200 tests in 24.393s, OK (skipped=3)`，新增样例 11 项。这是历史自测，不是本轮全量复核，也不能支持“200/200 全执行通过”“真实权限已接”或“无任何破坏性变更”的结论。
