#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_FILE = DATA_DIR / "companies.json"

def load_csv_rows(path: Path):
    if not path.exists():
        return []

    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            company = (
                row.get("company")
                or row.get("Company")
                or row.get("企業名")
                or row.get("会社名")
                or ""
            ).strip()
            roles = (
                row.get("roles")
                or row.get("role")
                or row.get("職種")
                or row.get("职位")
                or ""
            ).strip()
            location = (
                row.get("location")
                or row.get("Location")
                or row.get("勤務地")
                or row.get("工作地点")
                or ""
            ).strip()
            url = (
                row.get("url")
                or row.get("URL")
                or row.get("応募URL")
                or row.get("链接")
                or ""
            ).strip()

            if not company and not roles and not location and not url:
                continue

            rows.append({
                "company": company,
                "roles": roles,
                "location": location,
                "url": url,
            })
    return rows

def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    csv_files = [
        DATA_DIR / "companies.csv",
        DATA_DIR / "jobs.csv",
        DATA_DIR / "recruitment.csv",
    ]

    records = []
    for csv_file in csv_files:
        records.extend(load_csv_rows(csv_file))

    # 去重：避免同一条记录重复出现
    seen = set()
    unique = []
    for item in records:
        key = (
            item.get("company", ""),
            item.get("roles", ""),
            item.get("location", ""),
            item.get("url", "")
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    OUTPUT_FILE.write_text(
        json.dumps(unique, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Exported {len(unique)} records to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
