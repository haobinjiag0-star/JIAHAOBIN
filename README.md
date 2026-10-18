# JIAHAOBIN

用于筛选日本新卒 / 既卒企业，并汇总企业官网公开的招聘信息。

## 招聘数据更新

- `data/companies.csv` 是待检查企业清单，目前包含首批 50 家企业的企业信息、法人番号和官方招聘页 URL。
- `scripts/collect_jobs.py` 每天读取清单中的公开招聘页，并生成 `data/jobs.json`。
- `.github/workflows/pages.yml` 每天日本时间 02:17 运行，也支持在 GitHub Actions 中手动运行；更新清单、脚本或网页时也会自动运行并发布 GitHub Pages。
- `jobs.json` 是每次工作流运行时生成并随 Pages 发布的数据文件，不会自动提交回仓库。

## 抓取范围与准确性

程序只访问 CSV 中列出的企业官方招聘页。会先遵守对应站点的 `robots.txt`，按站点限速，并跳过无法确认允许访问、需要登录、页面过大或非 HTML 的页面。不会登录、绕过访问限制、执行页面 JavaScript，也不会抓取未列出的求职平台。

岗位、应聘对象、申请期间、说明会、选考流程和申请链接只在页面内容明确时提取；每项附来源网址、页面检查时间、证据片段和置信度。没有足够依据时状态为 `needs_confirmation`，网站显示“需确认”，不会根据旧信息猜日期。动态 JavaScript 页面或需要登录的内容可能无法自动读取。

自动检查结果用于线索整理。正式投递前请通过企业官网确认招聘年度、对象和截止日期。

## 更新企业清单

按现有表头向 `data/companies.csv` 添加记录，至少填写 `company`、`companyNumber` 和 `companyRecruitmentUrl`。`companyNumber` 用于跨数据源去重；没有官方招聘页时可留空，程序会将该企业标为需补充来源。提交到 `main` 后工作流会自动运行。

## 本地检查

```bash
python scripts/collect_jobs.py
```

脚本只使用 Python 标准库，无需安装额外依赖。