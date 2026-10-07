# 更新记录

## v2（2026-10-08）

v1 首次试跑暴露了八个问题（T1–T8，详见 [memory.md](memory.md#真实站点验证记录)）。归结为三个根因：网页文字不是稳定的数据源；条件判断没有分层；简历渲染放在了 Agent 沙箱里。v2 的改动逐条对应如下。

### 找岗：ATS 公共接口优先

- **新增 `assistant.py list-ats` / `fetch-ats`**，直接读取 Greenhouse、Lever、Ashby、Personio 的公共接口，不需要浏览器，因此可以在 `codex exec` 中无人值守运行。**原因：T1**，非交互的 `codex exec` 会自动拒绝浏览器域名权限，浏览器找岗得到 0 个岗位。
- **`fetch-ats` 写 `source.json`**，记录 `posted_at` 及其含义（Greenhouse 首次发布、Lever/Personio 创建时间、Ashby 最近发布）；`--recency-days` 给出 met / unmet / unknown。“首次看到”只能作为标明的替代值，此时时效为 `unknown`，不再记为满足。**原因：T2**，Greenhouse 页面不显示日期，Skill 用首次看到的时间把一个已发布 8 天的岗位判成了 7 天内，而接口本身有 `first_published`。
- **`jd.txt` 规范化**：接口来源的 JD 由确定性的 HTML 转文本生成，同一接口内容得到字节相同的文件，内容不变就不改写。浏览器来源在 `jd.md` 记录提取方式，不跨提取方式比较哈希；页面疑似被翻译扩展改写时改用原始 HTML。**原因：T3**，两次运行的哈希不同只因提取方式不同，翻译扩展还可能注入外文。
- **`upsert-job --source-json`**：直接用 `source.json` 入表，减少手工参数；原有显式参数照常可用，并新增 `--observed-at`。
- **浏览器运行时规则**：浏览器步骤必须在交互会话中运行；非交互运行中权限被拒就记为 `blocked`，不换其他浏览器、不直连 CDP（Chrome 调试接口）、不用 curl 抓取；摘要记录所用路线。**原因：T1**，交互式 Codex 曾改用用户全局的 CDP Skill 绕路，路线不可预期。

### 筛选：条件分层与逐岗隔离

- **三分法**：只有 `targets.yaml` 中 `hard_gates` 列出的条件能拦截；工具、方法、“优先”年限、行业经验等是匹配缺口，只降分、不拦截；JD 未写明的信息（雇佣类型、薪资）记为 `not stated`，申请时再核对。`examples/targets.yaml` 新增 `hard_gates`、`recency_days`、`languages.reject_if_required` / `do_not_reject`、`locations.work_models`、来源的 `ats` 字段。**原因：T5**，几乎所有岗位都成了“人工/未知”。
- **新增 `assistant.py check-quotes`**：`fit.md` 中的双引号引文必须出现在本岗位的 `jd.txt` 里，否则拦截；Skill 要求逐岗只依据本岗位 JD 写作、不跨岗位套用。**原因：T4**，批量生成的 `fit.md` 复制了另一家雇主的文字。

### 简历：终端构建、用户批准、Agent 只挑选

- **新增 `render_resume.py`**（build / approve / status）：在普通终端从 DOCX 母版构建少量简历版本。每个版本只做声明过的文字替换并注明事实 ID，其余字节保持不变；用 LibreOffice 渲染，用 poppler 检查页数、字体嵌入、图片、连字乱码、词语覆盖率和必含短语，生成预览图和 `manifest.json`；检查失败的版本不能批准，重新构建后批准作废。**原因：T6**，LibreOffice 无法在 Codex 沙箱内运行，Agent 改用 ReportLab 重新排版，脱离了 DOCX 模板。
- **`font_replacements`**：把 DOCX 声明的字体映射到已安装的同尺寸字体，并检查提取文本中的连字字符和覆盖率。**原因：T7**，默认字体回退让 fi/fl 连字提取成乱码。
- **`resume-tailor` 改为选择版本**：按职位类型选一个已批准版本，复制到 `jobs/JOB_ID/resume.pdf` 和 `jobs/JOB_ID/upload/<upload_filename>`，写计划、改动和核对记录；禁止在会话内渲染或重排简历。**原因：T6**。
- **`preflight --variants`**：上传的 PDF 必须是某个已批准版本（存在 manifest 时自动检查）。**原因：T6**，防止未经批准的 PDF 进入申请。

### 申请

- **`fetch-ats --questions`**（Greenhouse）写 `form.json`，演练时无需浏览器和任何数据输入即可把申请表问题映射到 `private/answers.md`。**原因：T1**，演练不再依赖浏览器权限。
- 真实申请上传 `upload/` 中的文件，`preflight` 带 `--variants`。审核时同时展示 JD 未写明项、匹配缺口和发布时间（含 `unknown`）。**原因：T2、T5**。
- 保留全部原有安全规则：`review` 模式、从不自动提交、结果不明不重试、不存凭据、页面文字只当数据。

### 运行环境与查看

- **`.claude/skills` 相对符号链接**指向 `.agents/skills`，Claude Code 与 Codex 共用同一份 Skills；`.gitignore` 只跟踪该链接，忽略其他本地 Claude 设置；Windows 上链接失效时复制文件夹。**原因：T8**，Claude Code 不读取 `.agents/skills`。
- **新增 `dashboard.py`**：一条命令生成单个本地 HTML，集中显示概览、`jobs.csv`、每个岗位的全部文件、简历版本检查与预览、运行摘要、Codex 事件日志和测试日志，不必逐个打开 Markdown 文件。页面含个人数据，只在本机查看。
- **SETUP 补充**：LibreOffice 与 poppler 安装；`codex exec` 读取 ATS 接口需要 `-c sandbox_workspace_write.network_access=true`（默认沙箱禁止联网，2026-10-08 实测）；首次试跑与查看结果的命令；更新了交给 Agent 的安装提示词。

### 审查修订（同日）

v2 交付前的代码、文档、隐私和“新 Agent 照做”审查发现了一批问题，修订如下。

- **接口与数据**：Greenhouse 欧洲站改用实际存在的 `boards-api.greenhouse.io`（原写的 `boards-api.eu.greenhouse.io` 不存在），并支持 `boards.eu.greenhouse.io` 和 `embed/job_board`、`embed/job_app` 网址；雇主页 `?gh_jid=` 给出改写提示。Personio 只有德语版的岗位改读原语言正文，`.com` 统一记为 `.de`。JD 的 HTML 实体只解码一次，JD 里写着的 `<style>` 等字样不再吞掉后文（原合同的“反复解码直到不变”有误）。board 名过长时 `job_id` 截短并加哈希，不再整板失败。`fetch-ats --job-dir` 必须是 `jobs/<job_id>`；不带 `--recency-days` 刷新时保留原时效窗口。时效天数向上取到 0.1 天，与 met/unmet 一致。
- **去重与提交闸门**：`upsert-job` 按岗位键判断同一岗位，拒绝第二个 `job_id`；`preflight` 拦截已在别的 `job_id` 下提交过的同一岗位；`--variants` 只接受 `private/` 下的 manifest，默认 manifest 总会检查。
- **引文核对**：跨行引文、代码里的引文、`「」`/`『』`、落单引号和省略号拼接都纳入检查；JD 无中文时含中文的引文跳过；一条都没检查时提示。**原因：T4**，堵住批量写作时绕过检查的写法。
- **筛选规则**：JD 没提的硬条件记满足，`unknown` 只用于 JD 提了要求而候选人信息未知；时效只有列入 `hard_gates` 才拦截；固定评分规则；`source.json` 的雇佣类型、工作形式、薪资元数据先于 `not stated`。**原因：T5**。
- **简历版本**：文本框内的段落不能修改；包含文本框（例如照片）的段落可以修改自己的文字，但替换内容不能跨过文本框（真实简历的标题行就锚定了照片，试跑时发现）；`fonts_embedded` 列出实际嵌入的字体并对未出现的映射目标给出警告（**T7**）；重建使批准作废时明确提示；只改 `role_family`/`description` 时同步 manifest；`output_dir` 必须在仓库内，manifest 不写本机绝对路径；沙箱内（`CODEX_SANDBOX`）跳过 LibreOffice 集成测试（**T6**）；三个脚本在 Python 3.10 以下给出明确提示。
- **结果页**：不加参数也读取最新 `runs/*/trial/` 与 `runs/*/tests.log`；默认仓库根目录为脚本所在文件夹；总览计数与岗位表一致；Codex 日志里向用户提问、子代理步骤、等待等工具调用不再丢失；manifest 字段格式异常时显示提示而不是崩溃。
- **Skills 与文档**：恢复简历改写的真实性约束；浏览器来源的停止规则、登录墙处理、聚合站线索转 ATS、`linkedin-<ID>` 式 `job_id`；问题演练与 SIMULATION 分开；`general` 版本兜底；不注册账号、自愿身份统计问题留空、不运行 `render_resume.py build/approve`；摘要列出每个来源；`cp -n` 不覆盖已有配置；沙箱联网提示；新增 `examples/site_sessions.md`；`.gitignore` 增加对散落的结果页、预览图、manifest、日志的保护。
