# 推荐模块 V2.1 阶段集成修复

工作树：`/home/hongjian/projects/education-agent/integration-v2.1-2026-11-26`。日期：2026-11-26。未提交 Git，供主代理统一审查、提交。

## 已完成

1. `demos/learnhouse-v2/upstream/learnhouse/apps/api/src/education/recommendation_mapping.py`
   - 增加可离线验证的 `attempt_option`，普通作答只计算未绑定 grant 的尝试次数。
   - 普通窗口结束或次数用尽后，允许仍有效、未撤销且有剩余额度的补交/订正授权。
   - grant 额度与开始作答接口保持一致，采用 `max(grant_slot)+1`，不是简单计数。
   - 现有草稿必须仍可提交；普通有效草稿、有效 grant 草稿可以续写，即使其最后一个额度已被分配。
   - 不用另一份 grant 掩盖现有无效草稿，因为当前开始作答接口会直接复用该草稿。
   - SQL 继续过滤当前学生作用域、发布版本和资源状态；查询数据库时间及资格数据，在纯函数中判断。返回前删除内部尝试/授权集合。
   - 每个可用映射新增 `start_option = {kind, attempt_id, resubmission_grant_id}`。`kind` 为 `normal`、`grant`、`resume`。主代理负责在两个推荐打开路由携带此字段，并使前端以重新校验后的值开始作答。

2. `demos/learnhouse-v2/upstream/learnhouse/apps/api/src/education/recommendation_import.py`
   - V2.1 尚无生产调度器，注册接口在学生本人/课程作用域检查后返回 `503 RECOMMENDATION_EXECUTION_UNAVAILABLE`。
   - 不再新增无人执行的 queued 任务；未加一个能够虚假开启执行的环境变量。
   - 已有任务的可信产物导入、推荐读取和成功回执路径保持原校验。

3. `demos/learnhouse-v2/integration/tests/test_recommendation_availability.py`
   - 新增 24 项离线回归，其中资格/可用性 15 项、实际推荐打开路由 9 项。覆盖正常次数、普通截止时间、补交/订正授权、撤销/过期/未来/耗尽授权、独立配额、grant 槽位、草稿续写、无效草稿、时间边界、503 无写入、跨学生拒绝、旧任务回执、映射过滤及内部字段不外露。
   - 加载实际生产函数，仅替换认证/数据库基础设施；不运行 HTTP 服务、SQL、模型或学生代码。

4. `demos/learnhouse-v2/scripts/check_recommendation_import.py`
   - 隔离数据库测试调整为先验证生产注册接口返回 503，然后只在它新建的临时库中准备既有导入任务。
   - 其 fixture 数据准备明确不是生产调度方案。此脚本本次未运行。

5. 根 `README.md`
   - 修复任务三全部 17 个本地文件链接，补齐根目录运行的验证脚本路径。
   - 明确 V2.1 自动调度未接通、只读/可信产物导入边界、已存在无效草稿的限制。
   - 将原分支验收描述标为历史记录，不冒充 V2.1 新验收；补充本次离线回归命令。

## 本次验证

```text
/usr/bin/python3.10 -B -m unittest discover -s demos/learnhouse-v2/integration/tests -p 'test_recommendation_*.py' -v
Ran 34 tests in 0.256s — OK
```

其中新增 24 项 + 原画像输入 10 项全部通过。4 个改动 Python 文件使用 `ast.parse` 语法检查通过；README 任务三 17 个本地链接均存在；`git diff --check` 通过。

主代理接通两个推荐打开路由后，本次另外复查了路由和 `Binding.start`：前端重新读取推荐入口后使用本次 `start_option`，续写匹配指定 draft，普通/授权开始使用指定 grant ID 或 null。9 项新测试通过 AST 提取并执行实际路由函数体，确认三种 option 透传、仅使用匹配 binding、画像/推荐/资源/发布版本失效拒绝、无资源不回落普通作业，以及最终 binding 访问复核。测试不启动 FastAPI、不绕行待测函数体；明确不覆盖装饰器、真实认证及数据库行为。

## 阶段限制与后续

- 本次未访问已有数据库/私人配置，未调用模型、Java，未运行带写入的 PostgreSQL 验收脚本。
- SQL 查询本身、真实登录、浏览器、并发撤权与提交流程仍须主集成阶段验证；纯函数通过不代表完整数据库验收。
- 推荐打开路由与前端的 `start_option` 已由主代理贯通，本次源码复查与路由函数离线回归通过；浏览器 mock 由主代理另行完成。普通作业页面已有的“默认取第一个 grant”逻辑不在本次负责文件范围内。
- 无效旧草稿不会被推荐成可作答；如何关闭旧草稿或转移至新授权，需要单独设计，不在本次静默改变提交语义。
- 画像完整产物与业务 DB 桥接、预算授权、调度/取消/恢复、真实 B 执行与自动导入仍留待下一阶段。
