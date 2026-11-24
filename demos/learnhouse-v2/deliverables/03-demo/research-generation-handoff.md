# 教学资源生成接入说明

## 合成联调的输入和操作

基线为 `7fda4a4`。按 RUN_LOCAL 准备独立的本地课程数据库，使用合成演示课程 A 和其中的学生 A。启动 API 前在同一个终端将 `EA_GENERATION_FIXTURE_COURSE_ID` 设为初始化文件 `.runtime/demo-accounts.local.json` 中 `objects.courses[0].id` 的整数值。已有 API 进程需要按 RUN_LOCAL 重启以读取配置。

以教师 A 登录并进入该 Java 课程，在生成练习页面选择“研究生成联调（合成样例）”，选已就绪资料、学生 A 和 `java.condition`。打开候选，核对初稿和修订稿、题面、样例及教师可见的参考解与六维核查。先通过结构校验，再由有权限的教师审核并发布。学生 A 打开已发布资源时取得题面和样例。初稿和受限材料仍由教师权限控制。

## 接口和版本

- 沿用 `POST /courses/{course_id}/generation-runs`。在显式配置的课程中，`mode=research_generator`、`run_mode=fixture` 请求使用 `budget_limit=0`、`max_attempts=1`、`max_calls=4`。四条固定响应驱动原生成工作流的两次生成和两次审核。调整练习要求不会改变固定题内容。所选资料用于来源和版本绑定。
- 沿用任务与资源读取、结构校验、人工审核、发布接口。任务返回候选引用和 `run_mode=fixture`，教师资源查询可读取 `candidate_history`。候选保存课程、来源版本、目标、任务、前后资源版本和内容哈希。生成产物哈希与各版题稿哈希保存在内部来源记录。模型响应与候选标记为合成。研究候选发布保持强制人工审核。
- C 使用公开发布状态、课程与班级访问范围、资源版本及知识点 ID。D 使用资料版本、内容哈希及教学目标。本次扩展了本地请求和任务结果的 `run_mode`，并给教师资源详情增加可选版次字段，集成前需同步调用方。数据库表结构沿用现有版本。

## 使用已配置的模型出题

在启动 V2 的进程环境里设置 `EA_GENERATION_ENV_FILE`，值为原教育智能体模型配置文件的绝对路径。配置文件中的服务端密钥、HTTPS 地址和模型名称沿用原项目设置。由平台管理员在现有策略页面启用研究生成，并为组织及相应课程配置正数的任务审批额度。交付的合成课程默认额度为零。重启服务后，有生成权限的教师会看到「模型生成 Java 练习」。

选择允许访问的学生、课程资料及原知识目录支持的目标。资料正文必须非空，当前接口接受最多 3500 字符。填写教师要求、审批额度和 1–8 次模型请求上限，确认后提交。生成页进入任务详情，刷新即可查看结果。审批额度按课程策略校验，实际可控的运行上限是模型请求次数及原命令的输出 token 限制。供应商账单金额由供应商结算。

执行沿用 `run_mode=execute`，请求额外传 `confirm_paid=true`。服务端先保存任务和幂等记录，再在后台调用原 `generate_question.py`。使用同一个幂等键重试只查询原任务。进程中断且结果未明时，任务超过有效期显示结果未知，由维护者核对运行记录。候选导入时再次检查权限、资料版本和课程策略，随后走人工审核和发布。运行产物保存在服务账号的 `~/.local/state/education-v2-generation/`。模型输入包含所选课程资料和教师要求，学生账号在研究流程中使用平台生成的内部标识。

在专用合成数据库中，从 Demo 目录运行 `.runtime/api-venv/bin/python -B scripts/test_generation_execute_offline.py`，可用原命令和离线模型响应复测真实执行入口、教师确认、重复提交及候选审核门禁。该测试会创建带模拟来源的待审核候选。

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
| 真实模型单题验收 | 通过 | 2026-11-25，经授权在本地合成课程试运行。Luna 一次生成、一次审核，共 2 次请求；审核后发布与学生权限核查 14 项通过 |
| 学生 Java 执行、双闭环运行 | 未执行 | 参考解除静态核对外未运行，双闭环为接口设计 |

单题验收使用独立进程临时启用研究生成，保留当前演示服务的课程额度。任务 `2a68ba3d-3a27-4d6d-b1a3-6de9baf0583e` 生成《判断水的状态》，出现 `0 → ICE`、`8 → WATER`。学生适配未提供画像，模型记为不确定。题面把水的状态简化为 0 度阈值，发布范围为合成 Java 班级的程序逻辑验收。实际费用未从供应商账单核对。发布核查报告位于验收环境 `.runtime/generation-paid-publication.json`。

首次在 WSL2 初始化时，Windows 产生的 CRLF 换行使 `schema.sql` 的字节哈希不匹配。核对 LF 文本的 SHA-256 与交付清单一致后，只在新建的专用库继续初始化。原有初始化脚本遇到同样的 CRLF 文件仍会拒绝。

V2.1 和最新版研究分工周计划仍待对照。共享请求扩展与输出字段待集成协调。
