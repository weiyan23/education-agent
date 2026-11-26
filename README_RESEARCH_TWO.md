# 研究内容二：学生画像与行为序列 · V2.1 阶段交付

更新：2026-11-26。本说明以 V2.1 修复后的代码为准，详细字段见 [接口与契约](docs/research-content-2-contract.md)。

**当前交付是根研究包、CLI 和离线回归。** 已修复改分重建、课程撤权、输出有效性和模拟数据边界；尚未完成真实事件消费、LearnHouse 画像版本落库、S08/T11 页面与受认证接口。数据格式化函数不是权限服务，合成序列不是研究效果验证。

## 已完成的范围

| 文件 | V2.1 实现 |
|---|---|
| `profiling/events.py` | 9 类事件枚举、结构与内容哈希检查、学生/课程过滤、带时区排序；包括新增 `submission_voided` |
| `profiling/evidence.py` | 证据类型与评分字段校验、有效性检查；新 live 证据携带 `submitted_at`，未知值保留 null |
| `profiling/reconstruction.py` | 显式重建；有效证据进入 `evidence`，旧修订留在 `evidence_history`；保留失效画像和事件指纹，支持本地检查点恢复 |
| `profiling/sequence.py`、`synthetic_sequence.py` | 3 组合成序列的规则演示；真实/未标来源 JSON 只产生未验证行为摘要 |
| `profiling/adapter.py` | 本地推荐/评估导出及 S08/T11 数据格式化；不提供 HTTP 鉴权或权威来源认证 |
| `reconstruct_profile.py`、`run_sequence.py`、`webapp/commands.py` | CLI 及旧本地工作台命令；默认预览，显式执行才落盘 |
| `tests/test_research_two.py`、`tests/test_profiling_integration.py` | 11 项基础样例、22 项边界与跨原推荐/CLI 回归 |

新增 `evidence_kind` 枚举不代表多题型画像算法已完成。原 `StudentProfile` 仍主要分析 Java 作答；教师评分目前参与有效性及修订记录，尚未成为新的多源认知模型。

## 运行与验证

在仓库根目录运行，以下合成序列命令不调用模型：

```bash
# 仅预览，不生成文件
python run_sequence.py --generate-synthetic

# 显式保存 3 组模拟数据至 runs/synthetic-sequences/
python run_sequence.py --generate-synthetic --execute

# 仅预览分析；追加 --execute 才写出 runs/sequence-demo/sequence.json
python run_sequence.py --sequence-file runs/synthetic-sequences/scenario_1_seq_synth_prog_student_synth_01.json --output runs/sequence-demo
```

画像作废/重建默认预览，不读取模型配置：

```bash
python reconstruct_profile.py --base-profile runs/sample/profile.json --course-id java_basics --revoke-record-id e1 --reason "本地作答作废验证"
```

`--revoke-record-id` 生成 `submission_voided` 事件；改分使用 `--correction-event` 提供含有效 `revised_grade` 的完整事件。课程须与原画像一致；旧画像缺少 `course_id` 时可用 `--course-id` 显式绑定本地范围，这不代表在线授权。默认操作人是 `local-unverified`。重建 `--execute` 可能调用模型，须按项目预算与执行约束使用；输出目录须与旧画像目录分开。

本轮实测命令：

```bash
PYTHONPATH=tests PYTHONDONTWRITEBYTECODE=1 python -B -m unittest test_profiling_integration test_research_two test_recommendation test_student_profile test_profile_update
```

2026-11-26 在 Python 3.10 下 **59 项通过**（22 + 11 + 13 + 8 + 5）。使用模型桩和临时目录，覆盖“live 证据→画像→改分→原推荐 `prepare_context`”、撤权、导出边界、CLI 与恢复；没有连接真实模型、执行 Java 或操作业务数据库。

## 下游交接要点

- **推荐导出**：只接受 completed 且有效的画像；partial/failed/失效/撤权结果不可用于当前推荐。传入课程必须等于画像绑定课程。返回 completed 状态及完整当前 `evidence`、`observations`、`summaries`，可被原根 `prepare_context` 消费；指纹不是数据库 `profile_version_id`。
- **来源状态**：推荐及评估导出显式返回 `integrity_verified=false`、`source_verification_status=unverified_local_input`。结构、范围和哈希检查不证明身份或权威来源；本地 `is_eligible_for_recommendation` 不能替代新版数据库版本校验和在线授权。
- **撤权与历史**：课程撤权使本地结果 inactive/invalidated，禁止自动重新激活；历史证据单独保留。函数不会自动修改在线数据库中的旧版本或权限。
- **S08/T11**：S08 保留逐项观察与计数，使用“有观察记录”而非票数推断总体掌握；失效画像不返回旧卡片。T11 的名单必须由调用方先授权；当前函数本身不完成教师身份、班级归属或 P15 鉴权。
- **合成演示**：输出明确携带 `synthetic_demo_data`、`is_synthetic=true`、`learning_evidence_eligible=false`；`temporal_confidence=none`，单点趋势为 `insufficient_data`。只可称模拟结果变化，不能声称真实掌握提升。

下一阶段需接入真实事件与版本存储、完成页面/API 权限闭环，和 C/D 统一版本及有效证据契约，再验证多源画像、真实时序模型和研究效果。

## 历史提交记录

原作者记录为“200 项，`OK (skipped=3)`，其中新增 11 项”。这是修复前的历史自测记录，不等于 200 项全部执行通过，也不能证明完整功能或零回归。V2.1 本模块复核范围以以上 59 项和实际接线边界为准。
