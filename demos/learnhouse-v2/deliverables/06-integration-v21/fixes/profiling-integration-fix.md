# V2.1 画像模块阶段集成修复交付

日期：2026-11-26。工作树：`/home/hongjian/projects/education-agent/integration-v2.1-2026-11-26`。

本轮目标是修复已发现的可复现边界缺陷，并使根研究包能作为下一阶段集成基础。没有接入 LearnHouse 数据库或真实事件消费，没有调用付费模型、执行 Java、读取私有 `.env`、提交或推送 Git。

## 已修复内容

| 问题 | 本版行为 | 回归证据 |
|---|---|---|
| 改分生成同 ID 的新旧证据导致推荐校验失败 | 保留 live submission 的稳定 record_id；当前 profile.evidence 只有有效修订，旧修订放 evidence_history，原画像保留为 stale_profile；记录 supersedes_evidence_sha256 与事件指纹 | live 证据→模型桩画像→改分重建→原 prepare_context→导出→原 prepare_context 全链通过 |
| 课程撤权无效 | access_revoked 按已绑定学生/课程撤销整份当前快照，不调用模型；输出 invalidated、is_active=false、access_status=revoked，保留课程及旧证据审计；禁止无重新授权流程的再次激活 | 整课程两条证据全部退出当前集合，推荐导出阻断，原 prepare_context 拒绝失效画像 |
| partial/failed 或跨课程画像被导出为 active | 只接受 completed 且有效的快照；拒绝课程重标；结构与证据引用复用原推荐校验器；输出保留 observations/evidence 和 completed 状态 | partial/failed/invalidated/无状态/跨课程均有回归 |
| 未校验证据宣称 integrity_verified | 校验事件内容哈希、学生、课程并拒绝 fixture；评估与推荐导出显式返回 integrity_verified=false、source_verification_status=unverified_local_input，区分结构校验和权威来源认证 | 外部学生/课程、哈希变动、fixture 被拒绝；缺来源哈希不会被宣称可信 |
| 新证据工厂缺 submitted_at | 输出 submitted_at；未知时为 null，不捏造为接收时间 | 新工厂产物可被原推荐链完整消费 |
| 全部证据作废时 CLI 抛 KeyError | 空证据与正常分支共用同一报告结构，始终包含 revision/failed_steps | 实际 CLI main + 本地模型桩 + 临时 RunStore 完成并可恢复 |
| 混合时区事件逆序 | 按带时区时间值比较 | UTC 00:00 正确排在 UTC 01:00 前 |
| 草稿/未标来源序列冒充真实学情 | 未标来源或自称 real 的本地 JSON 返回 unverified 行为摘要，不生成 Good/Bad 认知判断；fixture 必须显式 synthetic | 未标来源、自称 real、草稿带 result、synthetic 重新标记等均回归 |
| 凭事件条数产生置信度 | 移除 high/medium 条数规则，temporal_confidence=none；单点 trend=insufficient_data | 三组合成样例均不产生已校准置信度 |
| 用 Good/Bad 次数投票知识点掌握 | S08 格式化器输出 observed/有观察记录，保留逐项数量和解释；失效视图不暴露旧卡片 | 单个 Bad 不再直接输出总体 Bad 标签 |
| 工作台 sequence 的 execute/preview 参数不匹配 | CLI 接受 --execute；默认 preview 不落盘；目录输出固定 sequence.json | 用实际 build_command 生成参数，再运行真实 CLI 子进程验证 |

此外：拒绝未生效评分进入当前画像、拒绝 NaN/Inf/bool 分值；校验序列窗口、步号顺序和非负有限时长；事件改动必须匹配内容哈希；同一改分事件重复应用被拒绝并指引使用已保存结果；恢复沿用已保存的失效快照和完成步骤，不改变历史标记。CLI 恢复身份补入模型 URL、max_tokens、timeout、操作原因与操作者，避免配置变化时误用检查点。

## 文件交接

修改：`profiling/adapter.py`、`events.py`、`evidence.py`、`reconstruction.py`、`sequence.py`、`synthetic_sequence.py`，根 `reconstruct_profile.py`、`run_sequence.py`，`webapp/commands.py`，`tests/test_research_two.py`。

新增：`tests/test_profiling_integration.py`，22 项跨模块及 CLI 回归。

未修改任何 Demo 页面、Demo API 或 Demo 数据库文件；这些由主代理和其他组员处理。

## 函数及命令使用变化

1. 修订事件必须是完整 LearningEvent，包含 event_id、student_id、course_id、timestamp、payload、provenance、event_sha256。可用 `create_learning_event` 构建本地结构；哈希仅表示内容一致性，不证明操作者身份。
2. `grade_revised` 必须包含已生效的 revised_grade；单条作答作废使用新增 `submission_voided`。事件课程必须匹配画像绑定课程；旧根画像没有 course_id 时需由本地操作者显式绑定，不能伪装成已获得在线授权。
3. `reconstruct_profile.py --revoke-record-id ...` 自动生成 submission_voided 事件；旧画像可显式传 `--course-id ...`。默认操作人改为 `local-unverified`，不再默认冒充具体教师。预览不读模型配置，`--execute` 才进入运行。
4. `run_sequence.py --generate-synthetic` 现在默认预览；落盘须添加 `--execute`。分析也默认预览，添加 `--execute` 才写 `--output`，目录形式产物为 `sequence.json`，显式 `.json` 路径仍兼容。
5. `export_profile_for_recommendation` 返回完整原始画像结构及 completed 状态，不再仅返回 summaries 的 active DTO。这只是本地结构可消费性，仍没有数据库 profile_version_id / 权威来源认证。

## 给“本版功能”预览页的数据约定

`generate_synthetic_sequences()` 保持 3 组返回值。提交动作与模拟评分反馈已分开，因此动作数比原提交多。

`compute_sequence_trajectories()` 保留原 `concept_trajectories`、`history`、`trend` 等字段，新增/明确：

```json
{
  "data_source": "synthetic_demo_data",
  "is_synthetic": true,
  "source_verification_status": "synthetic_fixture",
  "learning_evidence_eligible": false,
  "method_kind": "synthetic_rule_illustration"
}
```

轨迹里的 `temporal_confidence` 为 `none`；单点趋势为 `insufficient_data`。这些是合成评分结果的规则变化示意，应显示“模拟结果变化”，不能表述为真实掌握提升或研究效果已验证。真实或未标来源的输入在本阶段仅输出未验证行为摘要；接入可信事件与证据以后，再建设真实时序推断。

## 实测结果

在上述集成工作树执行：

```bash
PYTHONPATH=tests PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3.10 -B -m unittest \
  test_profiling_integration test_research_two test_recommendation \
  test_student_profile test_profile_update
```

结果：**Ran 59 tests in 0.894s — OK**。构成：新增集成回归 22 项、原研究二样例 11 项、原推荐 13 项、原画像 8 项、原增量画像 5 项。

`git diff --check` 对本代理负责的文件通过。测试采用明确标记的本地模型桩和临时目录，包含原推荐消费链、CLI 子进程和临时本地 SQLite 检查点；没有连接业务数据库或真实服务。

## 本版仍未完成的边界

- 没有 HTTP 鉴权、真实事件订阅、权威来源核验、LearnHouse profile_version 落库或当前版本指针切换；本地函数不会自动使在线旧画像失效。source_verification_status 的 unverified 是有意保留的真实状态。
- 改分结构和生命周期已修复，但原画像模型仍主要分析 Java 作答。教师分数尚未作为可信多源观测进入新的研究模型，不能宣称“改分已完成认知纠错”或“多题型画像算法完成”。
- 还需与 C 对齐数据库画像版本、method_kind 与研究字段绑定；当前只证明能够被原根 prepare_context 消费，不代表新版 DB 推荐链已联通。
- T11 的名单由可信调用方提供，格式化函数本身不是教师身份鉴权 API；S08/T11 的实际页面与权限接线未由本代理实现。
- 时序算法目前只保留合成规则演示，没有新增学习概率、掌握阈值或未校准的高置信度。真实时序模型、有效评分语义、实验/消融和评估闭环属于下周工作。

原 fork 的 README 与契约文档仍包含“已严格权限隔离/完整集成”等过度表述；阶段交付应以本报告和主代理 V2.1 说明为准，并在下一轮统一更新组员文档。
