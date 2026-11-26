# 研究内容二：基于实时学情与动态行为序列的学生画像

## 1. 模块定位与核心功能
本模块负责教育智能体系统中的**研究内容二**，核心功能是**“根据用户学习事件与多源证据生成、更正并输出学生画像”**。

### 核心特性
1. **多源证据接入（Evidence Ingestion）**：统一 Java 代码作答、客观题选择、教师人工评分与工具执行观测的证据格式，严格隔离真实证据与模拟数据。
2. **学习事件流（Learning Events）**：追踪全生命周期的 8 类学习事件（提交、自动批阅、教师改分、复核、入班/撤权等）。
3. **改分与撤权失效重建（Invalidate & Rebuild）**：改变传统“只增量追加不纠错”的弊端。当发生改分或撤权时，标记旧画像失效并阻断下游推荐，基于纯净有效证据集显式重建新基线。
4. **动态行为时序轨迹（Dynamic Sequence Modeling）**：捕捉学生的连续行为时序（阅读、草稿、耗时、试错、订正），推断知识点定性演变趋势（`improving`/`regressing`/`stable`/`fluctuating`），绝无虚假概率。
5. **上下游交接与权限控制**：
   - 向**自适应学习推荐（模块 C）**交付具备版本指纹的有效画像（失效时自动阻断推题）；
   - 向**序列教学质量评估（模块 D）**交付带来源哈希的有效证据引用；
   - 为**学生端 S08（我的学习状态）**与**教师端 T11（班级学情）**提供脱敏且遵循 P15 权限的视图数据。

---

## 2. 代码与测试文件清单

### A. 核心实现代码文件
* `profiling/events.py` **[新增]**：统一学习事件契约，定义 `LearningEvent`、`create_learning_event()`、8 种核心事件类型及范围过滤。
* `profiling/evidence.py` **[增强]**：扩展多源证据模型，提供 `create_evidence_packet()`、支持多题型 `evidence_kind`、有效评分 `effective_grade`、生命周期状态 `lifecycle_status` 及作废函数 `invalidate_evidence()`。
* `profiling/reconstruction.py` **[新增]**：画像失效与显式重建算法，实现 `invalidate_profile()`、`rebuild_profile()`、`reconstruct_profile()`。
* `profiling/sequence.py` **[新增]**：动态行为序列规范与轨迹评估器，包含 Schema `dynamic-learning-sequence-input-v1` / `output-v1` 及 `compute_sequence_trajectories()`。
* `profiling/synthetic_sequence.py` **[新增]**：3 种典型教学时序场景生成器（常规递进掌握、持续试错误解、改分订正恢复），显式打标 `synthetic_demo_data`。
* `profiling/adapter.py` **[新增]**：上下游交付与页面适配层，包含 `export_profile_for_recommendation()`、`export_evidence_for_evaluation()`、`get_learning_state_for_s08()`、`get_class_learning_summary_for_t11()`。
* `profiling/__init__.py` **[增强]**：使用 Python 惰性加载机制对外导出核心 API，避免与外部生成模块产生循环导入。

### B. 独立 CLI 命令行入口
* `reconstruct_profile.py` **[新增]**：显式画像重建命令行工具，支持 `--base-profile`、`--revoke-record-id`、`--reason` 与预览/执行模式。
* `run_sequence.py` **[新增]**：时序分析与生成命令行工具，支持 `--generate-synthetic` 与 `--sequence-file` 轨迹解析。
* `webapp/commands.py` **[增强]**：将 `reconstruct` 与 `sequence` 两个命令注册进本地 Web 工作台调度元数据。

### C. 自动化测试文件
* `tests/test_research_two.py` **[新增]**：针对本模块的完整单元与集成测试套件，覆盖事件流、评分校验、失效重建、时序轨迹推断及适配器鉴权。

### D. 数据与契约规范文档
* `docs/research-content-2-contract.md` **[新增]**：详细的接口字段定义、时序 Schema、生命周期状态机及上下游交接规范。
* `README_RESEARCH_TWO.md` **[新增]**：本说明文档。

---

## 3. 运行与测试说明

### 3.1 运行本模块自动化测试
在项目根目录运行：
```bash
python -m unittest tests.test_research_two
```
**预期输出**：
```text
...........
----------------------------------------------------------------------
Ran 11 tests in 0.002s

OK
```

### 3.2 运行系统全量回归测试（验证 0 回归）
```bash
python -m unittest discover tests
```
**预期输出**：
```text
Ran 200 tests in ~24s

OK (skipped=3)
```

### 3.3 命令行运行动态时序生成与轨迹分析
1. 生成 3 组标准合成时序数据集：
   ```bash
   python run_sequence.py --generate-synthetic
   ```
   产物输出至：`runs/synthetic-sequences/`
2. 分析场景一（常规递进掌握）并输出轨迹报告：
   ```bash
   python run_sequence.py --sequence-file "runs/synthetic-sequences/scenario_1_seq_synth_prog_student_synth_01.json" --output "runs/test_seq_out.json"
   ```

### 3.4 命令行测试画像改分失效与重建（安全预览）
```bash
python reconstruct_profile.py --base-profile "runs/sample_base_profile.json" --revoke-record-id "e1" --reason "T10教师改分作废"
```

---

## 4. 模块交付与下游对接规范

### 4.1 对接接口摘要
* **自适应学习推荐（模块 C - 盛瑞轩）**：
  * 调用接口：`export_profile_for_recommendation(profile, student_id, course_id)`
  * 交付保障：画像失效时（`is_active == false`）自动阻断推荐推题，向 C 端返回受限原因；仅向 C 端提供包含完整版本指纹、来源哈希与有效证据计数的画像。
* **序列教学质量评估（模块 D）**：
  * 调用接口：`export_evidence_for_evaluation(profile, events, student_id)`
  * 交付保障：下发当前有效状态的全部证据清单及来源哈希引用，确保评估基于不可篡改的证据链。
* **前端展示页面（S08 / T11）**：
  * S08（我的学习状态）：调用 `get_learning_state_for_s08()`，输出知识点定性状态（良好/需巩固/待评估/无观察）与脱敏后的证据抽屉信息。
  * T11（班级学情与学生详情）：调用 `get_class_learning_summary_for_t11()`，严格执行班级成员与 P15 权限边界隔离。

### 4.2 核心验收标准达成核对
1. **旧画像不被覆盖，失效结果不能作为当前有效结果**：
   * 改分或撤权时，旧画像只读归档并标记 `is_active: false`，记录 `invalidation` 审计原因；
   * 重建过程基于纯净的有效证据子集生成带有 `supersedes_profile_sha256` 的新基线，旧版本与新版本隔离存储。
2. **未知状态不能伪造成掌握概率，模拟数据不能进入真实证据集**：
   * 知识点状态坚守 `Good / Bad / Unknown / no_observation` 四态定性边界，绝不输出虚假掌握概率百分比；
   * 底层证据接入层校验强行拦截非真实作答，合成仿真序列显式打标 `synthetic_demo_data`，物理阻断进入真实训练/评测集。

