# 首次安装与手动配置

本仓库提供工作流说明（Skills）和三个本地脚本。下载后必须加入**你自己的**履历、目标岗位、申请答案、简历版本和站点登录态，才能用于真实投递。建议先挑 1–2 个来源试跑。

## 1. 准备运行环境

- **Python 3.10 或更新版本**。三个脚本只用标准库。macOS 自带的 `/usr/bin/python3` 可能是 3.9：先运行 `python3 --version`，太旧就用 `brew install python` 安装新版本（脚本遇到旧版本会直接提示并退出）。
- **LibreOffice 和 poppler**（PDF 命令行工具集：`pdftotext`、`pdffonts` 等）：构建和检查简历版本要用。macOS：

  ```sh
  brew install --cask libreoffice
  brew install poppler
  ```

  其他系统用对应的包管理器安装，确认终端里能运行 `soffice --version` 和 `pdftotext -v`。若 `soffice` 不在 PATH 中，可在 `variants.json` 的 `renderer.soffice` 写完整路径（macOS 通常是 `/Applications/LibreOffice.app/Contents/MacOS/soffice`）。
- **Agent**：用 Codex 或 Claude Code 打开**整个仓库文件夹**。Skills 在 `.agents/skills/`（Codex 读取），`.claude/skills` 是指向它的符号链接（Claude Code 读取）。只复制某一个 `SKILL.md` 会缺少 `spec.md` 和脚本。Windows 上若符号链接变成了普通文件，按 [README](README.md#在-codex-和-claude-code-中使用) 复制文件夹。
- **浏览器**：只有 `ats: browser` 的来源、登录和真实申请需要。确认 Agent 能操作你打算使用的浏览器，优先使用日常的持久浏览器资料。
- Windows 用户可用文件管理器完成下述复制；终端命令示例以 macOS/Linux 为例。

## 2. 创建私有配置

在仓库根目录执行（`cp -n` 不会覆盖已有文件）：

```sh
mkdir -p private/source private/resume_variants
cp -n examples/targets.yaml private/targets.yaml
cp -n examples/career_facts.md private/career_facts.md
cp -n examples/answers.md private/answers.md
cp -n examples/site_sessions.md private/site_sessions.md
cp -n examples/resume_variants.json private/resume_variants/variants.json
```

这些 `private/` 文件已被 Git 忽略。不要修改 `examples/` 来填入真实个人信息。**已经有 `private/` 文件时（例如从 v1 升级）不要覆盖**：对照 `examples/` 只补上缺少的 v2 字段（`hard_gates`、`recency_days`、`languages.reject_if_required` / `do_not_reject`、`locations.work_models`、来源的 `ats`）。

### 原版简历和事实库

把原版简历放进 `private/source/`。简历版本由 **DOCX 母版**构建；只有 PDF 的话，先准备一份排版满意的 DOCX。有中英文版就都放入。若有历史岗位表，也可放这里作为线索，但表中的“开放”标记必须回到雇主/ATS 原页复核。

让 Agent 从原版简历提取候选经历到 `private/career_facts.md`，然后你逐条核对并标记可用于投递的事实。每条保留事实 ID、原始文件与页/段、公司、职位、日期、数字和你的确认状态。不能从职位 JD 反向补造经历。对不确定的数字或项目职责，先保持未知。

### 岗位范围：`private/targets.yaml`

| 字段 | 填什么 |
| --- | --- |
| `roles.include` / `roles.exclude` | 目标职位名称或关键词；必须排除的职位类型。 |
| `locations` | 可接受的国家、城市（留空表示该国任意城市）和工作形式 `work_models`（remote / hybrid / onsite，删掉不接受的）。 |
| `languages.accepted` | 你能用来工作的语言。 |
| `languages.reject_if_required` | JD 要求必须会、而你不会的语言：命中即硬条件不满足。 |
| `languages.do_not_reject` | 你还在学、JD 要求了也只算匹配缺口的语言。三个列表都没有的必需语言会被标为未知，Agent 会问你。 |
| `hard_gates` | **只有这里列出的条件能拦下岗位**。可选：`location`、`language`、`work_authorization`（JD 明确排除你的签证/许可情况）、`salary_floor`（JD 公布的薪资低于下限）、`excluded_roles`、`mandatory_credentials`（JD 明确必须的执照、学位或安全许可）、`recency`。JD 对某项只字未提时，该项算满足。工具、方法、“优先”的年限、行业背景等都只是匹配缺口：降低评分、列给你看，但不拦截。缺少这个字段时 Agent 会先问你。 |
| `recency_days` | 只看多少天内发布的岗位。只有 `hard_gates` 里有 `recency` 时才拦截；日期未知时不拦截，但会标出来。 |
| `work_authorization` / `sponsorship_needed` | 工作许可和是否需要雇主支持的真实情况；不确定就写 `unknown`，不要推断。 |
| `minimum_salary` | 薪资下限和币种；`unknown` 表示不设薪资门槛。只在 JD 公布了薪资时比较。 |
| `shortlist_score_threshold` | 进入候选清单的最低分（0–100）。 |
| `sources` | 每个来源的 `name`、`url`、`ats`、`scan_every_days`。 |

**如何确定 `ats`**：打开雇主招聘页里的某个职位，看网址。`job-boards.greenhouse.io/…`、`boards.greenhouse.io/…` 或欧洲站 `job-boards.eu.greenhouse.io/…`、`boards.eu.greenhouse.io/…` 是 `greenhouse`，`jobs.lever.co/…` 是 `lever`，`jobs.ashbyhq.com/…` 是 `ashby`，`….jobs.personio.de` 或 `.com` 是 `personio`。招聘页嵌在雇主自己域名里时，申请按钮或页面源码里通常能找到这些域名和公司名（board），例如 `boards.greenhouse.io/embed/job_board?for=公司名`；雇主页网址里的 `?gh_jid=数字` 是 Greenhouse 岗位 ID，可写成 `greenhouse:公司名:数字`。其余网站写 `browser`。写好后可以先测一下：

```sh
python3 assistant.py list-ats https://job-boards.greenhouse.io/公司名
```

在 Agent 沙箱里运行时若看到 `Operation not permitted` 或 `ATS API unreachable`，通常是沙箱不允许联网，不代表来源或 `ats` 写错了：Codex 用下文 §6 的 `-c sandbox_workspace_write.network_access=true`（或批准在沙箱外运行该命令）；Claude Code 在提示时允许该命令联网。

每次运行都会记录实际尝试了哪些来源；未访问或被拦的来源不能算“无岗位”。

### 固定申请答案

编辑 `private/answers.md`，填写姓名、邮箱、电话、所在地、LinkedIn、最早到岗日、工作许可、是否需要雇主支持和薪资期望。只填写你已确认、愿意在招聘网站使用的答案；未知项写 `unknown`。不同国家的工作许可与薪资币种分别写清楚。不要放密码、验证码、Cookie 或令牌。

## 3. 构建并批准简历版本（在普通终端）

简历版本 = DOCX 母版 + 少量声明过的文字替换（例如按职位类型调整标题行），用 LibreOffice 渲染成 PDF 并自动检查。**这一步在你自己的终端里做**：LibreOffice 无法在 Codex 沙箱（Agent 执行命令时的隔离环境）内运行，Agent 也不得在会话里生成或重排简历，只能从你批准的版本中挑选。

1. 编辑 `private/resume_variants/variants.json`：
   - `master`：DOCX 母版路径（相对仓库根目录）；`upload_filename`：上传给招聘方时的文件名，如 `Jane_Doe_CV.pdf`；`metadata`：PDF 的标题和作者。
   - `checks`：期望页数、最少图片数、词语覆盖率下限（默认 0.995）、必须出现的短语。
   - `variants`：每个版本有 `id`（小写字母、数字、连字符）、`role_family`、`description` 和 `edits`。`edits` 为空就是母版本身；建议保留一个 `role_family` 为 `general` 的母版版本，没有合适职位类型时兜底。每处替换写 `paragraph_contains`（只能匹配母版中的一个段落，且不能是文本框里的段落；包含文本框（例如照片）的段落可以改，但 `old` 不能跨过文本框）、`old`（在该段落中只出现一次）、`new`、`fact_ids`（来自 `career_facts.md`）和 `reason`。只能替换文字，不能增删段落。写法参考 `examples/resume_variants.json` 中的 `product-analytics` 版本。
   - **`new` 必须完全由所引事实支持**：日期、数字、雇主、职位和技能说法与事实一致；团队或公司成果不写成个人成果，计划中的工作不写成已完成；不加入事实里没有的说法。脚本只检查事实 ID 不为空，这一条要你（和 `resume-tailor`）逐条核对。
   - `output_dir` 保持默认（`private/resume_variants/build`）：它必须在仓库内，而 `preflight --variants` 只接受 `private/` 下的 manifest。
2. 构建：

   ```sh
   python3 render_resume.py build            # 全部版本；输入没变的版本会跳过
   python3 render_resume.py build --only 版本ID --force
   ```

3. 检查：每项检查的细节（缺失词、连字字符、嵌入了哪些字体等）在 `build` 的输出里，也写在 `private/resume_variants/build/manifest.json` 的 `checks.*.detail`，结果页（§7）的“简历版本”里也能看到。`python3 render_resume.py status` 只给概况：每个版本的状态（`not_built` 尚未构建、`draft` 待批准、`approved`、`failed`）、PDF 是否仍与 manifest 一致、哪些检查失败。打开 `private/resume_variants/build/版本ID/` 里的 PDF 和 `preview-N.png` 逐页看版式。任何检查失败的版本状态为 `failed`，不能批准。
4. 批准：

   ```sh
   python3 render_resume.py approve 版本ID --by "你的名字"
   ```

   LibreOffice 每次输出的 PDF 字节都不同，所以**任何一次实际重建**（`--force`，修改该版本的 `edits`，修改 `checks`、`renderer`、`metadata`，或升级 LibreOffice）都会生成新 PDF：版本回到 `draft`，构建时提示“Approval cleared”，需要重新查看和批准。重新批准后，对已经准备过简历的岗位重新运行 `$resume-tailor`，否则 preflight 会以 `Resume is not an approved resume variant` 拦截。只改 `role_family` 或 `description` 不会重建。

### 字体替换 `font_replacements`

DOCX 声明的字体若本机没装，LibreOffice 会用默认字体顶替，导致版式变化，或让提取出的文字里 fi/fl 连字变成乱码。做法：

1. 找出 DOCX 声明的字体：

   ```sh
   unzip -p private/source/你的母版.docx word/fontTable.xml | grep -o 'w:name="[^"]*"' | sort -u
   unzip -p private/source/你的母版.docx word/theme/theme1.xml | grep -o 'typeface="[^"]*"' | sort -u
   ```

2. 查本机是否已安装：`fc-list : family | sort -u`（随 poppler 安装），或在“字体册”中搜索。已安装的字体不需要映射。
3. 未安装的字体映射到已安装的**同尺寸（metric-compatible）字体**，例如 Calibri → Carlito、Cambria → Caladea、Arial → Liberation Sans、Times New Roman → Liberation Serif。macOS 可用 `brew install --cask font-carlito font-caladea font-liberation` 安装。把映射写进 `renderer.font_replacements`，如 `{"Calibri": "Carlito"}`。另外，macOS 上的 LibreOffice 遇到 Arial 时，会用自带的同尺寸字体 Liberation Sans 嵌入 PDF（2026-10-08 实测），版式不变；构建结果里显示为 `note: LibreOffice used metric-compatible Arial -> Liberation Sans`，这是正常现象，不是错误。
4. 重新构建，看 `build` 输出（或 manifest、结果页）里的检查细节：`fonts_embedded` 列出 PDF 实际嵌入的字体名，确认是你映射的目标字体；映射目标没出现在 PDF 里时会有 `WARNING`（目标字体没装，或被替换的字体在文档里根本没用到）。文本检查要求没有连字字符和 `(cid:` 乱码、词语覆盖率达标，并列出最多 20 个缺失词。缺失词里出现被拆开的 fi/fl 单词，通常说明字体映射还不对。

## 4. 首次登录各招聘网站

只有浏览器来源和真实申请需要登录。Agent 会在申请前打开原站入口并检查是否已进入申请表。对每个招聘域名，你可能需要在**同一个浏览器资料**里完成一次登录；密码、邮箱验证码、双重验证、CAPTCHA 和网站协议选择由你在网页中处理。会话由浏览器保管，项目只在 `private/site_sessions.md` 记录检查时间、浏览器、状态和页面证据。

登录不保证永久有效。会话过期或站点要求再次验证时，需要重新登录。不同招聘站点即使共用 ATS，也不能假定登录态相通。

## 5. 检查本地工具

```sh
python3 -m unittest -v
python3 assistant.py --help
python3 render_resume.py --help
python3 dashboard.py --help
```

测试是离线的，只说明本地逻辑正常（去重、JD 规范化与哈希、ATS 解析、引文核对、简历版本检查、提交前闸门、结果状态）；不表示任一网站的接口、表单、上传或提交已通过实测。有一项测试会真的调用 LibreOffice：在 Codex 沙箱里它会自动跳过（`skipped=1` 是正常结果），请在普通终端里再跑一次完整测试。

## 6. 首次试跑

### ATS 来源：可以用 `codex exec` 非交互运行

Codex 默认的 `workspace-write` 沙箱**禁止联网**，ATS 接口会读取失败；加 `-c sandbox_workspace_write.network_access=true` 打开（2026-10-08 用 codex-cli 0.160.1 实测）。这会让该次运行里的所有命令都能联网，Skills 只允许访问固定的 ATS 接口。

```sh
D=runs/$(date +%F)/trial && mkdir -p "$D"
codex exec --json -s workspace-write -c sandbox_workspace_write.network_access=true \
  -o "$D/scout-final.md" \
  '用 $job-scout 只扫描 private/targets.yaml 中 ats 不是 browser 的来源：保存 JD 和来源状态，写 fit.md 并运行 check-quotes，写 digest。不申请。' \
  > "$D/scout.jsonl" < /dev/null
```

提示词用单引号，避免 shell 把 `$job-scout` 当成变量。**一定要加 `< /dev/null`**：标准输入不是终端时，`codex exec` 会先打印 `Reading additional input from stdin...` 并一直等待输入结束。从脚本、计划任务或后台运行时，如果不加这一句，进程可能卡住数小时而没有任何输出（2026-10-08 试跑实测，卡了 3.5 小时）。`scout.jsonl` 是事件日志，`scout-final.md` 是 Agent 最后的回复，两者都会显示在结果页里。

### 浏览器来源、选简历、申请：在交互会话里

在仓库目录启动 Codex（`codex -c sandbox_workspace_write.network_access=true`，或 Codex 应用）或交互式 Claude Code（`claude`，Skills 用 `/job-scout` 等调用；`claude -p` 是非交互的，不能用于浏览器步骤），依次提出：

1. “用 `$job-scout` 扫描 `ats: browser` 的来源，保存原始 JD、提取方式和来源状态，不申请。”
2. “用 `$resume-tailor` 为 `job_id` X 选择已批准的简历版本，写计划、改动和核对记录。”
3. “用 `$job-apply` 对 `job_id` X 做问题演练：用 `fetch-ats --questions` 读取申请表问题并映射到 `private/answers.md`，不打开浏览器、不填写、不上传。”（还没有已批准的简历版本也可以做。）
4. 准备真实申请时：“用 `$job-apply` 打开 `job_id` X 的真实申请页，填写和回读已确认字段，上传对应简历，停在最终提交前让我审核。”

也可用 `$job-run` 串联，但建议先完成单个来源的试跑。提交前核对岗位、雇主、申请 URL、表单答案、附件、JD 未写明项和未知必填项。只有你明确批准该岗位的最终提交，Agent 才能点击提交。结果不明时调查已有申请，不自动重复点击。

Codex 交互会话的日志在 `~/.codex/sessions/年/月/日/rollout-*.jsonl`；想在结果页里看到过程（包括向你提问、子代理的步骤），把对应文件复制到当天的 `trial` 目录。

## 7. 查看结果：一个本地网页

```sh
R=runs/$(date +%F) && mkdir -p "$R/trial"
python3 -m unittest -v 2> "$R/tests.log"          # 可选：保存测试输出（unittest 写到 stderr）
python3 dashboard.py                               # 自动读取最新的 runs/*/trial/ 和 runs/*/tests.log
open "$R/dashboard.html"                           # Windows：用 start 打开该文件
```

日志放在别处时用 `--trial-dir 目录` 和 `--tests-log 文件` 指定。脚本默认以自己所在的文件夹为仓库根目录，从哪里运行都一样。

页面是一个自带样式和数据的 HTML 文件，包括：概览（各来源状态与申请状态的岗位数、简历版本状态）、`jobs.csv` 表格、每个岗位文件夹里的全部文件（Markdown 渲染、JSON 格式化、PDF 内嵌查看）、简历版本的检查结果和预览图、`runs/` 下的摘要，以及 `--trial-dir` 里的 Codex 事件日志和其他文本。其他参数：`--report 报告.md` 把一份报告放在最上面，`--out` 改输出路径，`--title` 改标题，`--root` 指定仓库根目录。单个超过 8 MB 的文件不内嵌，只给说明。

**页面含个人数据**：只在本机打开，不要上传、发布或发给他人。

## 8. 数据目录

- `jobs.csv`：岗位索引；`jobs/JOB_ID/`：JD、来源记录、匹配、简历副本与申请记录。
- `runs/YYYY-MM-DD/`：每次扫描摘要、试跑日志和结果页。
- `private/resume_variants/`：简历版本配置、构建产物和 manifest。
- `private/site_sessions.md`：登录检查元数据；密码仍留在浏览器或你自己的密码管理器。

这些运行数据都被 Git 忽略。分享仓库前运行 `git status` 和 `git ls-files`，确认没有把个人文件强制加入版本控制。

## 9. 交给 Agent 完成安装的提示词

把自己的 DOCX 简历作为附件和下面这段提示词一起发给 Codex 或 Claude Code。也可先发送提示词，让 Agent 索取缺少的资料。

> 请帮我在这台电脑上安装并配置 https://github.com/evahu622-hash/job-application-agent-skills ，直到可以进行一次真实岗位来源的只读试跑。你可以克隆公开仓库、创建本地私有配置、运行测试和读取 ATS 公共接口。先阅读仓库的 README.md、SETUP.md、intent.md、spec.md、CHANGELOG.md 及四个 SKILL.md；把**整个仓库**放在一个适合长期使用的位置并作为项目打开，保留 Skills、文档与脚本的相对路径。已有目录或个人数据不要覆盖。
>
> 请检查 Python 3.10+、LibreOffice（`soffice`）和 poppler（`pdftotext` 等）；缺少时帮我安装或告诉我命令。确认当前 Agent 能看到四个 Skills（Codex 读 `.agents/skills/`，Claude Code 读 `.claude/skills/` 链接；Windows 上链接失效时复制文件夹），未显示时重新打开项目或新建会话后再验证。
>
> 我会提供原版简历；如果没有附件，请先向我索取。请把简历放入被 Git 忽略的 private/source/，从原文提取带事实 ID 和准确出处的 private/career_facts.md，标出日期、数字、职责或中英文版本的冲突，交给我逐条核对。不要编造经历，也不要把源文件或事实库提交到 GitHub。
>
> 请用 `cp -n` 复制 examples/ 中的模板（已有的 private/ 文件不要覆盖，只对照模板补上缺少的字段），实际填写 private/targets.yaml 和 private/answers.md。先从简历提取能确定的信息，再用一份简短问卷集中向我补齐：目标职位与排除岗位、国家/城市与工作形式、可工作语言及不接受的必需语言、哪些条件算硬条件、只看多少天内发布的岗位、薪资下限及币种、最早到岗时间、各国工作许可与雇主支持、想扫描的 1–2 个来源和扫描频率。为每个来源判断 ats 类型，并用 `python3 assistant.py list-ats` 验证；若报 `Operation not permitted` 或 `ATS API unreachable`，先确认沙箱是否允许联网（见 SETUP.md §2），不要据此判定来源有误。未知答案写 unknown，不要替我推断。
>
> 请起草 private/resume_variants/variants.json：找出 DOCX 声明的字体并建议 font_replacements，按职位类型提出 1–3 个版本（另保留一个 role_family 为 general 的母版版本），每处文字替换注明事实 ID 和理由。替换后的文字必须完全由所引事实支持：日期、数字、雇主、职位不变，团队成果与个人成果、计划与已完成要区分，不加入事实里没有的说法。**不要在你的会话里运行 render_resume.py build 或 approve，也不要用任何其他工具生成或重排简历**；把我需要在普通终端运行的构建命令告诉我，我查看 PDF 和预览图后自己批准。
>
> 浏览器只用于 ats: browser 的来源和以后的申请。若需要，检查可操作的浏览器并优先复用我的日常会话；每个招聘站点先检查是否已登录。需要密码、验证码、CAPTCHA 或协议选择时，把页面留给我处理；不要在聊天中索取或在项目里保存密码、Cookie、令牌。
>
> 配置完成后运行 `python3 -m unittest -v`（在 Codex 沙箱里 LibreOffice 测试会被跳过，属正常），并用我选定的 1–2 个来源做一次只读试跑：ATS 来源用 list-ats 和 fetch-ats，写 fit.md 并运行 check-quotes，写当天 digest。此次试跑不要填写真实申请表、上传简历或提交。遇到缺失资料时继续完成不依赖它的步骤，再集中告诉我需要确认的项目。最后运行 `python3 dashboard.py` 并告诉我结果页路径，同时给我：安装路径、已填配置和未知项、测试结果、实际访问的来源及证据、登录状态，以及下一步如何构建简历版本、调用 job-scout、resume-tailor、job-apply 和 job-run。真实投递必须等我针对具体岗位另行提出；最终提交前仍要让我审核。
