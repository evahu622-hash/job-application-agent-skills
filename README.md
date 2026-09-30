# Job Application Agent Skills

一套供个人使用的 Codex 项目 Skills：用浏览器找岗位、依据真实履历逐岗生成简历、填写申请表，并在最终提交前让用户检查。仓库还包含一个标准库 Python 状态守卫和测试。它不是无人值守的批量投递服务；网页操作由运行时可用的浏览器完成。

## 下载与开始

1. 在 GitHub 选择 **Code → Download ZIP**，解压后用 Codex 打开整个文件夹；也可以 `git clone` 仓库。四个 Skills 位于 `.agents/skills/`，需要连同仓库中的 `spec.md` 和 `assistant.py` 一起使用。若当前会话未识别新 Skills，重新打开项目或新建会话。
2. 按 [SETUP.md](SETUP.md) 完成首次配置：放入自己的原版简历，填写岗位范围和固定答案，建立并核对事实库，连接可操作的浏览器，在各目标站点完成首次登录。
3. 运行 `python3 -m unittest -v test_assistant.py`。需要 Python 3.10+；测试只验证本地状态逻辑。
4. 在 Codex 中调用 `$job-scout` 找岗，选定 `job_id` 后调用 `$resume-tailor`，最后调用 `$job-apply` 准备真实申请。需要串联时调用 `$job-run`。

## 文件

| 部分 | 文件 |
| --- | --- |
| 目标 | [intent.md](intent.md) |
| 行为规格 | [spec.md](spec.md) |
| Skills 与状态守卫 | [`.agents/skills/`](.agents/skills/)、[`assistant.py`](assistant.py) |
| 测试 | [`test_assistant.py`](test_assistant.py) |
| 项目记忆模板 | [memory.md](memory.md) |
| 安装和手动配置 | [SETUP.md](SETUP.md)、[`examples/`](examples/) |

个人简历、事实库、岗位清单、申请材料和登录检查记录保存在被 Git 忽略的 `private/`、`jobs/`、`runs/`、`jobs.csv`。密码、验证码、Cookie 和令牌不写入项目。每次进入申请页都重新检查浏览器登录态；某些站点仍会要求续登、验证码或人工处理。

**使用边界：** Skill 说明不能保证任意招聘网站可自动化。第一次运行先用少量站点试跑；真实提交默认需要用户审核具体岗位、表单和附件后批准。CLI 的 `preflight` 通过只代表本地检查通过，不代表网站已收到申请。

许可：[MIT License](LICENSE)。
