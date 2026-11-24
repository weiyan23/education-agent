# 教学资源生成接入说明

## 合成联调的输入和操作

基线为 `7fda4a4`。按 RUN_LOCAL 准备独立的本地课程数据库，使用合成演示课程 A 和其中的学生 A。启动 API 前在同一个终端将 `EA_GENERATION_FIXTURE_COURSE_ID` 设为初始化文件 `.runtime/demo-accounts.local.json` 中 `objects.courses[0].id` 的整数值。已有 API 进程需要按 RUN_LOCAL 重启以读取配置。

以教师 A 登录并进入该 Java 课程，在生成练习页面选择“研究生成联调（合成样例）”，选已就绪资料、学生 A 和 `java.condition`。打开候选，核对初稿和修订稿、题面、样例及教师可见的参考解与六维核查。先通过结构校验，再由有权限的教师审核并发布。学生 A 打开已发布资源时取得题面和样例。初稿和受限材料仍由教师权限控制。

## 接口和版本

- 沿用 `POST /courses/{course_id}/generation-runs`。在显式配置的课程中，`mode=research_generator`、`run_mode=fixture` 请求使用 `budget_limit=0`、`max_attempts=1`、`max_calls=4`。四条固定响应驱动原生成工作流的两次生成和两次审核。调整练习要求不会改变固定题内容。所选资料用于来源和版本绑定。
- 沿用任务与资源读取、结构校验、人工审核、发布接口。任务返回候选引用和 `run_mode=fixture`，教师资源查询可读取 `candidate_history`。候选保存课程、来源版本、目标、任务、前后资源版本和内容哈希。生成产物哈希与各版题稿哈希保存在内部来源记录。模型响应与候选标记为合成。研究候选发布保持强制人工审核。
- C 使用公开发布状态、课程与班级访问范围、资源版本及知识点 ID。D 使用资料版本、内容哈希及教学目标。本次扩展了本地请求和任务结果的 `run_mode`，并给教师资源详情增加可选版次字段，集成前需同步调用方。数据库表结构沿用现有版本。

## 双闭环接口设计

两条接口均为设计。执行身份、预算和材料版本由服务端冻结。状态为 `completed`、`budget_exhausted`、`cancelled`、`stale` 或 `failed`。达到任务设定的通过条件、达到预算、取消或授权与输入版本失效时结束。每条建议附材料版本及位置，并以 `simulated` 标记。生成端只接收授权范围内的建议及引用，保存所用请求和输出版本。模拟结果不写入学生画像证据。

教学方案推演示例

```json
{"course_id":"course-java","material_version_ids":["m1"],"goal_ids":["java.condition"],"plan":"先解释阈值，再用样例检查等号边界","max_rounds":2}
```

```json
{"status":"completed","rounds_used":1,"stop_reason":"goal_checks_satisfied","provenance":"simulated","suggestions":[{"text":"补充阈值相等的样例","material_version_id":"m1","location":"条件判断示例"}]}
```

认知双身预学习示例

```json
{"course_id":"course-java","learner_snapshot_id":"authorized-snapshot-v1","material_version_ids":["m1"],"goal_ids":["java.condition"],"max_rounds":2}
```

```json
{"status":"completed","rounds_used":1,"stop_reason":"target_coverage_satisfied","provenance":"simulated","suggestions":[{"text":"练习区分大于和大于等于","material_version_id":"m1","location":"阈值定义"}]}
```

## 验收命令与记录

在仓库根目录执行 `python -B -m unittest discover -s demos/learnhouse-v2/integration/tests -p test_generation_candidate.py`，覆盖生成、六维审核、一次修订、权限失效、产物篡改和受限内容划分。V2 在独立环境启动后，在 Demo 目录执行 `python -B scripts/test_generation_fixture_http.py --output .runtime/generation-fixture-http.json`，用真实登录、API 和 PostgreSQL 验证版本回放、拒绝未审核发布和发布后学生可见性。另按需求表 AC03、AC04、AC05、P07、P08、P09 复核生成、授权及固定模板，并以《ai协作》研究内容一核对模拟来源、版本追踪、运行说明、交接字段和接口设计。

| 实际核查 | 状态 | 依据 |
|---|---|---|
| 研究出题回归 | 通过 12 项 | Windows 用仓库原有 `tests/test_generation.py` 运行 |
| 适配器与候选流转 | 通过 35 项 | Windows 将测试的解释器和研究目录指向本机，覆盖真实 CLI 预览及固定响应生成；网络连接在候选测试中被拦截 |
| V2 真实登录、业务 API、数据库、审核发布 | 通过 23 项 | Ubuntu 24.04 / WSL2 专用合成库。核查了版本、重复请求、待审核和跨班拦截。报告为 `.runtime/generation-fixture-http-wsl-01.json` |
| V2 既有业务与固定模板回归 | 通过 139 项 | 同一 WSL2 环境。报告为 `.runtime/v2-baseline-http-wsl-01.json` |
| 浏览器实操 | 通过这条合成流程 | 2026-11-25，乐磊钧在浏览器确认了教师生成并发布「温度提醒」，以及学生 A 可查看已发布题目、看不到教师专用内容。数据库另核对到第 2 版发布记录 |
| 付费生成、学生 Java 执行、双闭环运行 | 未执行 | 本轮采用合成样例，双闭环为接口设计 |

首次在 WSL2 初始化时，Windows 产生的 CRLF 换行使 `schema.sql` 的字节哈希不匹配。核对 LF 文本的 SHA-256 与交付清单一致后，只在新建的专用库继续初始化。原有初始化脚本遇到同样的 CRLF 文件仍会拒绝。

V2.1 和最新版研究分工周计划仍待对照。共享请求扩展与输出字段待集成协调。
