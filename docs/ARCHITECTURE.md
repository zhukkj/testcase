# TestBrain 项目技术文档

> 代码走读版，基于当前工作区代码（2026-11-21）。
> 运行环境实测：Python 3.12.15 / Django 6.0.2 / MySQL 8 / Milvus 2.4.5 / BGE-M3。
>
> 说明：`langgraph`、`GitPython`、`pandas` 仍在 `requirements.txt` 中，但代码里已无引用
> （原先只被已移除的 Java 源码分析模块使用），可安全清理。

---

## 目录

1. [项目功能概述](#1-项目功能概述)
2. [技术栈说明](#2-技术栈说明)
3. [代码逻辑逐层梳理](#3-代码逻辑逐层梳理)
4. [整体架构与数据流](#4-整体架构与数据流)
5. [关键设计决策与已知问题](#5-关键设计决策与已知问题)
6. [附录：路由表与配置项](#6-附录路由表与配置项)

---

## 1. 项目功能概述

### 1.1 一句话定位

TestBrain 是一个**面向测试团队的 AI 用例生产流水线**：把「需求文档 / 接口定义」变成结构化测试用例，用 AI 先做一遍评审，再用人工评分反向校准 AI 的评审可信度，全程可以按团队角色做数据隔离。

它不是一个「聊天框套壳」，而是把 AI 嵌进测试工作流的具体环节：**需求进来 → 测试点 → 用例 → 评审 → 复核 → 导出**。

### 1.2 核心功能清单

| 功能 | 入口 | 输入 | 输出 | 依赖 |
|---|---|---|---|---|
| 知识库入库 | `/upload/` | docx / pdf / md（≤5 个，单个 ≤20MB） | Milvus 向量片段 + 文档清单 | BGE-M3、Milvus |
| 知识库管理 | `/knowledge/` | — | 文档列表、片段统计、检索调试 | Milvus |
| TestCase 生成 | `/test_case_generator/` | 需求文本、设计方法、用例类型、条数 | 结构化用例（可保存入库） | RAG + LLM |
| PRD 分析 | `/prd_analyzer/` | PRD docx | 测试点 + 测试场景（含优先级、汇总） | pandoc + LLM |
| 按测试点生成 | `/test_case_generator/generate-by-points/` | 测试点数组、每点条数 | 用例（带 `test_point_id` 回指）+ 覆盖率统计 | RAG + LLM + 去重 |
| 质量闭环 | 生成时开关 | 生成结果 | 自评分数 + 低分重生成 | LLM |
| 单条 AI 评审 | `/test_case_reviewer/` → 详情 | 单条用例 | 评分、优点、问题、结论，并回写状态 | LLM |
| 批量 AI 评审 | 同上「全部评审」 | 选中 id 或按状态全量 | 后台任务 + 进度轮询 | 线程池 + LLM |
| 人工评分 / 复核 | 列表内联打分、`/test_case_reviewer/invalid-cases/` | 0–10 分 | 与 AI 分对比，差值 ≥3 标「无效」 | — |
| 评审清单导入导出 | 复核页 | xlsx / csv / 粘贴文本 | 批量回填人工分 | openpyxl |
| 接口 case 生成 | `/iface_case_generator/` | MeterSphere 导出 JSON | 同结构 JSON（含新用例） | LLM |
| 知识库问答 | `/kb_chat/` | 自然语言问题 | 回答 + 引用片段 | RAG + LLM |
| 角色与隔离 | `/accounts/register/`、`/accounts/members/` | 账号、角色、开关 | 组长/组员权限、数据隔离 | Django Group |
| 测试报告 | `/` | — | 状态分布环形图、通过率、AI 覆盖率、人工偏差 | 聚合查询 |

### 1.3 它到底解决了什么问题

**问题一：用例编写是纯体力活，且质量参差。**
一份 PRD 动辄几十个测试点，人工写用例一天只能覆盖一部分。这里用「PRD → 测试点 → 用例」两段式生成，把「想清楚测什么」和「写成用例」拆开，每一段都可控、可统计覆盖率（`stats.coverage`）。

**问题二：AI 生成的用例没人敢信。**
单纯「让模型写用例」在团队里推不动，因为没人验证。项目做了三层可信度机制：
1. **知识库增强**：召回团队自己的规范和历史用例，让产出贴合内部约定；
2. **AI 自评 + 低分重生成**：把评审意见当下一轮的提示词，而不是直接丢弃；
3. **人工评分对照**：人工分与 AI 分差值 ≥ 3 分，判定这条用例的 AI 评审「不可信」，红下划线标出并进入复核清单。

第三层是这个项目最有意思的设计——它承认 **AI 评审会错**，并给出了可量化的发现机制：`score_gap = |ai_score - manual_score|`。

**问题三：团队协作时「谁的用例」说不清。**
做了组长/组员两种角色，可开关数据隔离（组员只能看自己生成的 + 无归属的历史数据），组长能管人、能删用例。破坏性操作（删除）在接口层二次校验，不依赖前端隐藏按钮。

### 1.4 典型使用场景

**场景 A：版本提测前的用例补充（测试组员）**

```
上传本版本 PRD 到知识库
  → 测试点&场景分析：抽出 18 个测试点
  → 用这些测试点生成用例：每点 2 条，覆盖 18/18
  → 保存入库（状态 pending）
  → 批量 AI 评审：通过 26 条、不通过 10 条，状态自动回写
  → 首页通过率从 0% 变成 72.2%
```

**场景 B：评审质量抽检（测试组长）**

```
打开「评审复核」→ 偏差清单按 score_gap 降序
  → 导出 xlsx 分给组内 3 个人线下打分
  → 收回后导入，系统自动重算无效标记
  → 发现某批用例 AI 给 8 分、人工给 4 分 → 说明这批生成质量有问题，回查 prompt 与知识库
```

**场景 C：接口回归用例批量铺开（接口测试）**

```
从 MeterSphere 导出接口定义 JSON
  → 勾选 12 个接口，每接口 3 条
  → 生成后下载 JSON，导回 MeterSphere 直接执行
```

**场景 D：新人查规范（任何角色）**

```
打开「知识库问答」，问「连续密码错误会被锁定吗」
  → 回答引用《测试用例编写规范V2.docx》片段，标注 [1]
  → 点开引用片段回查原文
```

### 1.5 能力边界（当前没做的）

- 没有 Celery / 消息队列，所有长任务都在 Web 进程内跑（线程或协程）；
- 没有多租户，`TeamSetting` 是全局单例，一个部署只服务一个团队；
- 接口用例、PRD 测试点、知识库问答都**不落库**，只有 `TestCase` 进 MySQL；
- 没有用例执行能力，导出的 JSON 需要到 MeterSphere 里跑；
- 没有审计表，操作痕迹只在日志文件里。

---

## 2. 技术栈说明

### 2.1 总览

| 层 | 技术 / 版本 | 用在哪里 | 承担什么职责 |
|---|---|---|---|
| 语言 | Python 3.12.15 | 全项目 | — |
| Web 框架 | Django 6.0.2 | `config/`、`apps/*/views.py` | 路由、模板渲染、ORM、会话与鉴权、CSRF |
| 模板 | Django Template + Bootstrap 4.6（CDN） | `templates/`、各 app 的 `templates/` | 服务端渲染页面 |
| 前端脚本 | 原生 JS（无框架） | `static/js/*.js`、各 app `static/*.js` | fetch 调接口、进度轮询、SSE 订阅、局部渲染 |
| 关系库 | MySQL 8 + `mysqlclient` 2.2.7 | `apps/core/models.py` | 用例、评审记录、团队配置持久化 |
| 向量库 | Milvus 2.4.5（Docker）+ `pymilvus` 2.6.8 | `apps/knowledge/vector_store.py` | 1024 维向量存储与余弦检索（HNSW） |
| 嵌入模型 | BGE-M3（`sentence-transformers` 5.2.2 + `torch` 2.10.0） | `apps/knowledge/embedding.py` | 文本 → 1024 维向量，支持本地/阿里云两种后端 |
| LLM 接入 | `langchain-core` 1.2.9、`langchain-openai` 1.1.8 | `apps/llm/` | 统一模型客户端，DeepSeek / 通义千问均走 OpenAI 兼容协议 |
| 文档解析 | `unstructured` 0.18.27、`python-docx` 1.2.0、`pypandoc` 1.16.2、`spacy` 3.8.11 | `apps/knowledge/milvus_helper.py`、`apps/utils/file_transfer.py` | 分块、docx→markdown、Confluence 导出清洗 |
| 表格 | `openpyxl` 3.1.5、`xlwt` 1.3.0 | `test_case_reviewer/views.py` | 评审清单导出（xlsx）、用例导出（xls） |
| 数据处理 | `numpy` 2.4.2、`pydantic` 2.12.5 | `apps/utils/dedup.py`、`progress_schema.py`、`iface_test_case_schema.py` | 余弦相似度矩阵、任务进度模型、LLM 输出校验 |
| 配置 | `python-dotenv`、`PyYAML` | `apps/llm/base.py`、各 `prompts.py` | 密钥读取、Prompt 模板外置为 YAML |
| 实时通道 | SSE（`StreamingHttpResponse`） | `apps/core/views_sse.py`、`apps/utils/sse_bus.py` | 把后端日志实时推到前端控制台 |
| 部署 | `manage.py runserver` / WSGI（`config/wsgi.py`） | — | 开发与演示；生产需换 gunicorn/uwsgi |

### 2.2 关键技术决策（以及为什么这么选）

**① 用 Django 而不是 FastAPI**
项目里 90% 的交互是「页面 + 表单式接口」，Django 的 admin、auth、ORM、模板、CSRF 全套开箱可用。要接 AI 的部分只占很小一块，用 FastAPI 得自己补鉴权、会话、模板，收益不大。

代价是异步支持半残：Django 的 ORM 在 async 视图里会抛 `SynchronousOnlyOperation`，所以只有生成页用了 async（为了 `ainvoke` 并发），并且专门写了 `async_login_required` 来绕坑（见 3.7）。

**② 不引入 Celery，用线程 / 协程**
批量评审 34 条、接口生成 12 个接口，量级是「分钟级」，没必要上 broker。两处实现方式不同：
- 生成用例用 `asyncio.Semaphore(3)` + `ainvoke`（IO 密集，协程更省资源）；
- 批量评审用 `ThreadPoolExecutor(4)` + 同步 `invoke`（LangChain 同步链路成熟，线程池改动最小）。

**③ 向量库选 Milvus**
要按 `source` 字段做整文档删除、要 HNSW 索引、要能脱离外部服务本地跑。Milvus standalone + Docker 一条命令起来，`pymilvus` 的 API 也够直接（`insert` / `search` / `delete(expr)`）。
注意版本配对：服务端 2.4.5，客户端 2.6.8，且 **2.6 的服务端不再支持 embedded etcd**，这是踩过的坑。

**④ Embedding 本地化 + 可切换**
默认本地 BGE-M3（1024 维，中文效果好、无需联网），通过 `EMBEDDING_PROVIDER` 可切到阿里云 `text-embedding-v4`（`AliyunEmbedder`，10 条一批、10 路并发）。
本地模型用 `KnowledgeConfig.ready()` 在进程启动时预热成单例，避免每个请求都加载 2GB 权重。

**⑤ Prompt 全部外置为 YAML**
`configs/prompt_config.yaml` + `prompts.py` 里的 `PromptManager` 负责把 YAML 里的 `role / capabilities / system_template / human_template` 组装成 `ChatPromptTemplate`。改提示词不用改 Python，也不用重启（每次构造 Prompt 对象时重新读文件）。

**⑥ 内存态的任务进度**
`progress_registry` + `sse_bus` 都是进程内字典。够用、零依赖，但**多 worker 部署会失效**（轮询打到另一个进程就查不到进度）。这是明确的取舍，不是疏忽。

---

## 3. 代码逻辑逐层梳理

### 3.0 目录总览

```
config/                     项目配置与入口
├── settings.py             全量配置（LLM、向量库、日志、角色、RAG 阈值）
├── urls.py                 根路由，按功能挂载各 app
└── wsgi.py / asgi.py       部署入口

apps/
├── core/                   业务核心：模型、首页、鉴权、团队、SSE
│   ├── models.py           TestCase / TestCaseReview / KnowledgeBase / TeamSetting
│   ├── views.py            首页统计、知识库 CRUD、文件上传入库
│   ├── views_auth.py       注册（含组长邀请码）
│   ├── views_team.py       成员管理、角色调整、数据隔离开关
│   ├── views_sse.py        SSE 日志流
│   ├── permissions.py      可见性过滤（数据隔离）
│   ├── roles.py            Django Group 封装的两种角色
│   ├── context_processors.py  把角色注入所有模板
│   ├── forms.py / admin.py 表单与后台
│   └── management/commands/init_team_accounts.py  初始化演示账号
├── llm/                    模型接入层
│   ├── base.py             AbstractChatModel + LLMServiceFactory
│   ├── deepseek.py         ChatOpenAI 子类（extra_body 透传）
│   ├── qwen.py             通义千问
│   ├── callbacks.py        LangChain 回调 → 统一日志
│   └── utils.py            按 Agent 取默认 provider
├── knowledge/              知识库
│   ├── apps.py             ready() 预热 embedder + vector_store 单例
│   ├── embedding.py        BGEM3Embedder / AliyunEmbedder / create_embedder
│   ├── vector_store.py     Milvus 封装（建表、插入、检索、按 source 删除）
│   ├── milvus_helper.py    多格式解析 + 按标题分块 + Confluence 清洗
│   └── service.py          KnowledgeService：入库/检索/重排
├── ai_agents/              业务 Agent
│   ├── common/base_prompts.py   Prompt 基类
│   ├── test_case_generator/     用例生成 + 按测试点生成 + 质量闭环
│   ├── test_case_reviewer/      单条评审 + 批量评审 + 复核/导入导出
│   ├── prd_analyzer/            PRD → 测试点/场景
│   ├── iface_case_generator/    MeterSphere 接口用例
│   └── kb_chat/                 知识库 RAG 问答
└── utils/                  横切能力
    ├── logger_manager.py    统一日志、脱敏、日志镜像到前端
    ├── progress_registry.py 任务进度（内存）
    ├── progress_schema.py   进度/日志的 Pydantic 模型
    ├── sse_bus.py           每任务一条队列的日志总线
    ├── auth.py              async 视图的 login_required
    ├── dedup.py             用例语义去重
    ├── review_parse.py      评审 JSON 解析 + 结论映射
    └── file_transfer.py     docx → markdown

templates/ + static/         全局模板与资源
samples/                     接口用例样例 JSON
docs/                        本文档
```

---

### 3.1 `config/` 配置与入口

#### `settings.py`

**输入**：无（Django 启动时读取）。
**处理**：
- 设置 `HF_ENDPOINT=https://hf-mirror.com`，让 `sentence-transformers` 走国内镜像；
- `TOKENIZERS_PARALLELISM=false`：tokenizers 用多进程，进程 fork 前调用会死锁，Django 启动即禁；
- `USE_TZ=False` + `Asia/Shanghai`：全站用本地时间，避免模板里到处 `localtime`；
- 四块业务配置：`LLM_PROVIDERS`（模型与参数）、`AGENT_LLM_DEFAULTS`（每个 Agent 用哪个 provider）、`VECTOR_DB_CONFIG`（Milvus 地址与 collection）、`EMBEDDING_*`（本地/阿里云）；
- 三个可调阈值：`RAG_MIN_SCORE=0.35`、`SCORE_GAP_THRESHOLD=3.0`、`KB_*` 上传限制（后两者在 views 里）；
- `LOG_*`：文件名带启动时间戳、10MB 轮转、保留 5 份。
**输出**：模块级常量，供全项目 `from django.conf import settings` 读取。

#### `urls.py`

**输入**：HTTP 路径。
**处理**：把 `accounts/`（含 auth 内置登录登出）、`''`（core）、以及 5 个 Agent 各自的前缀挂上去。
**输出**：URL 到 view 的映射。
**交互**：每个子路由文件都带 `app_name`，模板里用 `{% url 'test_case_reviewer:review' %}`。

---

### 3.2 `apps/core/` 业务核心

#### `models.py`

| 模型 | 关键字段 | 逻辑要点 |
|---|---|---|
| `TestCase` | title/description/test_steps/expected_results、status(pending/approved/rejected)、created_by、bu/feature/priority、manual_score/comment/reviewer/reviewed_at | 用例主体；`created_by` 是数据隔离的依据；`manual_*` 是人工评分 |
| `TestCaseReview` | test_case(FK)、reviewer(可空)、review_comments、review_date | AI 评审时 reviewer 为空；`review_comments` 存的是 JSON 字符串 |
| `KnowledgeBase` | title、content、vector_id | 手动添加的知识条目留痕（检索实际走向量库） |
| `TeamSetting` | data_isolation_enabled | 单例（pk 固定 1），`get_solo()` 保证不出多条 |

三个派生属性是这块的核心：

```python
latest_review  # 优先吃 prefetch 缓存，避免列表页 N+1；回落 order_by('-review_date').first()
ai_score       # 最近一次评审的 score
score_gap      # |ai - manual|，任一方缺失返回 None
is_invalid     # score_gap >= SCORE_GAP_THRESHOLD
```

`latest_review` 为什么不用 `reviews.all|last`：Django 模板的 `last` 过滤器对 QuerySet 做负索引会抛 `ValueError: Negative indexing is not supported`。

`parsed` / `score` / `recommendation`（在 `TestCaseReview` 上）：把 `review_comments` 反序列化成 dict，并兼容 `6` / `"6"` / `"6/10"` 三种分数写法。

**交互**：被所有 Agent 的 views 写、被首页与复核页读。

#### `views.py`

| 函数 | 输入 | 处理 | 输出 |
|---|---|---|---|
| `index` | 当前用户 | 用 `visible_test_cases` 取可见范围 → 统计四种状态 → 算通过率（只拿已评审做分母）→ 遍历评审记录算平均分/低分/覆盖数 → 算人工评分均值与无效数 → 自己算 SVG 环形图的 `dash/gap/offset` | 渲染 `index.html` |
| `knowledge_view` | — | 直接渲染 | 页面 |
| `knowledge_documents` | GET | `knowledge_service.list_documents()` | JSON |
| `delete_knowledge_document` | POST `{source}` | 按 source 删 Milvus 全部片段 | JSON + 删除条数 |
| `add_knowledge` | POST `{title, content}` | 交给 `KnowledgeService.add_knowledge`（切块 + 向量化 + 双写） | JSON + knowledge_id |
| `search_knowledge` | POST `{query}` | 向量检索，返回带分数的原始召回（不做阈值过滤，用于调试） | JSON |
| `upload_single_file` | GET / POST(files[]) | GET 渲染页面并带上限制常量；POST 逐个走 `_ingest_knowledge_file` | JSON：每个文件的片段数/错误/是否覆盖 |

`_ingest_knowledge_file()` 是上传的单文件流水线：

```
扩展名白名单 → 大小 ≤20MB → 同名则先删旧向量再删旧文件
→ 存盘 → process_singel_file 解析分块 → 过滤空文本
→ 批量 embed → 组装 Milvus 记录（chunk_id = md5(文件名)[:10] + 序号）
→ add_data 插入 → 返回片段数
```

**为什么同名要覆盖而不是报错**：反复上传同一份 PRD 是高频动作，报错体验很差；但覆盖必须同时清掉旧向量，否则会检索到过期内容。

#### `views_auth.py::register`

**输入**：username、display_name、password×2、role、invite_code。
**处理**：用户名正则（3–20 位字母数字下划线，禁中文）→ 唯一性（`iexact`）→ 角色合法性 → 组长需邀请码 → 密码用 `validate_password` → `create_user` → `first_name` 存显示名 → `assign_role` → 自动登录。
**输出**：302 到首页，或带错误回显的注册页。

**为什么组长要邀请码**：组长能删用例，不能让任何人自助拿到这个身份；`TEAM_LEAD_INVITE_CODE` 从环境变量读。

#### `views_team.py`

- `members_view`：组长专用；`annotate(case_count=Count('created_testcases'))` 一次查出每人用例数；附 `lead_count` / `member_count` / 隔离开关状态。
- `update_member`：三种 action（`role` / `toggle_active` / `reset_password`）。
- `toggle_isolation`：翻转 `TeamSetting.data_isolation_enabled`。

三条防锁死规则（都在服务端）：
1. 不能改自己的角色；
2. 不能停用自己；
3. 不能把最后一个在职组长降级/停用（`_active_leads()` 统计）。

#### `permissions.py`

```
visible_test_cases(user)
  ├─ 未登录 → none()
  ├─ 组长 / 超管 → 全部
  ├─ 未开隔离 → 全部
  └─ 开隔离 → Q(created_by=user) | Q(created_by__isnull=True)
```

`created_by__isnull=True` 这条是关键：历史用例没有归属，如果严格按 owner 过滤，一开隔离组员就会觉得「数据全丢了」。把无主数据当公共资产，是这里唯一合理的折中。

`can_see_case()` 是单条版本，用于详情、评审、人工打分这类按 id 取的场景。

#### `roles.py` / `context_processors.py`

角色用 Django `Group` 实现（`测试组长` / `测试组员`），不动内置 `User` 表。`get_user_role()` 的规则：超管一律视为组长 → 在组长组 → 在组员组 → 兜底按组员（最低权限）。

`context_processors.user_role` 把 `current_role / current_role_label / is_team_lead` 注入所有模板，侧边栏据此显示「admin · 测试组长」并控制「成员管理」入口。

**async 注意**：context processor 直接查 `user.groups` 在 async 视图渲染时会炸，所以优先读 `request._user_role`（由 `async_login_required` 预先算好）。

#### `views_sse.py`

**输入**：`GET ?task_id=`。
**处理**：`get_queue(task_id)` 拿到该任务的队列 → 生成器循环 `q.get(timeout=15)`：
- 拿到日志 → 输出 `id/event: log/data`；
- 超时 → 输出 `event: progress` 心跳，驱动前端刷新。
**输出**：`text/event-stream` 流。
**交互**：数据源是 `sse_bus`，写入方是 `logger_manager.ProgressMirrorHandler`（业务代码只管打日志，日志自动被镜像成 SSE 事件）。

---

### 3.3 `apps/llm/` 模型接入层

#### `base.py`

- `BaseLLMService(BaseChatModel)`：定义 `generate / generate_with_history` 抽象方法，并提供 `_log_request / _log_response / _log_error`（只记录前 100 字符，避免日志爆掉）。
- `LLMServiceFactory.create(provider, **config)`：
  1. 从 `settings.LLM_PROVIDERS` 取该 provider 的配置；
  2. API Key 优先取入参，其次环境变量 `{PROVIDER}_API_KEY`；
  3. 挂载 `LoggingCallbackHandler`；
  4. 合并配置（settings 为底，运行时覆盖）；
  5. **打印前对 api_key 打码**（这里曾经打过明文）；
  6. 按名字分派到 `DeepSeekChatModel` / `QwenChatModel`。

#### `deepseek.py` / `qwen.py`

都是 `ChatOpenAI` 的子类，只做一件事：把厂商特有参数塞进 `extra_body`（如 `enable_thinking`），其余走标准 OpenAI 参数。这样换模型不用改业务代码。

#### `callbacks.py`

`LoggingCallbackHandler` 实现 `on_llm_start/end/error`、`on_tool_*`、`on_chain_*`，用 `run_id` 记录耗时（`elapsed_ms`），输出统一走 `logger_manager`——所以模型调用的每一步都会出现在「日志文件 + 前端控制台」。

#### `utils.py`

```python
def get_agent_llm_configs(agent_name):
    ...
    agent_provider = agent_defaults.get(agent_name).get("provider", global_default_provider)
```

注意：`agent_defaults.get(agent_name)` 若为 None 会直接 `AttributeError`，所以**新增 Agent 必须同步在 `AGENT_LLM_DEFAULTS` 里加一行**。

---

### 3.4 `apps/knowledge/` 知识库

#### `apps.py`

```python
def ready(self):
    if KnowledgeConfig.embedder is None:     KnowledgeConfig.embedder = create_embedder()
    if KnowledgeConfig.vector_store is None: KnowledgeConfig.vector_store = MilvusVectorStore()
```

进程启动即预热：加载 BGE-M3（约 2GB）、连接 Milvus 并 `load()` collection。用类属性做单例，而不是实例属性。

#### `embedding.py`

- `BGEM3Embedder`：`SentenceTransformer.encode(normalize_embeddings=True)`，归一化后点积即余弦。
- `AliyunEmbedder`：10 条一批，`ThreadPoolExecutor(max_workers=10)` 并发调 REST，内置 3 次重试与错误详情打印。
- `create_embedder()`：按 `EMBEDDING_PROVIDER` 分派，本地默认读 `EMBEDDING_MODEL_PATH`（避免联网下载 2.2GB）。

#### `vector_store.py`

表结构（`vv_knowledge_collection`）：`id(auto)`、`embedding(FLOAT_VECTOR, dim=1024)`、`content(VARCHAR 4096)`、`metadata`、`source`、`doc_type`、`chunk_id`、`upload_time`。索引 HNSW + COSINE。

方法：
| 方法 | 说明 |
|---|---|
| `_ensure_collection()` | 不存在则建表建索引并 `load()` |
| `add_data(rows)` | 插入前对 `content` 做 4096 截断（超长会直接报错），`flush()` |
| `search(vec, top_k)` | COSINE + `ef=32`，返回带 score 的命中列表，结束时 `release()` |
| `list_sources()` | `query(expr='chunk_id != ""')` 后按 `source` 聚合，统计片段数与最后上传时间 |
| `delete_by_source(source)` | `delete(f'source == "{source}"')` 后 flush |

**为什么删除条数不靠 `num_entities` 差值**：Milvus 的 `num_entities` 在 flush 后仍有延迟，差值经常算出 0，改成删除前先查该 source 的片段数。

#### `milvus_helper.py`

`process_singel_file(path)` 按扩展名分派：
- `.docx` → 先判断是不是 Confluence MHTML 导出（读前 4KB 找 `Exported From Confluence` / `MIME-Version`）→ 是则走 `clean_confluence_content`（MIME 分段 → quopri 解码 → BeautifulSoup 去 style/script/img → 压空行）再用 `partition_text`；否则 `partition(strategy="fast")`；
- `.xls/.xlsx` → `partition_xlsx`；
- 其余 → `partition(strategy="fast")`；
- 统一 `chunk_by_title(max_characters=4000, combine_text_under_n_chars=500)`。

启动时还把 spaCy 的 `max_length` 调到 1000 万，避免长文档解析报错。

#### `service.py`

`KnowledgeService` 是业务层门面，所有检索/入库都从这里走：

```python
add_knowledge(title, content)         # 手动条目：1000 字符切块 → embed → add_data → MySQL 留痕
list_documents()                      # 文档清单 + 总数 + 片段数
delete_document(source)               # 先统计再删
search_knowledge(query, top_k)        # 原始召回（给调试页）
search_relevant_knowledge(query)      # 给 Agent 用：阈值 + 重排 + 去重 + 拼接
```

`search_relevant_knowledge` 的流程值得单独说：

```
embed(query) → search(top_k*3 ≥10)
→ 过滤 score ≥ RAG_MIN_SCORE
→ 按 (关键词命中数, score) 双键排序
→ 取 top_k，按 content[:80] 去重
→ "\n\n".join() 成一段文本
```

`_extract_keywords` 用中文 bigram（`"登录锁定"` → `登录/录锁/锁定`）而不是 `text.split()`——中文没空格，原实现整句当一个 token，`keyword in content` 永远不命中。

---

### 3.5 `apps/ai_agents/` 业务 Agent

#### 3.5.1 `common/base_prompts.py`

两个基类：`BasePromptManager`（提供 `_create_chat_prompt_template`）和 `BasePrompt`（只定义 `format_messages` 接口）。各 Agent 的 `PromptManager` 继承它，自己负责读 YAML。

---

#### 3.5.2 `test_case_generator/` 用例生成

**`views.py`**

| 函数 | 输入 | 处理 | 输出 |
|---|---|---|---|
| `generate`（async） | GET / POST JSON `{requirements, llm_provider, case_design_methods, case_categories, case_count}` | 校验需求非空 → 工厂建模型 → `TestCaseGeneratorAgent.async_generate()` | `{success, test_cases}` |
| `generate_by_points`（async） | `{test_points[], case_count_per_point, context, auto_review}` | `TestPointCaseGenerator.generate()` | `{test_cases, failed_points, stats}` |
| `save_test_case` | `{requirement, test_cases[], llm_provider}` | 逐条组 `TestCase`（标题带测试点来源、优先级映射 高/中/低→p1/p2/p3）→ **逐条 save 拿主键** | `{success, test_case_id[]}` |

用 async 视图的原因：一次生成要调 LLM，`ainvoke` 才能让多个请求并发。代价是得用 `async_login_required`，并且所有 ORM 调用必须避开同步路径（见 3.7）。

`save_test_case` 为什么不用 `bulk_create`：MySQL 下 `bulk_create` 不回填自增主键，返回的 id 全是 None，前端拿不到 id 就没法跳详情。

**`generator.py`（`TestCaseGeneratorAgent`）**

输入：需求文本 + 设计方法 + 用例类型 + 条数。
处理：
1. `_get_knowledge_context()`：拿整段需求去检索知识库（失败不影响生成，只 warning）；
2. 组装 messages → `llm.ainvoke` / `llm.invoke`；
3. `_extract_json_from_response()`：先匹配 `^\[.*\]$`，不行就找最后一个 `},` 截断补 `]`；
4. `_validate_test_cases()`：必须是 dict、必须有 `description/test_steps/expected_results`、步骤与预期必须是 list 且非空；
5. 超量裁剪到 `case_count`；
6. `_dedup()`：走 `utils/dedup`，失败保留原结果。
输出：合法用例列表。

**`point_generator.py`（`TestPointCaseGenerator`）**

这是「补断层」时新增的关键模块，把「PRD 测试点」和「用例」真正连起来。

```
generate(test_points, context)
  ├─ asyncio.Semaphore(3) 控制并发
  ├─ 每个测试点一个协程 _generate_one()
  │    ├─ 用「测试点标题 + 各场景标题」检索知识库（比整段需求检索准）
  │    ├─ 生成条数 = max(count_per_point, 场景数)
  │    ├─ 调模型 → _parse_cases（兼容 markdown 围栏、中文引号、尾逗号）
  │    └─ 给每条用例打上 test_point_id / test_point_title
  ├─ gather(return_exceptions=True) → 单点失败只记 failed_points，不影响整体
  ├─ 跨测试点语义去重
  ├─ stats：total_points / covered_points / failed_points / total_cases / dedup_removed / coverage
  └─ optional auto_review → _quality_loop()
```

`_quality_loop()`（质量闭环）：
1. `QualityLoop.review_all()` 给每条打分（分数/问题/遗漏挂在 `case['review']`）；
2. 统计平均分与低分条数；
3. 按 `test_point_id` 分组，**每个点只重生成一次**（避免 3 条低分触发 3 次调用）；
4. 把评审意见拼进下一轮 context：`【上一轮用例被评审指出的问题，本次务必修正】`；
5. 新用例再评一次，**逐点比较新旧平均分，更高才整组替换**；
6. 全程累计 token（`usage`）。

设计意图写在文件头注释里：丢弃低分用例 = 这个测试点没人管了；而评审意见本身就是下一轮最好的提示词。

**`quality_loop.py`**

`REVIEW_PROMPT` 四维评审（完整性/可执行性/预期可验证性/场景覆盖度），要求只返回 `{score, problems[], missing[]}`。`extract_token_usage()` 兼容 `usage_metadata` 与 `response_metadata.token_usage` 两种返回结构。

---

#### 3.5.3 `test_case_reviewer/` 评审

**`views.py`**

| 函数 | 输入 | 处理 | 输出 |
|---|---|---|---|
| `review_view` | 当前用户 | `visible_test_cases()` + `prefetch_related('reviews')`，三个 tab 各自分页（每页 15） | 页面 |
| `case_review` | POST `{test_case_id}` | 单条：权限校验 → `TestCaseReviewerAgent.review()` → 解析 JSON → **回写 status** → 落 `TestCaseReview` | `{review_result, parsed, verdict, new_status, status_changed}` |
| `batch_review` | POST `{ids[]}` 或 `{scope}` | 校验权限（`visible_test_cases` 过滤掉越权 id）→ `start_batch_review()` | `{task_id, total, low_score_threshold}` |
| `batch_review_progress` | GET `?task_id=` | 读 `progress_registry` | 进度 JSON |
| `manual_score` | POST `{test_case_id, score, comment}` | 0–10 校验、0.5 分档 → 更新 `manual_*` → 返回 gap 与 is_invalid | JSON |
| `invalid_cases_view` | GET `?scope=invalid/pending/all` | `_collect_review_cases()` 分三组，按 gap 倒序 | 页面 |
| `export_review_sheet` | GET `?scope=` | openpyxl 生成 8 列清单（人工评分列留空） | xlsx（RFC 5987 文件名） |
| `import_manual_scores` | POST file / text | 表头定位「用例ID」「人工评分」列，无表头退回 A/C 列；空分跳过、非法报错 | `{updated, skipped, errors, invalid_count}` |
| `delete_test_cases` | DELETE `?ids=` | **仅组长** | JSON |
| `export_test_cases_excel` | GET `?ids=` | xlwt 导 .xls | 文件 |

三个细节：
- `review_view` 必须 `prefetch_related('reviews')`，否则 15 条 × 3 tab 的「AI 评分」列会变成 45 次查询；
- `batch_review` 支持 `scope` 是因为待评审 34 条分 5 页，用户只能勾当前页 15 条，「全部评审」必须由后端按状态捞；
- 删除接口自己校验组长身份——**按钮藏起来不等于接口安全**。

**`reviewer.py`**

极薄的一层：把 `TestCase` 的三个字段塞进 prompt，调 `llm.invoke()`，返回 `AIMessage`。业务判断全部在调用方（单条在 view、批量在 batch_review）。

**`batch_review.py`**

```
start_batch_review()
  ├─ uuid 生成 task_id
  ├─ 先写一次初始进度（避免前端首次轮询 404）
  └─ daemon Thread 跑 run_batch_review

run_batch_review()
  ├─ ThreadPoolExecutor(4) 提交 _review_one
  ├─ 每完成一条：加锁累加统计 → push() 写进度
  └─ 结束：拼「通过 X 条、不通过 Y 条、无结论 Z 条、平均分 N」
```

`_review_one()` 里两个关键点：
1. `TestCase.objects.filter(id=...).update(status=verdict)` 而不是 `instance.save()`——避免把并发情况下别人改过的描述/步骤用旧值覆盖回去；
2. `finally: close_old_connections()`——线程池会复用线程，Django 的数据库连接不关会一直累积。

并发之所以是 4：34 条 × 5~10 秒，串行约 4 分钟，4 路约 1 分钟；再加会触发模型侧 RPM/TPM 限流，反而更慢。

**`utils/review_parse.py`**

```python
parse_review_json(content)      # 剥 ``` 围栏 → 截取第一个 { 到最后一个 } → json.loads，失败返回 None
extract_score(parsed)           # 兼容 6 / "6" / "6/10"
recommendation_to_status(rec)   # 先判否定词再判肯定词
```

`recommendation_to_status` 的顺序是硬要求：**「不通过」包含「通过」子串**，先判肯定词的话所有「不通过」都会变成通过。这类子串误判是这套系统里最容易写错、后果又最直接（状态全反）的地方。

---

#### 3.5.4 `prd_analyzer/` PRD → 测试点

**`views.py`**：只接受 `.docx`。
处理链路：同名文件先删 → 存到 `prd/` → `word_to_markdown()` 转 md → 读全文 → `PrdAnalyserAgent.analyse()`。

**`analyser.py`**：
1. 组装 messages → `invoke`；
2. 剥 ```json 围栏 → `json.loads`；
3. `_validate_analysis_result()`：测试点必须有 `id/title/description/priority/scenarios`，场景必须有 `id/title/description/test_type`，只保留合法项；
4. `_build_summary()`：总测试点数、总场景数、高/中/低优先级分布（`_normalize_priority` 兼容「高/high/p0」）。
输出：`{test_points, summary}`。

**`utils/file_transfer.py`**：优先 pandoc（排版还原好），没有 pandoc 就降级用 python-docx 提取段落 + 表格并补 `#` 标题层级。转换结果为空直接抛错，避免上层读到空文件还以为成功。

---

#### 3.5.5 `iface_case_generator/` 接口用例

**核心思想：最小输出协议。**

MeterSphere 的用例 JSON 嵌套极深（`MsHTTPElement` → `children[0].assertionConfig` → `jsonPathAssertion` + `bodyAssertionDataByType` 双份断言）。让模型直接生成完整结构，字段错漏几乎必然。

所以模型只输出「差异」，结构由代码拼：

```json
{ "name": "登录_密码为空", "request_body_json": {"password": ""},
  "request_query": [], "request_rest": [], "assertion_condition": "NOT_EQUALS" }
```

**链路**：

```
views.iface_case_generator (POST single_file)
  └─ 存 uploads/（重名自动加 _1）→ parse_api_definitions → 返回接口清单

views.iface_case_generator (POST generate_test_cases)
  └─ daemon 线程跑 generate_test_cases_for_apis()，立即返回 task_id

generate_test_cases_for_apis
  ├─ APITestCaseGeneratorAgent(llm_provider)
  ├─ generate_test_cases_for_apis_batch：ThreadPoolExecutor(5) 按接口并发
  │    └─ _generate_cases_for_single_api
  │         ├─ _has_request_parameters() 无参接口直接跳过
  │         ├─ _generate_with_retry()：解析失败重试 2 次，重试时条数减半 + 追加格式约束
  │         └─ _merge_minimal_case_to_full_case()
  │              ├─ 深拷贝 templates/iface_test_case_template.jsonc
  │              ├─ request = deepcopy(接口定义的 request)
  │              ├─ 回填 body / query / rest（只覆盖 value，键必须能在定义里找到）
  │              └─ 断言由后端固定生成：code EQUALS|NOT_EQUALS 10000
  ├─ 合并回 api_definitions[].apiTestCaseList
  └─ 写回原 JSON 文件 → progress 100% + file_path
```

前端用 SSE（`/api/stream-logs/`）+ 2 秒轮询兜底，100% 后显示下载链接。

---

#### 3.5.6 `kb_chat/` 知识库问答（新增）

```
GET  /kb_chat/            → 渲染对话页（带 provider 列表与字数上限）
GET  /kb_chat/api/stats/  → 已接入文档数 / 片段数
POST /kb_chat/api/ask/    → {question, history[], llm_provider}
```

`ask()` 的处理：

1. 校验问题非空、≤500 字；
2. `_clean_history()`：只放行 `user/assistant`、每条截 800 字符、最多 6 条；
3. `_retrieve()`：embed → `search(top_k=18)` → `RAG_MIN_SCORE` 过滤 → `(关键词命中数, 分数)` 双键排序 → 去重取 6 条；
4. **无命中直接返回固定话术**（`no_context=true`），不调模型——避免模型脱离片段编造被当成规范；
5. `_build_context()`：带 `[n] 来源：文件名` 编号，总长 ≤6000 字符；
6. messages = System(规则 + 片段) + 历史 + Human(问题) → `invoke`；
7. 返回 `{answer, sources[{index, source, doc_type, score, snippet}]}`。

前端：回车发送（判 `isComposing` 避免输入法选词误发）、气泡渲染、`[n]` 转上标、来源可折叠、空库橙色提示。

---

### 3.6 `apps/utils/` 横切能力

#### `logger_manager.py`

四件事：

1. **日志落盘**：控制台 + 全量文件 + 仅错误文件，`RotatingFileHandler` 10MB × 5，文件名带启动时间戳（`2026-11-21_15-43-11.log`）。
2. **任务上下文**：`ContextVar` + `线程ID→task_id` 注册表（跨线程兜底），`TaskContextFilter` 把 `task_id` 注入每条 LogRecord。
3. **日志镜像**：`ProgressMirrorHandler` 把带 task_id 的日志同时写进 `progress_registry`（前端轮询拿到的 `logs`）和 `sse_bus`（SSE 实时推）。**业务代码只管 `logger.info`，前端控制台就自动有了。**
4. **脱敏**：`SecretRedactFilter` 把 `sk-xxx`、`Bearer xxx`、`api_key: xxx` 替换成 `***REDACTED***`，挂在所有 handler 上。原因是 LLM 初始化会把完整配置打进日志，一旦分享或提交仓库就泄露。

#### `progress_registry.py` / `progress_schema.py`

`set_progress(task_id, dict)` 用 Pydantic 校验后**合并**到 `ProgressData`：
- `log` 字段是追加语义（列表上限 2000 条），其余字段是覆盖语义；
- `percentage >= 100` 自动置 `COMPLETED`；
- 全程 `threading.Lock`；
- `cleanup_expired(3600)` 清理过期任务（目前靠手动/后续接定时任务）。

`ProgressData` 支持任意 `extra` 字段，所以批量评审可以把「通过/不通过/平均分/最近 8 条明细」塞进 `extra` 一起轮询出来。

#### `sse_bus.py`

每个 task 一条 `Queue(maxsize=1000)` + 递增序号。`publish_log()` 在队列满时**丢最旧**（背压），避免慢消费者把内存吃满。

#### `auth.py`

`async_login_required` 解决三个 async 坑：
1. 用 `await request.auser()` 取用户，否则 `request.user` 触发同步 ORM → `SynchronousOnlyOperation`；
2. 补 `request._cached_user = user`，否则模板里的 `{{ user }}` 走同步懒加载会再查一次库并炸；
3. 用 `await user.groups.filter(...).aexists()` 预先算好角色挂 `request._user_role`，否则 context processor 里同步查 groups 同样炸。

#### `dedup.py`

`dedup_test_cases(cases, embedder, threshold=0.9)`：批量 encode → 归一化 → `arr @ arr.T` 得到相似度矩阵 → 贪心保留（后面的与前面 ≥0.9 就丢）。向量化失败时退化为不去重，不让去重拖垮主流程。

#### `review_parse.py`

见 3.5.3 末尾。三个函数被单条评审、批量评审、首页统计、复核页共用。

---

### 3.7 模板与静态资源

- `templates/base.html`：左侧固定导航 + `main-content`；无鉴权时 body 加 `.auth-page` 隐藏侧边栏（登录/注册页用）。
- `templates/index.html`：四张统计卡 + 手写 SVG 环形图（用后端算好的 `dash / gap / offset`）+ 测试报告区 + 最近用例。
- `templates/upload.html` / `knowledge.html` / `members.html`：上传、知识库管理、成员管理。
- 各 Agent 的模板放在自己 app 的 `templates/` 下（`APP_DIRS=True` 自动发现，前提是 app 在 `INSTALLED_APPS` 里）。
- `static/css/theme.css`：统一靛蓝 `#4f46e5`，覆盖 Bootstrap 默认观感；`--tb-*` CSS 变量是后续改色的唯一入口。

---

## 4. 整体架构与数据流

### 4.1 分层结构

```
┌──────────────────────────────────────────────────────────────┐
│  浏览器（Django 模板渲染 + 原生 JS fetch / EventSource）        │
└───────────────┬──────────────────────────────────────────────┘
                │ HTTP（Django URLconf → view）
┌───────────────▼──────────────────────────────────────────────┐
│  视图层  apps/core/views*.py、各 ai_agents/*/views.py          │
│  职责：参数校验、鉴权、可见性过滤、组织调用、组装响应            │
└───────┬───────────────────────────────┬──────────────────────┘
        │                               │
┌───────▼──────────────┐      ┌─────────▼──────────────────────┐
│  Agent 层            │      │  服务层                         │
│  ai_agents/*         │      │  knowledge/service.py           │
│  职责：Prompt 组装、  │◄────►│  职责：入库/检索/重排            │
│  LLM 调用、结果解析、 │      │  llm/base.py（工厂）             │
│  合并/去重/闭环       │      │                                 │
└───────┬──────────────┘      └─────────┬──────────────────────┘
        │                               │
┌───────▼───────────────────────────────▼──────────────────────┐
│  基础设施层                                                    │
│  LLM API（DeepSeek/Qwen）  Milvus（向量）  MySQL（业务数据）    │
│  BGE-M3（本地推理）        文件系统（uploads / prd / outputs）  │
│  logger_manager / progress_registry / sse_bus（内存横切）      │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 链路 A：需求文本 → 用例 → 落库（最完整的一条）

```
[1] 浏览器 POST /test_case_generator/
    body: {requirements, case_design_methods, case_categories, case_count, llm_provider}

[2] async_login_required
    → await request.auser()，未登录 302 到 LOGIN_URL
    → aexists() 算角色，挂 request._user_role

[3] views.generate
    → json.loads(request.body)，校验 requirements 非空
    → LLMServiceFactory.create(provider)
        → 读 settings.LLM_PROVIDERS[provider]
        → 取 DEEPSEEK_API_KEY
        → new DeepSeekChatModel(ChatOpenAI 子类)，挂 LoggingCallbackHandler

[4] TestCaseGeneratorAgent.async_generate
    → knowledge_service.search_relevant_knowledge(requirements)
        → BGE-M3 encode（归一向量）
        → Milvus search(COSINE, top_k≥10)
        → RAG_MIN_SCORE 过滤 → 关键词重排 → 去重 → 拼成文本
    → Prompt 从 configs/prompt_config.yaml 组装（system + human）
    → await llm.ainvoke(messages)
        → 回调 on_llm_start：日志记录 prompt 前 300 字
        → 回调 on_llm_end：记录耗时与响应摘要

[5] 结果处理
    → 正则抠 JSON 数组（容忍 markdown 围栏/尾随文本）
    → json.loads → 逐条校验字段与类型 → 超量裁剪
    → dedup_test_cases（余弦 0.9）

[6] 响应 {success: true, test_cases: [...]}
    前端渲染表格

[7] 用户点「保存」→ POST /test_case_generator/save-test-case/
    → 逐条构造 TestCase(title/description/test_steps/expected_results/
                        requirements/llm_provider/priority/status='pending'/created_by=user)
    → 逐条 save()（为了拿到自增 id）
    → 写 MySQL：core_testcase
    → 返回 {test_case_id: [...]}

[8] 首页 / 重新打开时
    → index() 用 visible_test_cases(user) 统计
    → 通过率 = approved / (approved + rejected)
```

**落库点**：只有第 7 步写 MySQL；第 4–6 步的生成结果不落库，用户不保存就丢弃。

### 4.3 链路 B：批量 AI 评审 → 状态回写 → 统计变化

```
[1] POST /test_case_reviewer/api/batch-review/
    body: {ids:[...]} 或 {scope:"pending"}

[2] 权限收敛
    → visible_test_cases(user).filter(id__in=ids) 得到 allowed
    → 越权 id 记 warning 并剔除

[3] start_batch_review()
    → uuid task_id
    → set_progress(task_id, {percentage:0, ...})   ← 先写一次，避免首轮轮询 404
    → Thread(daemon).start() → 立即返回 {task_id, total}

[4] 后台线程 run_batch_review
    for each case_id（ThreadPoolExecutor 4）:
        → TestCase.objects.get()
        → TestCaseReviewerAgent.review()  → llm.invoke()
        → parse_review_json()             → {score, strengths, weaknesses, recommendation}
        → TestCaseReview.objects.create(reviewer_id=当前用户, review_comments=json)
        → recommendation_to_status()      → approved / rejected
        → TestCase.objects.filter(id).update(status=verdict)
        → finally: close_old_connections()
    每完成一条：加锁更新统计 → set_progress（percentage / message / extra）

[5] 前端
    EventSource /api/stream-logs/?task_id=  ← logger 镜像出的实时日志
    每 2s GET /api/batch-review/progress/   ← percentage / extra（通过 X 条、平均分…）
    100% 后停止轮询并刷新列表

[6] 首页统计随之变化
    approved 增加 → 通过率上升；TestCaseReview 新增 → AI 覆盖率与平均分更新
```

**为什么要拆成「立即返回 + 轮询」**：34 条 × 5~10 秒，同步等在一个 HTTP 请求里，浏览器和反向代理都会先超时。

### 4.4 链路 C：文档入库

```
POST /upload/  (multipart, files[])

→ 数量 ≤5、逐个：扩展名白名单 → ≤20MB
→ 同名：先 delete_by_source 清旧向量，再删旧文件
→ 保存到 MEDIA_ROOT（uploads/）
→ process_singel_file：
     docx(Confluence?) / pdf / md → unstructured partition → chunk_by_title(4000)
→ 过滤空文本 → embedder.get_embeddings(texts)（一次批量 encode）
→ 组装记录 {embedding, content, metadata, source, doc_type, chunk_id, upload_time}
→ vector_store.add_data()：content>4096 截断 → insert → flush
→ 返回 [{name, count, error, overwritten}]
```

### 4.5 链路 D：知识库问答

```
POST /kb_chat/api/ask/ {question, history[]}

→ 校验（非空、≤500 字）
→ _clean_history：只留 user/assistant、截 800 字、最多 6 条
→ _retrieve：
     embed(question) → Milvus search(top_k=18)
     → score ≥ RAG_MIN_SCORE
     → (关键词命中数, score) 排序 → 按 content[:80] 去重 → 取 6
→ 空结果 → 直接返回固定话术（no_context=true），不调模型
→ 非空：
     context = "[1] 来源：xxx\n内容..." 拼接（≤6000 字）
     messages = System(规则+context) + history + Human(问题)
     → invoke → answer
→ 返回 {answer, sources[{index, source, score, snippet}]}
```

### 4.6 链路 E：接口用例生成（文件进、文件出）

```
POST /iface_case_generator/ (single_file=xxx.json)
→ 存 uploads/ → parse_api_definitions 读 apiDefinitions → 返回接口清单
→ 前端渲染表格（path/name/method/已有用例数）

POST /iface_case_generator/ (generate_test_cases=1, file_path, selected_apis, ...)
→ daemon 线程 → 立即返回 task_id

后台：
→ 读 JSON → 按 path 建索引 → 过滤选中项
→ ThreadPoolExecutor(5)：
     每接口 → 检查有无参数 → 一次 LLM 调用生成 N 条最小用例
     → Pydantic 校验（失败重试 2 次，条数减半 + 加格式说明）
     → 合并：模板 + 接口 request + 模型值 + 后端固定断言
→ 合并回 api_definitions[].apiTestCaseList
→ 写回同一个 JSON 文件 → set_progress(100%, file_path)
→ 前端显示下载链接 → GET /download_file/?file_path= （限制在 uploads 目录内）
```

**这条链路不碰数据库**：不写 TestCase，也不参与首页统计与评审。

### 4.7 横切关注点如何协作

| 关注点 | 实现 | 协作方式 |
|---|---|---|
| 日志 | `logger_manager` 单例 + RotatingFileHandler | 所有模块 `get_logger(__name__)`，格式含 thread 与 task_id |
| 日志→前端 | `ProgressMirrorHandler` | 订阅带 task_id 的日志，写入 progress_registry + sse_bus |
| 进度 | `progress_registry`（进程内 dict + Lock） | 业务 `set_progress`，前端轮询 `get_progress` |
| 实时流 | `sse_bus`（每任务 Queue，满则丢最旧） | `views_sse.stream_logs` 消费，超时发心跳 |
| 鉴权 | `login_required` / `async_login_required` | 同步视图用前者，async 视图用后者（补 _cached_user） |
| 数据隔离 | `permissions.visible_test_cases` | 所有列表/统计/批量操作统一走它 |
| 角色 | `roles.py` + `context_processors` | 模板控制入口显隐，接口层二次校验 |
| 脱敏 | `SecretRedactFilter` | 挂在所有 handler 上，覆盖控制台/文件/前端镜像 |

### 4.8 数据模型关系

```
User (Django 内置)
 ├─1:N─ created_testcases → TestCase.created_by      （数据隔离依据，可为空=公共历史数据）
 ├─1:N─ reviews           → TestCaseReview.reviewer  （AI 评审时为空）
 ├─1:N─ manual_reviews    → TestCase.manual_reviewer
 └─M:N─ groups            → Group(测试组长 / 测试组员)

TestCase
 ├─1:N─ reviews           → TestCaseReview（多轮评审，取最新一条算 ai_score）
 └─ 派生：ai_score / score_gap / is_invalid / latest_review

TestCaseReview.review_comments  存 JSON 字符串，property parsed 反序列化

KnowledgeBase     手动添加条目的留痕（检索实际查 Milvus）
TeamSetting       单例（pk=1），只有 data_isolation_enabled 一个开关
```

**双存储**：`KnowledgeBase`（MySQL，留痕）与 Milvus（切片向量）是两套数据，`KnowledgeBase.vector_id` 目前没有真正回填，属于历史遗留字段。

### 4.9 并发与事务边界

| 场景 | 并发方式 | 事务边界 |
|---|---|---|
| 用例生成 | async + `Semaphore(3)` | 无显式事务；保存时逐条 `save()` |
| 按测试点生成 | async + `Semaphore(3)`，每点一协程 | 同上 |
| 质量闭环重生成 | async + `Semaphore(3)` | 同上 |
| 批量评审 | `ThreadPoolExecutor(4)` + 每线程 `close_old_connections()` | 每条独立；状态回写用 `filter().update()` 而非 `save()` |
| 接口用例生成 | `ThreadPoolExecutor(5)` | 无数据库写入 |
| 首页统计 | 同步多次 count/聚合 | 只读 |

**没有跨步骤事务**：批量评审里「写 TestCaseReview」和「改 TestCase.status」是两条独立语句，中间失败会出现「有评审记录但状态没变」。这是可接受的：重跑一条即可幂等地补齐。

---

## 5. 关键设计决策与已知问题

### 5.1 踩过并修掉的坑（对排查同类问题有参考价值）

| 现象 | 根因 | 修法 |
|---|---|---|
| 所有「不通过」被判成「通过」 | 「不通过」含子串「通过」，先判肯定词 | `recommendation_to_status` 先判否定词 |
| 列表页越翻越慢 | 每条用例都查一次 reviews 拿分数 | `prefetch_related('reviews')` + `latest_review` 吃缓存 |
| 模板报 `Negative indexing is not supported` | `reviews.all|last` 对 QuerySet 负索引 | 换成属性方法 `order_by('-review_date').first()` |
| async 视图 500：`SynchronousOnlyOperation` | `request.user` / `user.groups` 触发同步 ORM | `auser()` + 补 `_cached_user` + `aexists()` 预取角色 |
| 保存用例返回的 id 全是 null | MySQL 下 `bulk_create` 不回填自增主键 | 改逐条 `save()` |
| 删文档后统计到 0 个片段 | Milvus `num_entities` 有 flush 延迟 | 删前先 `list_sources()` 查条数 |
| 关键词过滤形同虚设 | 中文没空格，`text.split()` 整句当一个 token | 中文 bigram + 停用词 |
| 日志里有完整 API Key | 工厂把 final_config 明文打了日志 | 打码 + 全局 `SecretRedactFilter` |
| 评审完首页通过率不动 | 评审只写 `TestCaseReview`，没回写 `TestCase.status` | 解析 `recommendation` 并映射状态 |
| 组员一开隔离就「数据全丢」 | 历史用例 `created_by` 为空 | 可见性规则放宽为 `自己 + 无归属` |
| AI 生成请求体是 `{"param_name": ...}` | 最小模板把 `request_body_json` 示例写成单对键值，模型照抄 | 改成"整个请求体对象"的正确语义 |

### 5.2 现存问题（按影响排序）

| # | 问题 | 影响 | 建议 |
|---|---|---|---|
| 1 | 无 Celery，长任务跑在 Web 进程内 | 进程重启即丢任务；并发高时 worker 被占满 | 接 Celery + Redis，把批量评审/接口生成改成任务 |
| 2 | 进度与 SSE 队列是进程内字典 | 多 worker 部署时轮询可能打到别的进程，查不到进度 | 进度落 Redis；SSE 用 Redis pub/sub |
| 3 | `/api/stream-logs/` 无鉴权且 `csrf_exempt` | 知道 task_id 就能读日志 | 加 `login_required` + task 归属校验 |
| 4 | 接口用例：无 body 的接口生成 0 条 | GET/纯 query 接口全部静默失败 | `MinimalCase.request_body_json` 默认空 dict 导致必走 body 分支；改为仅在接口确有 body 时合并 |
| 5 | 接口用例：重试时的格式约束从未生效 | 解析失败只能靠"条数减半"救 | `prompts.py` 里 `from .api_test_case_parser` 路径写错（实际文件是 `iface_test_case_parser.py`），ImportError 被吞 |
| 6 | 接口用例：原地覆盖上传文件 | 重复生成会不断累加，无版本 | 输出到新文件并记录来源 |
| 7 | PRD 分析只支持 docx，且存到相对路径 `prd/` | 依赖启动目录，迁移易出错 | 用 `settings.BASE_DIR / 'prd'`，扩展名放开 pdf/md |
| 8 | 分块策略不一致 | 手动条目 1000 字符切块，文件走 `chunk_by_title(4000)` | 统一走一个 chunker，参数配置化 |
| 9 | `TestCase.status` 被 AI 结论直接回写 | 没有人工确认环节，AI 判错就污染通过率 | 增加 `status_reviewed_by` 或"待确认"中间态 |
| 10 | 依赖残留 `langgraph` / `GitPython` / `pandas` | 安装体积大、审计噪音 | Java 分析模块已删除，可从 requirements 清理 |
| 11 | 无审计表 | 谁改了状态、谁删了用例无法追溯 | 加 `OperationLog` 或接 Django admin log |

---

## 6. 附录

### 6.1 路由表

| 方法 | 路径 | 视图 | 鉴权 |
|---|---|---|---|
| GET | `/` | `core.views.index` | 登录 |
| GET/POST | `/upload/` | `core.views.upload_single_file` | 登录 |
| GET | `/knowledge/` | `core.views.knowledge_view` | 登录 |
| GET | `/api/knowledge-documents/` | `core.views.knowledge_documents` | 登录 |
| POST | `/api/knowledge-documents/delete/` | `core.views.delete_knowledge_document` | 登录 |
| POST | `/api/add-knowledge/` | `core.views.add_knowledge` | 登录 |
| POST | `/api/search-knowledge/` | `core.views.search_knowledge` | 登录 |
| GET | `/api/stream-logs/` | `core.views_sse.stream_logs` | **无** |
| GET/POST | `/accounts/register/` | `core.views_auth.register` | 公开 |
| GET | `/accounts/login/` `/accounts/logout/` | Django auth | 公开 |
| GET | `/accounts/members/` | `core.views_team.members_view` | 登录 + 组长 |
| POST | `/accounts/members/update/` | `core.views_team.update_member` | 登录 + 组长 |
| POST | `/accounts/members/toggle-isolation/` | `core.views_team.toggle_isolation` | 登录 + 组长 |
| GET/POST | `/test_case_generator/` | `test_case_generator.views.generate` | 登录（async） |
| POST | `/test_case_generator/save-test-case/` | `save_test_case` | 登录 |
| POST | `/test_case_generator/generate-by-points/` | `generate_by_points` | 登录（async） |
| GET | `/test_case_reviewer/` | `review_view` | 登录 |
| GET | `/test_case_reviewer/invalid-cases/` | `invalid_cases_view` | 登录 |
| POST | `/test_case_reviewer/case-review-detail/api/review/` | `case_review` | 登录 |
| POST | `/test_case_reviewer/api/batch-review/` | `batch_review` | 登录 |
| GET | `/test_case_reviewer/api/batch-review/progress/` | `batch_review_progress` | 登录 |
| POST | `/test_case_reviewer/api/manual-score/` | `manual_score` | 登录 |
| GET | `/test_case_reviewer/api/export-review-sheet/` | `export_review_sheet` | 登录 |
| POST | `/test_case_reviewer/api/import-manual-scores/` | `import_manual_scores` | 登录 |
| DELETE | `/test_case_reviewer/api/delete-test-cases/` | `delete_test_cases` | 登录 + 组长 |
| GET/POST | `/prd_analyzer/` | `prd_analyzer.views.prd_analyzer` | 登录 |
| GET/POST | `/iface_case_generator/` | `iface_case_generator.views.iface_case_generator` | 登录 |
| GET | `/iface_case_generator/download_file/` | `download_file` | 登录 |
| GET | `/iface_case_generator/api/get-generation-progress/` | `get_generation_progress_api` | **无** |
| GET | `/kb_chat/` | `kb_chat.views.chat_view` | 登录 |
| POST | `/kb_chat/api/ask/` | `kb_chat.views.ask` | 登录 |
| GET | `/kb_chat/api/stats/` | `kb_chat.views.stats` | 登录 |

### 6.2 关键配置项

| 配置 | 默认值 | 作用 |
|---|---|---|
| `LLM_PROVIDERS.default_provider` | `deepseek` | 全局默认模型 |
| `AGENT_LLM_DEFAULTS` | 5 个 Agent 均为 deepseek | 每个 Agent 可单独指定 |
| `VECTOR_DB_CONFIG` | `127.0.0.1:19530 / default` | Milvus 地址与 collection |
| `EMBEDDING_PROVIDER` | `local` | `local` 或 `aliyun` |
| `EMBEDDING_MODEL_PATH` | `models/bge-m3` | 本地权重路径 |
| `RAG_MIN_SCORE` | `0.35` | 检索最低余弦相似度 |
| `SCORE_GAP_THRESHOLD` | `3.0` | 人工与 AI 分差超过即判「无效」 |
| `TEAM_LEAD_INVITE_CODE` | `TESTBRAIN-LEAD` | 注册组长所需邀请码 |
| `LOG_FILENAME_WITH_TIMESTAMP` | `True` | 日志按启动时间分文件 |
| `KB_MAX_FILES / KB_MAX_FILE_SIZE` | 5 / 20MB | 上传限制（在 views 中定义） |

### 6.3 启动与验证

```bash
# 依赖
cd ~/Desktop/TestBrain
env -u PYTHONPATH .venv-run/bin/python manage.py migrate
env -u PYTHONPATH .venv-run/bin/python manage.py init_team_accounts

# Milvus（需先启动，服务端 2.4.5）
docker run -d --name milvus-standalone -p 19530:19530 -p 9091:9091 milvusdb/milvus:v2.4.5

# 启动（--noreload：改了 URLconf/模型必须手动重启）
env -u PYTHONPATH .venv-run/bin/python manage.py runserver 127.0.0.1:8000 --noreload

# 自检
env -u PYTHONPATH .venv-run/bin/python manage.py check
```

默认账号：`admin`（组长/超管）、`test1`~`test3`（组员），密码见 `init_team_accounts` 的 `--password`。

---

*文档基于当前代码整理；接口与字段以代码为准，若后续改动请同步更新第 6.1 节路由表。*
