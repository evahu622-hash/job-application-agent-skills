# Job Application Agent Skills

一套供个人使用的求职 Agent Skills（Codex 与 Claude Code 通用）：从招聘系统（ATS，Applicant Tracking System，企业发布岗位、收简历的系统，如 Greenhouse）的公共接口和网页找岗位、核验职位描述（JD），按真实履历为每个岗位选择合适的简历版本，填写申请表，并在最终提交前让你检查。仓库还包含三个只用 Python 标准库的脚本和测试。它不是无人值守的批量投递服务。

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
3. **找岗**：`$job-scout`。ATS 来源走公共接口，可在 `codex exec` 中无人值守运行（需允许联网，命令见 [SETUP.md](SETUP.md#6-首次试跑)）；其他网站需要交互会话。
4. **选简历**：对选定的 `job_id` 调用 `$resume-tailor`。
5. **申请**：`$job-apply` 填好表单后停下，你审核后才提交。
6. **查看结果**：`python3 dashboard.py`，用浏览器打开 `runs/今天日期/dashboard.html`，一页看到配置是否齐全、岗位、每个岗位的全部文件、简历版本，以及最新的试跑日志和测试结果（自动读取 `runs/*/trial/` 和 `runs/*/tests.log`）。该页面含个人数据，只在本机查看。

需要串联时调用 `$job-run`。在 Claude Code 中把 `$` 换成 `/`，例如 `/job-scout`。

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
- **Claude Code**：Claude Code 读取 `.claude/skills/`。仓库里的 `.claude/skills` 是指向 `../.agents/skills` 的相对符号链接，两边用同一份 Skills，用 `/job-scout` 等调用（Claude Code 能否经链接发现 Skills 尚待新会话实测，见 memory.md）。浏览器步骤要用交互式会话（`claude`），`claude -p` 是非交互的。
- **Windows**：Git 可能把符号链接检出成一个普通文本文件。此时删除 `.claude\skills`，再把 `.agents\skills` 整个文件夹复制为 `.claude\skills`（PowerShell：`Remove-Item .claude\skills; Copy-Item -Recurse .agents\skills .claude\skills`）；每次更新仓库后重新复制。
- 若会话没有识别新 Skills，重新打开项目或新建会话。

## 下载与测试

[下载 ZIP](https://github.com/evahu622-hash/job-application-agent-skills/archive/refs/heads/main.zip)，或 `git clone https://github.com/evahu622-hash/job-application-agent-skills.git`。需要 Python 3.10+、LibreOffice 和 poppler（一组 PDF 命令行工具；安装见 [SETUP.md](SETUP.md)）。运行 `python3 -m unittest -v` 执行离线测试（在 Codex 沙箱里有一项 LibreOffice 测试会自动跳过）；也可直接复制 [交给 Agent 完成安装的提示词](SETUP.md#9-交给-agent-完成安装的提示词)。

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

个人简历、事实库、简历版本、岗位清单、申请材料、结果页和登录检查记录保存在被 Git 忽略的 `private/`、`jobs/`、`runs/`、`jobs.csv`。密码、验证码、Cookie 和令牌不写入项目。

## 使用边界

- 不保证任意招聘网站都能自动化；第一次先用 1–2 个来源试跑。
- 浏览器步骤必须在交互会话中运行，以便你批准站点权限、登录和处理验证码；非交互运行中被拒的来源记为 `blocked`，不找替代路线。Agent 不替你注册招聘网站账号。
- 简历只能从你批准过的版本中选，版本里的每处改动都对应事实 ID，且必须忠于事实。每次重新构建都会让该版本的批准作废，需要重新批准。
- 真实提交需要你针对具体岗位审核并批准；结果不明时不自动重试。`preflight` 通过只代表本地检查通过，不代表网站已收到申请。

许可：[MIT License](LICENSE)。
