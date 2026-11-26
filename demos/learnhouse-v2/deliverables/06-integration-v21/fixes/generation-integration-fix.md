# 生成任务后台身份修复交接

日期：2026-11-26。修改位置为独立集成工作树 `/home/hongjian/projects/education-agent/integration-v2.1-2026-11-26`。没有修改原 Demo 或审查 checkout，没有提交 Git。

已修复 A 分支确认的 P1：提交时的教师身份不再直接作为后台任务派发及结果导入的授权依据。

改动文件：

- `demos/learnhouse-v2/upstream/learnhouse/apps/api/src/education/security.py:48`：增加 `require_current_actor`，以原 principal/org/user 标识重新查询当前教师及组织成员关系、禁用状态、当前角色/grant、服务身份。请求入口与后台入口共用 `_actor_from_row`，保留原 HTTP 身份验证语义。当前身份与组织关系行以 `FOR SHARE` 锁定，在候选导入事务提交前不会释放。
- `demos/learnhouse-v2/upstream/learnhouse/apps/api/src/education/business_routes.py:322`：生成流程新增 `authorize_generation`；派发前、导入前均重新检查当前 Actor、当前策略、generate 权限及目标学生、源资料 read 权限/状态/哈希和配置有效性。实际候选校验使用重新加载的 Actor。未改变 write_once 幂等、任务状态锁、候选强制审核或取消后禁止结果应用的语义。
- `demos/learnhouse-v2/integration/tests/test_generation_authorization.py`：新增 10 项纯内存回归。执行实际安全函数与完整生成处理函数；替换数据库、模型和线程调度边界，不启动应用、不加载私有配置、不连接数据库或网络。

验证结果：

| 检查 | 结果 |
| --- | --- |
| `timeout 30 /usr/bin/python3.10 -B -m unittest discover -s demos/learnhouse-v2/integration/tests -p test_generation_authorization.py -v` | 10/10 通过：正常派发导入、提交后停用、运行中停用、退出组织、角色改变、撤销生成权、撤销资料读权限、策略变更、派发前取消、运行中取消。 |
| `/usr/bin/python3.10 -B -m unittest discover -s demos/learnhouse-v2/integration/tests -p test_generation_candidate.py` | 原候选回归 8/8 通过。 |
| `PYTHONPATH=.:tests /usr/bin/python3.10 -B -m unittest test_generation` | 原生成回归 12/12 通过。 |
| 将两个停用回归在内存中替换为 Git HEAD 的修复前 handler 后执行 | 两项均如预期失败：派发前停用仍调用一次、运行中停用后仍 succeeded；确认测试覆盖原缺陷。未修改源文件。 |
| `git diff --check` | 通过。 |

本次正常回归共 30 项通过；故意验证旧实现失败的 2 项不算通过数。首次采用真实 to_thread 的 Python 3.10 单测在测试事件循环关闭时挂起，已停止该进程；最终测试把线程调度替换为同步的离线边界，保留运行前/返回后两次身份变化场景。没有遗留测试进程。

残余边界：已派发的模型调用不承诺被身份停用即时中断；身份撤销会在结果导入前拦截，不写候选、不向学生发布。派发前检查会释放数据库事务后才运行外部模型，避免长时间锁住管理员操作；本次未做分布式取消或持久化工作队列改造。适配器内部固定 epoch/version 的通用回调没有在这次最小修复中重构，产品层继续按任务状态与两阶段实时权限校验阻止失效结果应用。没有付费模型、浏览器、真实 PostgreSQL 并发/锁和完整 HTTP 端到端复测；独立合成环境中的相关验收留待后续迭代。

业务路由文件也有其他组员的推荐改动；本次只改导入行和生成函数段，未改推荐、画像或 UI。
