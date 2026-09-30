# Job Application Agent：行为规格

四个项目 Skill 分别负责发现岗位（`job-scout`）、逐岗简历（`resume-tailor`）、网页申请（`job-apply`）和串联（`job-run`）。浏览器执行网页动作；`assistant.py` 负责岗位去重、提交前核验和结果状态。安装和手动配置见 [SETUP.md](SETUP.md)。

## 文件合同

| 文件 | 内容 |
| --- | --- |
| `private/source/` | 使用者自己的原版简历及可选历史岗位表；不加入 Git。 |
| `private/targets.yaml` | 目标职位、地区、语言、硬条件、来源网站与扫描频率。 |
| `private/career_facts.md` | 从源简历核对的事实 ID、原始出处和是否可用于申请。 |
| `private/answers.md` | 已确认的联系、到岗、工作许可和薪资等答案；不存凭据。 |
| `private/site_sessions.md` | 各站点的浏览器登录检查记录；不存密码、验证码、Cookie 或令牌。 |
| `jobs.csv` | 一行一个 `job_id`，由 `assistant.py upsert-job` 更新。 |
| `jobs/JOB_ID/jd.txt` | 原始 JD 正文；仅在原文变化时修改，用于计算哈希。 |
| `jobs/JOB_ID/jd.md` | 原站 URL、访问时间、来源状态与摘要。 |
| `jobs/JOB_ID/fit.md` | 硬条件满足 / 不满足 / 未知，JD 引文和事实 ID。 |
| `jobs/JOB_ID/resume-plan.md` | 选择、排序、删除和改写计划。 |
| `jobs/JOB_ID/resume-diff.md` | 每处改动和事实来源。 |
| `jobs/JOB_ID/resume.pdf` | 待上传的逐岗 PDF。 |
| `jobs/JOB_ID/fact-check.md` | 事实、PDF 文本和版式检查。 |
| `jobs/JOB_ID/pre-submit.json` | 表单回读和附件核验结果。 |
| `jobs/JOB_ID/application.json` | 提交结果、时间与页面证据。 |
| `runs/YYYY-MM-DD/digest.md` | 本轮扫描范围、岗位和失败原因。 |

`job_id` 优先取原站稳定岗位 ID；否则由规范化原始 URL 派生，不能仅凭相似标题合并。

## 流程

1. **配置**：使用者放入自己的简历、填写目标和固定答案，核对事实库。未知的工作许可、签证、薪资或到岗答案保持 `unknown`。
2. **找岗**：按配置逐站搜索，打开雇主或 ATS 原始 JD。只有完整 JD 与有效 Apply 入口均已看见，才标 `active_verified`；404 标 `closed`；登录墙和访问错误标 `blocked` 或 `unknown`。搜索摘要和历史表格只作线索。
3. **筛选**：先判断位置、语言、资历、职责和其他用户硬条件，再评分。未知或不满足的硬条件不推荐进入申请。
4. **简历**：生成计划、逐条事实 diff 和 PDF；核对日期、数字、雇主、职责、可复制文本及版式。冲突未解则暂停。
5. **登录与填表**：优先复用同一招聘站点的持久浏览器会话，并在原站确认已进入申请表。每次运行都重新验证登录状态；记录元数据到 `private/site_sessions.md`。若缺密码、二次验证、CAPTCHA 或协议选择，由用户在网页处理。逐页填写已确认答案并回读；上传当前岗位 PDF，确认页面显示该附件。未知必填或附件不明则暂停。
6. **预提交**：网页回读后写 `pre-submit.json`，运行 `python3 assistant.py preflight JOB_ID --resume jobs/JOB_ID/resume.pdf --pre-submit jobs/JOB_ID/pre-submit.json`。原站开放状态须在 72 小时内确认；`dry_run=true` 一律拦截。CLI 通过不代表网站已收到申请，也不构成提交授权。
7. **提交与记录**：向用户展示具体岗位、公司、URL、表单摘要和附件；获得该次最终提交批准后仅点击一次。明确成功页或回执才记 `submitted_confirmed`；结果不明记 `submission_unknown`，调查既有申请，不自动重试。

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

布尔值要由网页回读结果支持。CLI 还检查岗位身份、申请 URL、JD 版本、PDF 哈希、原站新鲜度和既有提交状态。浏览器登录态取决于站点会话有效期，不保证“一次登录永久有效”。

## 实现边界与验收

Skills 是运行说明，不是后台守护程序。`assistant.py` 不登录网站、生成 PDF、上传文件或点击提交。浏览器能力和站点规则会变化，需要逐站试跑；不绕过验证码、访问限制或站点安全措施。

运行 `python3 -m unittest -v test_assistant.py` 验证本地状态守卫。真实链路还需分别验证原始 JD、登录、表单、附件和提交回执；单元测试不能替代网页证据。
