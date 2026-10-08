# Job Application Agent Skills

一套供个人使用的求职 Agent Skills（Codex 与 Claude Code 通用）：从招聘系统（ATS，Applicant Tracking System，企业发布岗位、收简历的系统，如 Greenhouse）的公共接口和网页找岗位、核验职位描述（JD），按真实履历为每个岗位选择合适的简历版本，填写申请表，并在最终提交前让你检查。仓库还包含三个只用 Python 标准库的脚本和测试。它不是无人值守的批量投递服务。

## v2 更新了什么（2026-10-08）

v2 根据一次完整的本地试跑修订（试跑覆盖 30 多个招聘来源，四个 Skill 都实际运行过）。和 v1 相比：

- **找岗更稳**：Greenhouse、Lever、Ashby、Personio 四类招聘系统改为直接读公开接口，不需要浏览器，能拿到真实的发布时间。v1 曾把一个 8 天前发布的岗位当成"7 天内"。
- **筛选更合理**：只有你在 `targets.yaml` 的 `hard_gates` 里列出的条件（地点、语言、签证、薪资下限等）才会直接淘汰岗位。简历没写的工具或经验只扣分、单独列出，不再让几乎所有岗位卡在"未知"。
- **简历更可靠**：先在终端构建并批准少量简历版本，每个版本只改你声明过的地方，比如标题、技能顺序。Agent 只负责挑选，不在会话里生成或重排简历。上传前程序会核对：PDF 必须是批准过的版本。v1 试跑时，Agent 因为沙箱里跑不了 LibreOffice，自己重新排版了一份简历，脱离了原模板。另外，LibreOffice 默认的字体替换会让 PDF 里的文字被识别成乱码（例如 fi、fl 连写）。v2 用字体映射和自动检查避免这类问题。
- **浏览器规则更清楚**：浏览器步骤要在交互会话里跑；遇到 Cookie 横幅选"拒绝非必要"；你不在场时，遇到登录或验证就跳过，并列出需要你本人处理的网站。
- **Claude Code 也能用**：仓库里自带 `.claude/skills`，已验证 Claude Code 能识别这四个 Skill。
- **一页看结果**：运行 `python3 dashboard.py`，生成一个本地网页，岗位、文件、简历版本和运行记录都在里面。

完整清单和每条改动的原因见 [CHANGELOG.md](CHANGELOG.md)。

## 第一次使用：需要配置什么

### 终端 30 秒入门（Mac）

下面灰色框里的命令，都要粘贴到 Mac 的「终端」里运行，**不是**贴进 AI 的对话框。

1. 解压下载的 ZIP。按 `Command + 空格`，输入「终端」（或 Terminal），回车打开。
2. 在终端里输入 `cd`，后面加一个空格。然后把仓库文件夹从访达拖进终端窗口，按回车。
3. 输入 `pwd` 回车。显示的路径以仓库文件夹名结尾（例如 `job-application-agent-skills-main`），就说明位置对了，后面的命令都在这里运行。

没用过终端也可以看 [官方的终端入门](https://code.claude.com/docs/en/terminal-guide)。

### 第 0 步：先装好一个 AI 工具（二选一）

这些 Skill 要在 AI 编程工具里运行。上面的「终端 30 秒入门」教你打开终端、进入仓库文件夹；然后选一个工具。

**推荐：在终端里用（每一步都能确认）**
- **Claude Code**：需要 Claude 付费订阅（Pro、Max、Team 或 Enterprise），或者 Claude Console 账号。
  1. 在终端运行 `curl -fsSL https://claude.ai/install.sh | bash` 安装。
  2. 装好后新开一个终端窗口，进入仓库文件夹，输入 `claude`，按提示在浏览器里登录（见 [官方入门](https://code.claude.com/docs/en/quickstart)）。
- **Codex**：用 ChatGPT 账号登录。
  1. 先按 [SETUP §1](SETUP.md#1-准备运行环境) 装好 Homebrew。
  2. 运行 `brew install --cask codex`（也可以用 `npm install -g @openai/codex`）。
  3. 进入仓库文件夹，输入 `codex`，按提示登录。

**也可以：用 ChatGPT 桌面版里的 Codex（图形界面）**
- 下载并安装 ChatGPT 桌面版，用 ChatGPT 账号登录（见 [官方入门](https://learn.chatgpt.com/docs/quickstart)）。
- 官方说明里的步骤是：在「Select where ChatGPT should work」中选择 **open a folder**，选中解压后的仓库文件夹；再在顶部的 ChatGPT 下拉菜单里选 **Codex**；然后在输入框里发消息。

**怎么确认装好了**：在 AI 工具的输入框里发一句「列出这个项目里可用的 skills」。回答里出现 `job-scout`、`resume-tailor`、`job-apply`、`job-run` 四个名字，就说明工具已经打开了正确的文件夹。如果没出现：确认打开的是整个仓库文件夹（不是里面的某个子文件夹），然后新开一个对话再试。

确认之后，把 [SETUP.md §9 的提示词](SETUP.md#9-交给-agent-完成安装的提示词) 粘贴到同一个输入框，让 Agent 带你走完下面的配置清单。

### 配置清单

逐项完成下表，详细步骤见 [SETUP.md](SETUP.md)。Agent 可以帮你起草，但**简历版本的批准和个人答案要由你自己确认**。

| # | 做什么 | 在哪里 | 必需吗 | 大约多久 |
| --- | --- | --- | --- | --- |
| 0 | 装好 Codex 或 Claude Code、登录，并确认能看到四个 skills | 见上面「第 0 步」 | 必需 | 10–20 分钟 |
| 1 | 安装工具：Python 3.10+、LibreOffice（把简历转成 PDF）、poppler（检查 PDF） | 终端，见 [SETUP §1](SETUP.md#1-准备运行环境) | 必需 | 10–20 分钟 |
| 2 | 把配置模板复制到 `private/`。这个文件夹被 Git 忽略，正常提交时不会上传到 GitHub；不要用 `git add -f` 强行加入 | 终端，见 [SETUP §2](SETUP.md#2-创建私有配置) 里的 `cp -n` 命令 | 必需 | 1 分钟 |
| 3 | 放入原版简历（Word 格式 `.docx`，有中英文版就都放） | `private/source/` | 必需 | 1 分钟 |
| 4 | 写求职目标：职位方向、城市、能用的语言、哪些条件直接淘汰（`hard_gates`）、薪资下限、要扫描的招聘页 | `private/targets.yaml` | 必需 | 15–30 分钟 |
| 5 | 整理事实库：Agent 从简历提取每条经历和数字，你逐条确认 | `private/career_facts.md` | 必需 | 20–30 分钟 |
| 6 | 填固定答案：联系方式、最早到岗时间、工作许可、是否需要签证支持、期望薪资 | `private/answers.md` | 真实申请前必需 | 10 分钟 |
| 7 | 设置简历版本：字体映射和每个版本要改的地方；然后构建、打开 PDF 检查、批准 | `private/resume_variants/variants.json`，用 `python3 render_resume.py build` 和 `approve` | 选简历和申请前必需 | 20–40 分钟 |
| 8 | 准备浏览器：Codex 用交互会话；Claude Code 用 `claude --chrome`，并安装 Claude in Chrome 扩展 | 见 [SETUP §1](SETUP.md#1-准备运行环境) | 只扫招聘系统接口时不需要 | 5–10 分钟 |
| 9 | 在自己的 Chrome 里登录要用的招聘网站 | 你的浏览器 | 可选，按需 | 按需 |
| 10 | 跑一遍测试和第一次试跑 | `python3 -m unittest -v`，以及 [SETUP §6](SETUP.md#6-首次试跑) | 建议 | 10 分钟 |

**如果有人已经帮你准备好了 `private/` 文件夹**：把整个文件夹放到仓库根目录，已经完成的步骤可以跳过。但简历版本请你自己打开 PDF 看过；确认没问题后，用你的名字重新批准一次：先在终端运行 `python3 render_resume.py status`，看清每个版本的 ID（例如 `bd`）；再运行 `python3 render_resume.py approve 版本ID --by "你的名字"`，把「版本ID」换成真实的 ID。

## 从 v1 升级

1. 拉取新版本：`git pull`，或重新下载 ZIP。
2. 安装 LibreOffice 和 poppler（见 [SETUP §1](SETUP.md#1-准备运行环境)）。
3. **不要覆盖已有的 `private/` 文件**。对照 `examples/` 补上 v2 新增的字段：`hard_gates`、`recency_days`、`languages.reject_if_required` / `do_not_reject`、`locations.work_models`，以及每个来源的 `ats`。
4. 新建 `private/resume_variants/variants.json`，构建并批准简历版本。v2 选简历和申请时，只会使用已批准的版本。
5. `jobs.csv` 格式没变，旧记录可以继续用。
6. 用 `codex exec` 无人值守运行时，命令末尾要加 `< /dev/null`，否则可能一直卡住（见 [SETUP §6](SETUP.md#6-首次试跑)）。

## 四个 Skills

| Skill | 做什么 | 需要浏览器吗 |
| --- | --- | --- |
| `job-scout` | 按 `private/targets.yaml` 扫描来源，核验原始 JD，区分硬条件与匹配缺口，评分，更新 `jobs.csv` | ATS 来源不需要；其他网站需要 |
| `resume-tailor` | 为选定岗位挑一个**你已批准**的简历版本，写清选择理由、改动和核对记录 | 不需要 |
| `job-apply` | 在真实申请页登录、填写、回读、上传简历，停在最终提交前等你批准 | 需要（演练可不用） |
| `job-run` | 串联以上步骤，最后生成本地结果页 | 视步骤而定 |

## 使用流程

1. **配置**（一次）：按 [SETUP.md](SETUP.md) 放入原版简历，填写岗位范围、固定答案和事实库。
2. **构建简历版本**（在普通终端，一次或按需）：`python3 render_resume.py build`，查看 PDF 和预览图后 `python3 render_resume.py approve 版本ID --by "你的名字"`。Agent 不在会话里生成或重排简历。
3. **找岗**：`$job-scout`。ATS 来源走公共接口，可在 `codex exec` 中无人值守运行（需允许联网），Claude Code 用户在交互会话中运行（两种写法见 [SETUP.md](SETUP.md#6-首次试跑)）；其他网站需要交互会话。
4. **选简历**：对选定的 `job_id` 调用 `$resume-tailor`。
5. **申请**：`$job-apply` 填好表单后停下，你审核后才提交。
6. **查看结果**：`python3 dashboard.py`，用浏览器打开 `runs/今天日期/dashboard.html`，一页看到配置是否齐全、岗位、每个岗位的全部文件、简历版本，以及最新的试跑日志和测试结果（自动读取 `runs/*/trial/` 和 `runs/*/tests.log`）。该页面含个人数据，只在本机查看。

需要串联时调用 `$job-run`。在 Claude Code 中把 `$` 换成 `/`，并把命令放在消息最前面，例如 `/job-scout 只扫描 ATS 来源，不申请`；写在句子中间时不保证会运行该 Skill。

## 支持的招聘系统（ATS）公共接口

| ATS | 招聘页示例 | 发布时间字段 |
| --- | --- | --- |
| Greenhouse | `https://job-boards.greenhouse.io/公司`（也支持 `boards.greenhouse.io`、欧洲站 `job-boards.eu.greenhouse.io` / `boards.eu.greenhouse.io`、嵌入页 `embed/job_board?for=公司`） | 首次发布时间 |
| Lever | `https://jobs.lever.co/公司`（也支持 `jobs.eu.lever.co`） | 创建时间 |
| Ashby | `https://jobs.ashbyhq.com/公司` | 最近发布时间（可能是重新发布） |
| Personio | `https://公司.jobs.personio.de`（或 `.com`，统一记为 `.de`；只有德语版的岗位读取原语言正文） | 创建时间 |

这些来源由 `python3 assistant.py list-ats` / `fetch-ats` 直接读取，不打开浏览器，JD 文本稳定可比。招聘聚合站和自建招聘页仍走浏览器；聚合站上的线索若指向以上 ATS，改用 `fetch-ats` 读取。

## 在 Codex 和 Claude Code 中使用

- **Codex**：用 Codex 打开整个仓库文件夹，Skills 位于 `.agents/skills/`，用 `$job-scout` 等调用。
- **Claude Code**：Claude Code 读取 `.claude/skills/`。仓库里的 `.claude/skills` 是指向 `../.agents/skills` 的相对符号链接，两边用同一份 Skills，用 `/job-scout` 等调用（2026-10-08 已验证 Claude Code 能经这个链接发现四个 Skills；在 Claude Code 里完整运行各 Skill 需要登录，尚未实测，见 memory.md）。浏览器步骤要用交互式会话 `claude --chrome`，并需要 Claude in Chrome 扩展和订阅登录（见 [SETUP.md](SETUP.md#1-准备运行环境)）；`claude -p` 是非交互的。
- **Windows**：Git 可能把符号链接检出成一个普通文本文件。此时删除 `.claude\skills`，再把 `.agents\skills` 整个文件夹复制为 `.claude\skills`（PowerShell：`Remove-Item .claude\skills; Copy-Item -Recurse .agents\skills .claude\skills`）；每次更新仓库后重新复制。
- 若会话没有识别新 Skills，重新打开项目或新建会话。

## 下载与测试

[下载 ZIP](https://github.com/evahu622-hash/job-application-agent-skills/archive/refs/heads/main.zip)，或 `git clone https://github.com/evahu622-hash/job-application-agent-skills.git`。需要 Python 3.10+、LibreOffice 和 poppler（一组 PDF 命令行工具；安装见 [SETUP.md](SETUP.md)）。先运行 `python3 --version`：低于 3.10（macOS 自带的常是 3.9）时，按 [SETUP.md §1](SETUP.md#1-准备运行环境) 安装新版本。然后运行 `python3 -m unittest -v` 执行离线测试（在 Codex 沙箱里有一项 LibreOffice 测试会自动跳过）；也可直接复制 [交给 Agent 完成安装的提示词](SETUP.md#9-交给-agent-完成安装的提示词)。

## 文件

| 部分 | 文件 |
| --- | --- |
| 目标 | [intent.md](intent.md) |
| 行为规格与命令 | [spec.md](spec.md) |
| Skills | [`.agents/skills/`](.agents/skills/)（Claude Code 经 `.claude/skills` 链接读取） |
| 脚本 | [`assistant.py`](assistant.py)（状态守卫、ATS 接口）、[`render_resume.py`](render_resume.py)（简历版本）、[`dashboard.py`](dashboard.py)（结果页） |
| 测试 | `test_*.py` |
| 安装和配置 | [SETUP.md](SETUP.md)、[`examples/`](examples/) |
| 版本变化与项目记忆 | [CHANGELOG.md](CHANGELOG.md)、[memory.md](memory.md) |

个人简历、事实库、简历版本、岗位清单、申请材料、结果页和登录检查记录保存在被 Git 忽略的 `private/`、`jobs/`、`runs/`、`jobs.csv`。密码、验证码、Cookie 和令牌不写入项目。"被 Git 忽略"只保证这些文件不会随正常提交上传到 GitHub。你交给 AI 工具的材料（例如把简历作为附件发给 Codex 或 Claude Code）按该工具自己的数据处理方式处理，请自行确认。

## 使用边界

- 不保证任意招聘网站都能自动化；第一次先用 1–2 个来源试跑。
- 浏览器步骤必须在交互会话中运行，以便你批准站点权限、登录和处理验证码；非交互运行中被拒的来源记为 `blocked`，不找替代路线。Agent 不替你注册招聘网站账号。
- 简历只能从你批准过的版本中选，版本里的每处改动都对应事实 ID，且必须忠于事实。每次实际重建（`--force`，或改了该版本的输入；输入没变的版本会跳过）都会让该版本的批准作废，需要重新批准。
- 真实提交需要你针对具体岗位审核并批准；结果不明时不自动重试。`preflight` 通过只代表本地检查通过，不代表网站已收到申请。

许可：[MIT License](LICENSE)。
