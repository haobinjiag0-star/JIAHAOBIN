# scripts/README.md

This folder contains a small utility to convert CSV recruitment
records into a single JSON file consumed by the site.

Files added by the assistant on branch `feature/data-and-script`:

- scripts/collect_jobs.py  - Python script that reads CSV and writes data/companies.json
- data/companies.csv       - A small sample CSV with 4 companies

How to run locally (if you have Python):

1. Create a venv (optional but recommended):
   python3 -m venv venv
   source venv/bin/activate   # macOS / Linux
   venv\Scripts\Activate.ps1 # Windows PowerShell

2. Run the script:
   python3 scripts/collect_jobs.py

3. The script will write `data/companies.json`.

Notes:
- You already added a GitHub Actions workflow to auto-run this script when CSV changes.
- If you want me to also create a PR that contains the generated `data/companies.json`, tell me and I will generate it and open a PR branch.
