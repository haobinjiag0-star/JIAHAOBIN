# JIAHAOBIN

简要说明这个仓库的目的、包含的文件和如何生成 data/companies.json。

## 说明
这个仓库包含用于从 CSV 生成 companies.json 的脚本和 GitHub Actions workflow，用来自动更新 data/companies.json。

## 使用
本地运行：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # 如果有 requirements.txt
python scripts/collect_jobs.py
```

在 GitHub Actions 上：
- Workflow: .github/workflows/generate_companies.yml
- 手动触发或当 data/*.csv 或 scripts/collect_jobs.py 修改时自动运行。

## 文件
- scripts/collect_jobs.py — 抓取/处理数据并输出到 data/companies.json
- data/companies.json — 自动生成的 JSON 数据（由 workflow 提交 PR）

## 贡献
欢迎 issue / PR。
