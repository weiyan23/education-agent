# 教育智能体 V2 本地运行说明

**V2.1 阶段集成补充（2026-11-26）**：先下载整个 `integration/demo-v2.1-2026-11-26` 分支，并进入 `demos/learnhouse-v2` 再执行下文命令。新下载的源码不包含本机私有运行环境；先执行 `doctor`，缺少依赖、数据库或账号时按第二节初始化独立环境。不要将“当前工作区已配置”理解为新工作树也已配置。旧实例和新实例不可同时占用相同端口；本次集成未替换旧实例。

新增入口：教师/管理员登录 → 左侧“本版功能” → 切换三个合成时序样例。该预览不调用模型、不写画像/推荐/成绩。教师“练习准备”仍以课程返回的能力为准；未配置模型时不显示真实模型选项。推荐自动任务尚未开放，注册请求明确返回 `RECOMMENDATION_EXECUTION_UNAVAILABLE`；正常课程教学与规则统计保留。

V2.1 研究模块依赖仓库根目录，不能只复制 `demos/learnhouse-v2`。打包方式与本阶段验证边界见 `deliverables/06-integration-v21/README.md`。

本版基于真实 LearnHouse 源码改造，包含学生、教师、管理后台及 PostgreSQL 业务数据。它不是双击 HTML 即可运行的静态演示。无需 ChatGPT 登录，也不使用手机验证码；使用本机初始化的五个独立邮箱账号及随机密码。

## 一、当前工作区直接启动（已配置依赖）

在本文件所在目录执行：

```bash
python scripts/local_demo.py doctor
python scripts/local_demo.py start
```

打开 **http://localhost:3300/education/login**。请使用 `localhost`，不要将前端域名改成局域网 IP。

在 VS Code Remote/SSH 中运行时，通过“端口 / Ports”面板转发远程 **3300** 到本机 **3300**，保留远程连接，再在本机浏览器打开上面的地址。仅需转发前端端口。

本地启动器使用 Webpack 和 1 秒文件轮询，避免大型依赖目录耗尽 Linux 文件监听配额。首次编译可能稍慢；直接打开 `http://localhost:3300/` 也会进入本版登录页。请使用本启动器，不要同时运行上游 `bun dev`。

查看账号位置：

```bash
python scripts/local_demo.py accounts
```

在本机编辑器打开 `.runtime/demo-accounts.local.json` 读取随机密码。文件不进入源码包；密码不硬编码、不在网页公开，也不写到验收报告。初始化时会生成学生 A/B、教师 A/B、平台管理员五个账号。

停止本脚本启动的前后端：

```bash
python scripts/local_demo.py stop
```

数据库和 Redis 保留，学习历史不清空。`stop` 只停止本脚本登记且进程身份仍匹配的服务；其他方式启动的服务由其启动者停止。端口已被未知应用占用时，不要随意杀进程，先核对 `.runtime/demo-api.log` 和 `.runtime/demo-web.log`。

若旧进程显示 `Module not found`，并且具体原因包含 `OS file watch limit reached`，应先用更新后的启动器重启：

```bash
python scripts/local_demo.py stop
python scripts/local_demo.py start
```

然后浏览器按 `Ctrl+Shift+R` 强制刷新。该情况不等同于依赖未安装，不必直接删除 `node_modules` 或重装软件包。

## 二、在另一台电脑配置完整源码包

已验证的运行环境是 Linux x86_64。Windows 可在 WSL2 Ubuntu 中按以下步骤准备；**Windows/WSL2 新机安装、macOS、Docker 和生产部署尚未验收**。源码包不附带大型依赖、数据库或当前机器的私有凭据。

前置组件：Node 20.19.5（上游要求至少 20.9）、Bun 1.4.4、uv、Python 3.14.7（上游精确要求）、PostgreSQL 14+ 配合 pgvector、Redis。当前实测 PostgreSQL 14.24 / pgvector 0.8.1 / Redis 6.0.16。不要擅自修改 API 的 Python 版本范围。完整空库初始化实际依赖 pgvector，即使上游部分提示将它标为可选。

1. 解压源码包；保留 `upstream/learnhouse`、`scripts`、`deliverables/02-design/schema.sql` 的相对目录。
2. 在包根目录准备后端依赖（首次安装需要访问软件源）：

```bash
mkdir -p .runtime
UV_PROJECT_ENVIRONMENT="$PWD/.runtime/api-venv" uv sync --project upstream/learnhouse/apps/api --frozen --no-dev --no-install-project --python 3.14.7
uv pip install --python .runtime/api-venv/bin/python -r requirements-demo.txt
```

3. 在 `upstream/learnhouse/apps/web` 目录安装前端依赖：

```bash
bun install --frozen-lockfile
```

完成后回到包根目录。完整 LearnHouse 前端依赖较多，不要将 `node_modules` 上传 GitHub。

4. 准备**独立空数据库** `learnhouse_v2_demo` 和独立 Redis 数据库。PostgreSQL 服务须安装 pgvector 扩展文件，并在该数据库启用 `CREATE EXTENSION vector;`；应用账号应为该专用数据库的 owner，具备初始化 schema/表的权限。不得连接已有生产库，不要复用真实教学数据。
5. 将连接串交互式保存到本地私有文件：

```bash
python scripts/configure_demo.py
```

输入 PostgreSQL URL（例如 `postgresql://用户名:密码@localhost:5432/learnhouse_v2_demo`）和 Redis URL（例如 `redis://localhost:6379/2`）。输入不回显，不放入命令行参数或 Git。配置写入 `.runtime/connections.local.json`。

6. 仅首次初始化执行：

```bash
.runtime/api-venv/bin/python scripts/prepare_demo.py
```

脚本先检查数据库为空，再初始化上游结构、验证本版 DDL 哈希、建立扩展结构及合成演示数据。数据库非空时会拒绝，防止覆盖；若初始化中途失败，查看本机异常原因后由负责人处理专用测试库，不要直接对已有库反复执行 DDL。

7. 运行第一节的 `doctor` 和 `start`。本版使用开发服务器方便二次开发；生产构建、HTTPS域名、反向代理、备份、负载和部署迁移属于后续独立阶段。

## 三、演示顺序

1. **教师 A**：我的课程 → 条件与分支 → 练习准备 → 选择资料、知识点 → 规则模板 → 查看候选 → 执行检查 → 教师确认 → 发布到 A 班 → 布置作业。
2. **学生 A**：我的作业 → 打开刚布置的作业 → 开始作答 → 保存草稿 → 提交 → 查看反馈。客观题按冻结答案确定性评分；混合题等待教师批阅。
3. **教师 A**：作业与批阅 → 答卷 → 逐题打分和反馈 → 保存评分并生效。需要更正时填写原因并追加修订。
4. **学生 A**：结果页申请复核；教师在同一答卷页维持或修订。学习状态显示有效成绩的统计，不显示未经验证的掌握概率。
5. **管理员**：账号管理、权限范围、教学策略、任务与能力、操作记录。管理员不能读取学生答卷/私人笔记正文。

预置两份混合练习可直接用于第 2–4 步。验收脚本会另建名称包含“验收演示”的作业，不覆盖预置练习。不同角色最好分别用独立浏览器窗口或浏览器配置文件；同一浏览器的所有标签页共享登录会话。

## 四、运行与复测约束

- 所有写请求要求合法 Origin、幂等键；需要状态前置的写请求还要求 If-Match。浏览器页面自动处理。收到版本冲突时刷新后核对当前内容，不能覆盖别人的修改。
- V2 模式关闭旧 LMS 教学/AI/代码接口及上游后台任务。本版须使用专用实例；不能把该开关直接套到正在服务其他用户的 LearnHouse 实例。
- 学生笔记页面支持本人新增和读取；教师阅读/画像用途授权及撤回已在API验收，但本版授权UI尚未接入。模型未接通，授权也不会自动启动分析。
- 模型 API key、短信、ChatGPT 网站登录均不是本地运行前提。AI 入口显示未配置；固定模板仅演示流程，不宣称已经实现多智能体或自适应教学效果。
- `requirements-demo.txt` 补充 jsonschema 请求校验依赖。重新运行 `uv sync` 后须再次安装该文件，避免未登记到上游锁文件的补充包被移除。
- 禁止把 `.runtime`、`.env`、数据库、Cookie、密码或真实学生材料打包/上传。

复测：先启动服务，再执行 `.runtime/api-venv/bin/python scripts/test_v2_demo.py`。测试会创建新的合成业务记录，保留原历史；详细命令与专项参数见 `deliverables/05-demo-acceptance/README.md`。报告中的“通过”只覆盖已记录场景，不代表生产或研究效果验收。
