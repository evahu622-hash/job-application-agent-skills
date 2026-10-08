# 项目记忆

本文件只记通用设计决策和验证结论。不要写个人履历、账号、密码、岗位原文或申请内容；实例运行记录放在被 Git 忽略的 `private/`、`jobs/`、`runs/`。

## 设计决策

- 单人使用，Skills 优先；浏览器负责真实网页动作，本地文件记录中间产物。
- `assistant.py` 只做确定性去重、哈希、新鲜度、预提交和结果状态检查。
  - 补充（v2）：另外只读访问固定的 ATS 公共接口（`list-ats` / `fetch-ats`），并核对引文（`check-quotes`）。
- 首版为 `review` 模式：具体申请由用户在最终提交前审核。
- 旧岗位表和搜索摘要只提供线索；原站完整 JD 和有效申请入口才可标为已核验开放。
- 申请结果不明时不自动重试。
- 登录会话由浏览器保管，项目仅记录站点检查结果，不存凭据；每轮重新验证。

### v2（2026-10-08）

- **ATS 优先**：Greenhouse、Lever、Ashby、Personio 来源用公共接口（`list-ats` / `fetch-ats`），不需要浏览器，可在 `codex exec` 中运行；`assistant.py` 只用 HTTPS 访问固定接口域名。浏览器只用于其他来源和申请。
- **JD 规范化**：接口来源的 `jd.txt` 由确定性 `html_to_text` 生成，同一接口内容得到字节相同的文件，内容不变不改写；浏览器来源记录提取方式，不跨提取方式比较哈希。
- **发布时间**：用接口的 `posted_at` 并注明字段含义；“首次看到”只能作为标明的替代值，此时时效为 `unknown`，不拦截但必须显示。Ashby 的发布时间可能是重新发布。
- **条件分类**：只有 `targets.yaml` 中 `hard_gates` 列出的条件能拦截；软要求是匹配缺口；JD 未写明的信息记为 `not stated`，申请时再核对。
- **逐岗隔离**：`fit.md` 只能依据本岗位的 `jd.txt`，JD 引文放双引号，用 `check-quotes` 机检。
- **浏览器运行时**：浏览器步骤必须在交互会话中运行；非交互运行中权限被拒即记为 `blocked`，不换浏览器、不直连 CDP（Chrome 调试接口）、不抓取。摘要记录所用路线。
- **简历版本**：在普通终端用 `render_resume.py` 从 DOCX 母版构建，只做声明过的文字替换（其他字节不变），按 DOCX 声明字体做同尺寸字体替换，自动检查后由用户批准；Agent 只挑选已批准 PDF，`preflight --variants` 校验哈希。
- **双运行时**：Skills 放在 `.agents/skills/`（Codex），`.claude/skills` 为相对符号链接（Claude Code）；Windows 链接失效时复制文件夹。
- **结果查看**：`dashboard.py` 生成单个本地 HTML，内嵌所有数据；含个人数据，只在本机查看。

### v2 审查修订（2026-10-08）

- **硬条件判断**：JD 没提的硬条件记 `met`（“JD silent”）；只有 JD 提出要求、而候选人对应信息是 `unknown` 时才记 `unknown`。时效只有列在 `hard_gates` 里才拦截。`targets.yaml` 设了 `scoring` 就按它评分，否则用默认算式；`fit.md` 写出算式。
- **岗位键去重**：同一岗位换网址（域名别名、跟踪参数、雇主页 `?gh_jid=`）也只有一个 `job_id`；`upsert-job` 拒绝第二个，`preflight` 拦截已在别的 `job_id` 下提交过的同一岗位。
- **引文核对收紧**：引文可跨行；代码、`「」`、`『』` 里的也检查；省略号每段至少 12 个字符且按顺序；落单引号算缺失；JD 无中文时含中文的引文跳过。非 JD 引文一律不用引号。
- **JD 文本**：HTML 实体只解码一次（Greenhouse 先反转义一次），JD 中写着的 `<style>` 等字样不再吞掉后文。
- **简历版本**：替换文字必须忠于事实（日期、数字、雇主、职位不变；团队与个人、计划与完成分开）；`general` 版本可兜底；任何实际重建都使批准作废并提示；只改显示信息不重建；`output_dir` 必须在仓库内，manifest 不写本机绝对路径；`preflight --variants` 只接受 `private/` 下的 manifest，默认 manifest 总会检查。
- **演练分两种**：真实 JD 的问题演练（`jobs/JOB_ID/`，`dry_run: true`）与虚构 JD 的 SIMULATION（`runs/日期/simulation/`，单独 `--store`）。
- **浏览器**：交互会话里遇登录墙先请使用者处理再复查；不注册账号；通过 CDP 等方式操作日常浏览器资料需使用者同意（Claude in Chrome 是 Agent 自带路线，虽在日常 Chrome 里运行，也不需要这项同意）；聚合站线索指向 ATS 时改用 `fetch-ats`。

## 真实站点验证记录

每次 PoC 追加日期、站点、输入条件、可见页面证据、结果和下一步。不要把本地单元测试当成真实网站验证。

| 日期 | 站点 | 检查范围 | 观察结果 | 待解决问题 |
| --- | --- | --- | --- | --- |
| 2026-10-07 | Codex `codex exec`（非交互） | 浏览器找岗 | T1：浏览器域名权限被自动拒绝，浏览器来源得到 0 个岗位；交互式 Codex 改用了用户全局的 CDP Skill。 | v2：ATS 来源改走公共接口；浏览器步骤只在交互会话，非交互中被拒即 `blocked`。待验证：交互会话下浏览器来源的路线记录。 |
| 2026-10-07 | Greenhouse 雇主招聘页 | 发布时间 | T2：页面不显示发布日期，Skill 退回“首次看到”，把一个已发布 8 天的岗位误判为 7 天内；公共接口有 `first_published`。 | v2：`fetch-ats` 写 `posted_at` 与字段含义，`--recency-days` 判定；首次看到只能给出 `unknown`。待验证：真实来源再跑一次。 |
| 2026-10-07 | 浏览器提取的 JD | `jd.txt` 哈希 | T3：两次运行的哈希不同，只因提取方式不同（项目符号、空白）；浏览器翻译扩展可能向页面文字注入外文。 | v2：接口来源用确定性规范化文本；浏览器来源记录提取方式、不跨方式比较；疑似翻译时改用原始 HTML。待验证：同一岗位重复抓取哈希一致。 |
| 2026-10-07 | 批量生成的 `fit.md` | 逐岗隔离 | T4：一份 `fit.md` 复制了另一家雇主的文字。 | v2：只依据本岗位 `jd.txt` 写，引文用双引号，`check-quotes` 拦截缺失引文。 |
| 2026-10-07 | 筛选结果 | 条件判断 | T5：几乎所有岗位都成了“人工/未知”：工具、QBR（季度业务回顾）经验等软要求被当成硬条件；JD 未写明（如雇佣类型）被当成候选人事实未知。 | v2：`hard_gates` 白名单、匹配缺口、`not stated` 三分。待验证：同一批岗位重新筛选后的候选清单。 |
| 2026-10-07 | Codex 沙箱 + LibreOffice | 简历渲染 | T6：LibreOffice 在 Codex 沙箱内无法运行（实测仅 `danger-full-access` 可行），Agent 改用 ReportLab 重新排版简历，而没有渲染 DOCX 模板。 | v2：`render_resume.py` 在普通终端构建、用户批准；Skill 禁止会话内渲染或重排。 |
| 2026-10-07 | LibreOffice 渲染 | PDF 文本 | T7：默认字体回退使提取文本里的 fi/fl 连字乱码；把模板声明的字体映射到已安装的同尺寸字体后恢复正常。 | v2：`font_replacements` + 连字与词语覆盖率检查；SETUP 说明如何找字体。 |
| 2026-10-07 | Claude Code | Skill 发现 | T8：Claude Code 不读取 `.agents/skills`，Codex 读取。 | v2：增加 `.claude/skills` 相对链接；Windows 复制文件夹。发现已验证，见本表最后一行。 |
| 2026-10-08 | Codex `codex exec`（codex-cli 0.160.1） | 沙箱联网 | 默认 `workspace-write` 下访问 Greenhouse 接口报 `Operation not permitted`；加 `-c sandbox_workspace_write.network_access=true` 后返回 200。 | SETUP 已写入该参数；注意它对该次运行的所有命令开放网络。 |
| 2026-10-08 | Greenhouse 欧洲站 | 接口域名 | `boards-api.eu.greenhouse.io` 在 DNS 中不存在（NXDOMAIN）；`boards-api.greenhouse.io` 对 4 个欧洲站 board 返回与 `boards.eu.greenhouse.io` 相同的列表和单岗位内容。 | 已改为所有 Greenhouse board 都用 `boards-api.greenhouse.io`，并支持 `boards.eu.greenhouse.io` 页面网址。 |
| 2026-10-08 | Personio 公共接口 | 只有德语版的岗位 | `?language=en` 接口里这类岗位的 `jobDescriptions` 为空；不带语言参数的 `/xml` 有完整正文；`.de` 与 `.com` 返回相同内容。 | `fetch-ats` 遇空正文改读 `/xml` 并在 `api_url` 记录；`.com` 统一记为 `.de`。 |
| 2026-10-08 | Codex 沙箱（codex-cli 0.160.1） | 单元测试 | 沙箱内 LibreOffice 集成测试报错；沙箱命令带环境变量 `CODEX_SANDBOX=seatbelt`。 | 该测试在此环境变量存在时跳过；完整测试需在普通终端运行。 |
| 2026-10-08 | Claude Code | Skill 发现（T8） | 用 `claude -p` 复核时登录已过期，未能验证。 | 已由本表最后一行解决。 |
| 2026-10-08 | 试跑 R1：ATS 公共接口 + 历史岗位表线索 | 复查已关闭的旧线索 | 已关闭、从未保存过的线索各留下一个只有 `source.json` 的 `jobs/<id>/` 空文件夹（Agent 还为一个受阻线索手写了一个），污染 `jobs/` 和结果页。 | 已修复：这类线索 `fetch-ats` 只输出 `"saved": false`、不建文件夹，`job-scout` 只写进摘要。 |
| 2026-10-08 | 试跑 R3：后台运行的 `codex exec` | 标准输入 | 打印 `Reading additional input from stdin...` 后等了 3.5 小时，没有任何事件。 | 已修复：SETUP 的命令一律带 `< /dev/null`。 |
| 2026-10-08 | macOS 上的 LibreOffice | 字体检查 | 映射目标 Arial 没出现在 PDF 里，报 `WARNING`；用一行字的 DOCX 复核，LibreOffice 总是嵌入自带的同尺寸字体 Liberation Sans，版式不变。 | 已修复：已知的同尺寸字体对显示为 `note`，不再报警。 |
| 2026-10-08 | 真实简历 DOCX | 改标题行 | 标题行段落锚定了照片文本框，原规则一律拒绝修改这类段落，所有改标题的版本都建不出来。 | 已修复：允许修改该段落自己的文字；文本框内的段落和跨过文本框的替换仍然拒绝。 |
| 2026-10-08 | Claude Code 2.1.289（未登录，无费用） | Skill 发现（T8） | 在仓库里运行 `claude -p hi --output-format stream-json --verbose < /dev/null`，`system/init` 事件的 `skills` 和 `slash_commands` 都列出 job-apply、job-run、job-scout、resume-tailor；空 Git 仓库对照组一个都没有。 | 已解决：经 `.claude/skills` 链接的发现可用。在 Claude Code 里完整运行各 Skill 需要登录，尚未实测。 |
