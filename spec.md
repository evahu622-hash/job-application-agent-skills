# Job Application Agent：行为规格

四个项目 Skill 分别负责发现岗位（`job-scout`）、逐岗选择简历版本（`resume-tailor`）、网页申请（`job-apply`）和串联（`job-run`）。三个标准库 Python 脚本提供确定性部分：

- `assistant.py`：读取招聘系统（ATS，Applicant Tracking System）公共接口、岗位去重、JD 引文核对、提交前核验和结果状态。
- `render_resume.py`：在**普通终端**里把使用者的 DOCX 母版构建成少量简历版本 PDF，检查后由使用者批准。
- `dashboard.py`：把配置、岗位、简历版本、运行摘要和试跑日志汇总成一个本地 HTML 页面。

浏览器只负责没有公共接口的来源、登录和网页申请。安装和手动配置见 [SETUP.md](SETUP.md)，版本变化见 [CHANGELOG.md](CHANGELOG.md)。

## 文件合同

| 文件 | 内容 |
| --- | --- |
| `private/source/` | 使用者自己的原版简历（DOCX 母版）及可选历史岗位表；不加入 Git。 |
| `private/targets.yaml` | 目标职位、地区与工作形式、语言、`hard_gates` 硬条件清单、`recency_days`、薪资下限、来源（含 `ats` 类型）与扫描频率。 |
| `private/career_facts.md` | 从源简历核对的事实 ID、原始出处和是否可用于申请。 |
| `private/answers.md` | 已确认的联系、到岗、工作许可和薪资等答案；不存凭据。 |
| `private/site_sessions.md` | 各站点的浏览器登录检查记录（站点、浏览器路线、检查时间、状态、页面证据）；不存密码、验证码、Cookie 或令牌。模板见 `examples/site_sessions.md`。 |
| `private/resume_variants/variants.json` | 简历版本配置：母版路径、上传文件名、字体替换、检查阈值、每个版本的文字替换及其事实 ID。模板见 `examples/resume_variants.json`。 |
| `private/resume_variants/build/manifest.json` | `render_resume.py` 生成：每个版本的状态（`draft` / `approved` / `failed`）、检查结果、PDF 哈希、批准人与时间。`output_dir` 必须在仓库内；`preflight --variants` 只接受 `private/` 下的 manifest，所以请保持默认位置。路径一律相对仓库根目录；仓库外的母版只记文件名。 |
| `private/resume_variants/build/<版本 ID>/` | 该版本的 PDF（文件名为 `upload_filename`）、工作副本 DOCX 和预览图 `preview-N.png`。 |
| `jobs.csv` | 一行一个 `job_id`，由 `assistant.py upsert-job` 更新。 |
| `jobs/JOB_ID/jd.txt` | JD 正文，用于计算哈希。ATS 来源由 `fetch-ats` 规范化生成：同样的接口内容得到字节相同的文件，内容不变就不改写。 |
| `jobs/JOB_ID/source.json` | `fetch-ats` 生成：ATS、岗位 ID、标题、公司、地点、原帖与申请 URL、发布时间及其含义、`updated_at`、ATS 元数据 `employment_type`（雇佣类型）/ `workplace_type`（工作形式）/ `compensation`（薪资区间，接口有才写）、抓取时间、实际使用的 `api_url`、`jd_sha256`、来源状态、时效判断 `recency`。 |
| `jobs/JOB_ID/form.json` | `fetch-ats --questions` 生成（仅 Greenhouse）：申请表问题、是否必填、字段名与类型、选项。 |
| `jobs/JOB_ID/jd.md` | 浏览器来源：原站 URL、访问时间、浏览器路线、来源状态证据、JD 提取方式与摘要。ATS 来源可以没有，同类信息在 `source.json`。 |
| `jobs/JOB_ID/fit.md` | 硬条件 met / unmet / unknown（带 JD 引文和事实 ID）、匹配缺口、JD 未写明项、发布时间、评分。 |
| `jobs/JOB_ID/resume-plan.md` | 选用哪个已批准版本及原因、JD 重点、仍然可见的匹配缺口。 |
| `jobs/JOB_ID/resume-diff.md` | 该版本相对母版的每处改动、事实 ID 与理由。 |
| `jobs/JOB_ID/resume.pdf` | 选中版本的 PDF，与 manifest 中的 `pdf_sha256` 一致。 |
| `jobs/JOB_ID/upload/<upload_filename>` | 同一 PDF 的上传副本；招聘方看到的是这个文件名。 |
| `jobs/JOB_ID/fact-check.md` | manifest 中的检查结果、两份副本的哈希、批准记录。 |
| `jobs/JOB_ID/form-map.md` | 问题演练时申请表问题到答案或事实 ID 的映射，以及未解决的问题。 |
| `jobs/JOB_ID/pre-submit.json` | 表单回读和附件核验结果；问题演练时为 `"dry_run": true`。 |
| `jobs/JOB_ID/application.json` | 提交结果、时间与页面证据。 |
| `runs/YYYY-MM-DD/digest.md` | 本轮扫描摘要：`targets.yaml` 里**每个**来源的状态（已尝试 / 跳过 / 受阻及原因）与路线（ATS 接口或哪种浏览器）、岗位、评分、待回答问题。同一天再次运行时追加带时间的新段落。 |
| `runs/YYYY-MM-DD/trial/`、`runs/YYYY-MM-DD/tests.log` | 试跑日志（Codex 或 Claude Code 的 JSONL，可带同一前缀的 `_prompt.txt` 提示词和 `_last.md` 最后回复）和测试输出；`dashboard.py` 默认读取最新一份。 |
| `runs/YYYY-MM-DD/simulation/` | 虚构 JD 的 SIMULATION 演练：自己的 `jobs.csv` 和 `jobs/`，命令加 `--store runs/YYYY-MM-DD/simulation/jobs.csv`。 |
| `runs/YYYY-MM-DD/dashboard.html` | `dashboard.py` 生成的本地结果页；含个人数据，不得发布。 |

`job_id`：ATS 来源由 `list-ats` / `fetch-ats` 生成，格式为 `{ats}-{board}-{ats_job_id}`，转小写，`[a-z0-9._-]` 以外的字符换成 `-`；超过 80 个字符时截短 board 并加上 board 的 8 位哈希（结果稳定）。`fetch-ats --job-dir` 必须是 `jobs/` 下以这个 `job_id` 命名的文件夹（`jobs/<job_id>`；SIMULATION 为 `runs/YYYY-MM-DD/simulation/jobs/<job_id>`）。其他来源用 `<站点>-<原站稳定岗位 ID>`（如 `linkedin-4012345678`），否则由规范化后的原始 URL 派生。不能仅凭相似标题合并。

同一岗位换了网址也只能有一个 `job_id`：`upsert-job` 用“岗位键”判断——ATS 岗位 ID（Greenhouse 用全局数字 ID，包括雇主页上的 `?gh_jid=`；Lever、Ashby 用 UUID；Personio 用公司 + ID），否则用去掉 `www.`、末尾斜杠和跟踪参数（`utm_*`、`gh_src`、`ref` 等）的网址。键已属于另一个 `job_id` 时拒绝：`Same posting already tracked under another job_id: X`。申请 URL 只在含 ATS 岗位 ID 时参与比较。

## 命令合同

所有路径都从仓库根目录运行。`assistant.py` 的全局参数 `--store PATH`（默认 `jobs.csv`）写在子命令之前，`jobs/`、`private/` 相对它所在的目录解析。

```sh
python3 assistant.py list-ats SOURCE_URL [--recency-days N]
python3 assistant.py fetch-ats POSTING_URL --job-dir jobs/JOB_ID [--questions] [--recency-days N]
python3 assistant.py upsert-job --source-json jobs/JOB_ID/source.json [--observed-at ISO]
python3 assistant.py upsert-job --job-id ID --company C --title T --location L \
  --source-url URL --apply-url URL --jd-file jobs/JOB_ID/jd.txt --source-status STATUS [--observed-at ISO]
python3 assistant.py check-quotes JOB_ID [--file NAME ...]
python3 assistant.py preflight JOB_ID --resume PDF --pre-submit JSON [--variants MANIFEST]
python3 assistant.py record-outcome JOB_ID --resume PDF --pre-submit JSON --outcome confirmed|unknown --evidence TEXT [--variants MANIFEST]

python3 render_resume.py build [--config PATH] [--only ID] [--force]
python3 render_resume.py approve ID --by TEXT [--config PATH]
python3 render_resume.py status [--config PATH]

python3 dashboard.py [--root DIR] [--out PATH] [--title TEXT] [--report PATH.md]... [--trial-dir DIR] [--tests-log PATH ...]
```

- `list-ats` 输出岗位 JSON 数组（标题、地点、`posted_at`、`job_id`、URL 等）；`--recency-days` 时每条带 `recency`。`age_days` 向上取到 0.1 天，所以显示的天数和 `met` / `unmet` 一致。
- `fetch-ats` 只用 HTTPS 访问固定的 ATS 公共接口，超时 30 秒；网络错误、超时和 HTTP 502/503/504 间隔 2 秒重试一次（`list-ats` 相同）。接口返回的岗位记为 `active_verified`（公共接口只返回已发布且有申请入口的岗位）。接口回答 404/410，或岗位已不在列表中（Ashby、Personio），记为 `closed`，退出码 0，不改动 `jd.txt`。成功时写 `source.json`；`closed` 只在岗位已保存过（已有 `source.json` 或 `jd.txt`）时更新 `source.json`，否则输出 `"saved": false`，不创建任何文件夹或文件；其他网络或 HTTP 错误退出码 2、输出 `Blocked: ...`，不写任何文件：状态未知，不能记为 `closed`。不带 `--recency-days` 时沿用旧 `source.json` 里的时效天数，刷新不会丢掉 `recency`。
- JD 文本：HTML 实体只解码一次（与浏览器相同）。Greenhouse 的 `content` 是转义过一次的 HTML，先反转义一次再解析；因此 JD 里写着的 `<style>`、`<template>` 等字样会保留为文字，不会吞掉后面的内容。
- Personio：英文接口（`?language=en`）里某个岗位没有正文时（只有德语等其他语言版本），改读默认语言接口（`/xml`），`source.json` 的 `api_url` 记录实际用的接口。
- `upsert-job --source-json` 从 `source.json` 读取身份、URL、来源状态和抓取时间，JD 文件为同目录的 `jd.txt`；原有显式参数用法不变。同一岗位已属于另一个 `job_id` 时拒绝（见上文“岗位键”）。
- `check-quotes` 按段落（空行分段，引文可以跨行）抽出至少 12 个字符的引文：直引号 `"..."`、弯引号 `“...”`、`「...」`、`『...』`，代码里的也算。规范化后必须是该岗位 `jd.txt` 的原文。省略号 `...` 只能用于省略：每一段至少 12 个字符，并按原文顺序出现。段落里有落单的引号也记为缺失。JD 不含中日韩文字时，含中文的引文不可能是原文，列入 `skipped`，不检查。有缺失时退出码 2；一条都没检查时在 stderr 提示。
- `preflight --variants`：相对路径先按当前目录、再按 `--store` 所在目录查找，实际位置必须在这两处之一的 `private/` 下（所以在仓库根目录运行 SIMULATION 时，`--variants private/resume_variants/build/manifest.json` 照常可用）；`--store` 所在目录下的默认 manifest `private/resume_variants/build/manifest.json` 存在时也一定检查。上传 PDF 的哈希必须是这些 manifest 中 `approved` 版本的 `pdf_sha256`，否则拦截。同一岗位（相同岗位键）在另一个 `job_id` 下已是 `submitted_confirmed` 或 `submission_unknown` 时也拦截。
- 三个脚本都需要 Python 3.10+，低于该版本时直接提示并退出。

### 支持的 ATS 公共接口

| ATS | 来源 URL（`list-ats`） | 岗位 URL（`fetch-ats`） | `posted_at` 含义 |
| --- | --- | --- | --- |
| Greenhouse | `https://job-boards.greenhouse.io/{board}`、`https://boards.greenhouse.io/{board}`、欧洲站 `https://job-boards.eu.greenhouse.io/{board}` 与 `https://boards.eu.greenhouse.io/{board}`、嵌入页 `.../embed/job_board?for={board}`、`greenhouse:{board}` | `.../{board}/jobs/{id}`（四种域名均可，忽略查询串）、`.../embed/job_app?for={board}&token={id}`、`greenhouse:{board}:{id}`（雇主自建页上的 `?gh_jid={id}` 用这个写法） | `first_published`：首次发布 |
| Lever | `https://jobs.lever.co/{company}`、`https://jobs.eu.lever.co/{company}`、`lever:{company}` | `https://jobs.lever.co/{company}/{uuid}`（含 eu） | `createdAt`：创建时间 |
| Ashby | `https://jobs.ashbyhq.com/{org}`、`ashby:{org}` | `https://jobs.ashbyhq.com/{org}/{uuid}` | `publishedAt`：最近一次发布，可能是重新发布 |
| Personio | `https://{company}.jobs.personio.de`（或 `.com`）、`personio:{company}` | `https://{company}.jobs.personio.de/job/{id}`（或 `.com`；两者是同一租户，统一记为 `.de`） | `createdAt`：创建时间 |

接口域名：Greenhouse 一律用 `boards-api.greenhouse.io`（2026-10-08 实测它也提供欧洲站的岗位；`boards-api.eu.greenhouse.io` 在 DNS 中不存在）；Lever 用 `api.lever.co` / `api.eu.lever.co`；Ashby 用 `api.ashbyhq.com`；Personio 用公司自己的 `{company}.jobs.personio.de`。

其他网址（招聘聚合站、自建招聘页）在 `targets.yaml` 中写 `ats: browser`，走浏览器流程；`list-ats` 会以 `Unsupported ATS URL` 拒绝。

## 硬条件与匹配缺口

- **硬条件**只有 `private/targets.yaml` 的 `hard_gates` 中列出的几类：地区与工作形式、必须的工作语言、JD 明确排除的签证/工作许可情况、JD 公布的薪资低于下限、排除的职位类型、JD 明确必须且无法替代的资质（执照、学位、安全许可）、发布时效。`targets.yaml` 缺 `hard_gates` 或 `recency_days`（旧文件）时，先问使用者补齐，不能当作“没有硬条件”。
- 每项硬条件这样判断：
  - **JD 没提**（没有语言要求、没有签证或工作许可说明、没公布薪资、没有必须的资质）：记 `met`，注明“JD silent / not stated”，不需要引文。
  - **JD 提了**：引用 JD 原文，与 `targets.yaml` 比较，记 `met` 或 `unmet`，相关处附事实 ID。
  - **`unknown`**：只有 JD 提出了要求、而 `targets.yaml` 里对应的候选人信息是 `unknown` 时才用。例如 JD 写明不提供签证支持，而 `work_authorization` 是 `unknown`。
- **匹配缺口**：工具、方法、“优先”的年限、JD 当作经验描述的行业背景等软要求。它们降低评分并列给候选人看，**不阻止**进入候选清单。
- **JD 未写明**：先看 `source.json` 的 ATS 元数据 `employment_type`、`workplace_type`、`compensation`，有值就作为元数据引用（不加引号，因为不是 JD 原文）；JD 和元数据都没有的才记为 `not stated`，在申请时再核对。除非 `targets.yaml` 把它设为硬条件，否则不是“未知硬条件”。
- **评分**（在 `fit.md` 中写出算式）：`targets.yaml` 设了 `scoring`（自定义模型或 `private/` 下的文件）时按它评分；否则用默认算式：从 100 分起，JD 标为必须的匹配缺口每项减 15，标为优先或加分的每项减 5，资历或职位类型明显不符（但不属于排除职位）减 20；`not stated` 和未知时效不扣分；最低 0 分。
- `unmet` 的岗位不进入候选清单。除发布时效外有 `unknown` 硬条件的岗位：集中问使用者，补齐前该岗位列为“待回答”，不进入候选清单和申请。答案写回 `targets.yaml`（语言、工作许可、雇主支持、薪资）；属于申请答案的同时写入 `private/answers.md`；然后重新评估该岗位的 `fit.md`。
- `fit.md` 只能依据该岗位自己的 `jd.txt` 写；JD 引文一律放在直引号 `"..."` 或弯引号 `“...”` 里；任何引号（包括 `「」`、`『』` 和代码里的引号）都不用于别的用途，强调用**加粗**。写完运行 `check-quotes`，`missing` 中的每一项都必须改正；`fit.md` 引用了 JD 却 `checked: 0`，说明引文没有标出，也要改正。不得跨岗位套用文字。

## 发布时间（recency）

- 优先用 `source.json` 的 `posted_at`（ATS 接口），或原页面上可见的绝对日期，并说明字段含义：Greenhouse 是首次发布，Lever 与 Personio 是创建时间（可能早于实际发布），Ashby 是最近一次发布、可能反映重新发布。
- “首次看到”只能作为标明的替代值，此时时效为 `unknown`，绝不能记为 `met`。页面只写相对时间（如“Reposted 1 week ago”）时记录原文，时效仍为 `unknown`。
- 只有 `recency` 列在 `hard_gates` 里时，`unmet` 的时效才拦截；否则只作为匹配信息显示。
- `unknown` 的时效不阻止进入候选清单，但必须在 `fit.md`、摘要和申请审核中显示。

## 浏览器运行时

- Cookie 横幅：有拒绝选项（Reject all / 只用必要 Cookie）时选拒绝并继续，记录 `cookies: rejected non-essential`；不点"接受全部"、不接受追踪、不付费；没有拒绝选项的横幅或"付费或接受追踪"墙按同意墙处理。用户不在场且要求不等待时，所有登录、验证、同意墙记为 `blocked`，在 digest 的 **Needs the user** 一节逐站写明原因和需要用户本人做的动作。

- ATS 公共接口不需要浏览器，可以在非交互运行（`codex exec`）中完成；沙箱需要允许联网（见 SETUP.md）。Claude Code 在交互会话中运行，`/job-scout` 放在消息最前面（见 SETUP.md §6）。
- 浏览器步骤（`ats: browser` 来源、登录、真实申请表）必须在交互会话中运行（Codex 应用/TUI，或交互式 Claude Code 会话；`claude -p` 和 `codex exec` 都是非交互的），让使用者能批准站点权限、登录和处理验证码。
- 交互会话遇到登录墙或验证码：暂停，请使用者在页面里登录或处理，然后复查一次；仍被拦或处于非交互运行时才记为 `blocked`。检查结果记入 `private/site_sessions.md`。不代填、不索取凭据，不在任何招聘站点注册账号。
- 浏览器路线：用 Agent 自带的浏览器工具（Codex 浏览器或 Claude in Chrome）。通过 CDP 技能等方式操作使用者日常的浏览器资料，需要使用者对本次运行明确同意。Claude in Chrome 属于自带路线：它虽然在使用者日常的 Chrome 里运行、沿用其中的登录状态，但不需要这项同意；`site_sessions.md` 里记为 `Claude in Chrome`。它需要用 `claude --chrome` 启动或在 `/chrome` 中启用（前提见 SETUP.md §1）。
- 非交互运行中浏览器权限被拒：该来源记为 `blocked`，不找替代路线（不换其他浏览器、不直连 CDP 即 Chrome 调试接口、不用 curl 抓该站）。
- 招聘聚合站（如 LinkedIn）上的线索，若 Apply 指向支持的 ATS，只在摘要里记为线索，岗位用 `fetch-ats` 入表（雇主页的 `?gh_jid=ID` 写成 `greenhouse:BOARD:ID`），由 ATS 决定 `job_id` 并去重。聚合站页面只有在雇主或 ATS 没有该岗位页面时才算原始页面。遇到访问限制、JD 不完整或岗位/雇主身份不确定时，停止该来源，继续其他来源。
- 摘要里记录本轮用了哪条路线（ATS 接口，或哪种浏览器）。
- 浏览器提取的 JD 在 `jd.md` 记录提取方式；不同提取方式得到的哈希不可比较。翻译类浏览器扩展可能把外文注入页面文字；看起来被翻译或混杂语言时，记录下来并改用原始 HTML 或接口。

## 简历版本

- 使用者在普通终端用 `render_resume.py` 把 DOCX 母版构建为少量版本（按职位类型区分），逐个查看 PDF 和预览图后批准。LibreOffice 无法在 Codex 沙箱内运行，所以 Agent 会话里**不渲染、不重排**简历。
- 每个版本只能对母版做声明过的文字替换，每处替换带事实 ID 和理由。替换后的文字必须完全由这些事实支持：日期、数字、雇主、职位和技能说法与事实一致；团队或公司成果与个人成果分开，计划中的工作与已完成的工作分开；不引入事实里没有的说法。`render_resume.py` 只检查事实 ID 不为空，内容是否忠于事实由起草者和 `resume-tailor` 逐条核对。
- 构建时直接修改 `word/document.xml` 文本，其他内容逐字节保留，并核对“除被替换段落外，所有段落文字与母版一致”。文本框里的段落不能改；包含文本框（例如照片锚点）的段落只能改它自己的文字，而且替换不能跨过文本框。
- 渲染时按 `font_replacements` 把 DOCX 声明的字体映射到已安装的同尺寸字体，避免默认回退字体把 fi/fl 连字提取成乱码。检查项：页数、未加密、字体全部嵌入（结果里列出实际嵌入的字体名；映射目标没出现在 PDF 里时给出警告）、图片数、无连字字符和 `(cid:` 乱码、母版词语覆盖率、必含短语。任何检查失败的版本状态为 `failed`，不能批准。
- LibreOffice 每次输出的 PDF 字节都不同。因此任何一次实际重建（`--force`，或修改 edits、`renderer`、`metadata`、`checks`，或升级 LibreOffice）都会生成新 PDF，状态回到 `draft`，批准作废，构建时会提示。重新批准后，要对已经准备过简历的岗位重新运行 `resume-tailor`，否则 preflight 以 `Resume is not an approved resume variant` 拦截。只改 `role_family` 或 `description` 不会重建，manifest 会同步更新。
- `resume-tailor` 按职位类型选一个 `approved` 版本（没有合适的职位类型时，可用 `role_family: general` 的已批准版本兜底，并在计划中说明），复制到 `jobs/JOB_ID/resume.pdf` 和 `jobs/JOB_ID/upload/<upload_filename>`，并写计划、改动和核对记录。没有任何已批准版本、或改动与事实不符时，暂停并请使用者修改或构建/批准版本。
- 用虚构 JD 演练（SIMULATION）时，所有产物放在 `runs/YYYY-MM-DD/simulation/` 下并标记 `SIMULATION`，命令加 `--store runs/YYYY-MM-DD/simulation/jobs.csv`。

## 流程

1. **配置**：使用者放入自己的简历、填写目标和固定答案、核对事实库，在普通终端构建并批准简历版本。未知的工作许可、签证、薪资或到岗答案保持 `unknown`。
2. **找岗**：ATS 来源用 `list-ats` 和 `fetch-ats`，`upsert-job --source-json` 入表。其他来源用浏览器打开雇主或 ATS 原始 JD：只有完整 JD 与有效 Apply 入口均已看见，才标 `active_verified`；404 标 `closed`；登录墙和访问错误标 `blocked` 或 `unknown`。搜索摘要和历史表格只作线索。
3. **筛选**：按上文区分硬条件、匹配缺口和 JD 未写明项；写 `fit.md` 并运行 `check-quotes`；评分达到阈值、没有 `unmet` 硬条件、也没有（发布时效以外的）`unknown` 硬条件的岗位进入候选清单。
4. **简历**：选择已批准版本，写 `resume-plan.md`、`resume-diff.md`、`fact-check.md`。冲突未解则暂停。
5. **登录与填表**：交互会话中，优先复用同一招聘站点的持久浏览器会话，并在原站确认已进入申请表。每次运行都重新验证登录状态，记录元数据到 `private/site_sessions.md`。密码、二次验证、CAPTCHA 或协议选择由使用者在网页处理。逐页填写已确认答案并回读；自愿填写的身份统计问题（性别、种族、退伍或残障状况）除非 `answers.md` 有已确认答案，否则留空。上传 `jobs/JOB_ID/upload/<upload_filename>`，确认页面显示该附件。未知必填项或附件不明则暂停。
   **问题演练**（真实 JD）：先 `fetch-ats --questions --recency-days N` 和 `upsert-job --source-json`；Greenhouse 写 `form.json`，其他来源在非交互运行中把问题记为无法获取；写 `form-map.md` 和 `"dry_run": true` 的 `pre-submit.json`。不进浏览器填写、不上传、不提交；简历文件可以还没有。
6. **预提交**：网页回读后写 `pre-submit.json`，运行 `python3 assistant.py preflight JOB_ID --resume jobs/JOB_ID/upload/<upload_filename> --pre-submit jobs/JOB_ID/pre-submit.json --variants private/resume_variants/build/manifest.json`。原站开放状态须在 72 小时内确认；`dry_run=true` 一律拦截（演练时第一个报出的原因也可能是简历或来源时间，同样算拦截）。CLI 通过不代表网站已收到申请，也不构成提交授权。
7. **提交与记录**：向使用者展示具体岗位、公司、URL、表单摘要、附件、JD 未写明项、匹配缺口和发布时间；获得该次最终提交批准后仅点击一次。明确成功页或回执才记 `submitted_confirmed`；结果不明记 `submission_unknown`，调查既有申请，不自动重试。
8. **查看**：运行 `python3 dashboard.py` 生成本地结果页；不加参数时自动读取最新的 `runs/*/trial/` 和 `runs/*/tests.log`。

## `pre-submit.json` 合同

```json
{
  "job_id": "example-123",
  "jd_sha256": "与 jobs.csv 相同的 64 位十六进制值",
  "resume_sha256": "实际上传 PDF 的 64 位十六进制值",
  "mode": "review",
  "fields_verified": true,
  "attachment_verified": true,
  "unknown_required_fields": [],
  "dry_run": false
}
```

布尔值要由网页回读结果支持。CLI 还检查岗位身份、申请 URL、JD 版本、PDF 哈希、简历是否为已批准版本、原站新鲜度和既有提交状态。浏览器登录态取决于站点会话有效期，不保证“一次登录永久有效”。

## 实现边界与验收

Skills 是运行说明，不是后台守护程序。`assistant.py` 不登录网站、不渲染 PDF、不上传文件、不点击提交；它只访问固定的 ATS 公共接口。`render_resume.py` 只在普通终端运行。浏览器能力和站点规则会变化，需要逐站试跑；不绕过验证码、访问限制或站点安全措施。

运行 `python3 -m unittest -v` 验证本地脚本逻辑（离线固定数据，不联网）。真实链路还需分别验证 ATS 接口、原始 JD、登录、表单、附件和提交回执；单元测试不能替代网页证据。
