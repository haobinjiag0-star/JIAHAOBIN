#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/collect_jobs.py

Reads CSV files under data/ and writes a de-duplicated JSON
file at data/companies.json. Designed to be run in CI (GitHub
Actions) or locally with Python 3.x.

Usage:
  python scripts/collect_jobs.py

The script looks for these CSVs (if present):
  - data/companies.csv
  - data/jobs.csv
  - data/recruitment.csv

It extracts a few common fields (company, roles, location, url),
removes exact duplicates, and writes data/companies.json.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import List, Dict

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_FILE = DATA_DIR / "companies.json"

CSV_FILES = [
    DATA_DIR / "companies.csv",
    DATA_DIR / "jobs.csv",
    DATA_DIR / "recruitment.csv",
]


def normalize_cell(value: str) -> str:
    return (value or "").strip()


def load_csv_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []

    rows: List[Dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            company = (
                r.get("company")
                or r.get("Company")
                or r.get("企業名")
                or r.get("会社名")
                or r.get("社名")
                or ""
            )
            roles = (
                r.get("roles")
                or r.get("role")
                or r.get("職種")
                or r.get("职位")
                or ""
            )
            location = (
                r.get("location")
                or r.get("Location")
                or r.get("勤務地")
                or r.get("工作地点")
                or ""
            )
            url = (
                r.get("url")
                or r.get("URL")
                or r.get("応募URL")
                or r.get("链接")
                or ""
            )

            company = normalize_cell(company)
            roles = normalize_cell(roles)
            location = normalize_cell(location)
            url = normalize_cell(url)

            if not (company or roles or location or url):
                continue

            rows.append({
                "company": company,
                "roles": roles,
                "location": location,
                "url": url,
            })
    return rows


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    records: List[Dict[str, str]] = []
    for p in CSV_FILES:
        records.extend(load_csv_rows(p))

    # Deduplicate by tuple of fields
    seen = set()
    unique = []
    for item in records:
        key = (
            item.get("company", ""),
            item.get("roles", ""),
            item.get("location", ""),
            item.get("url", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    OUTPUT_FILE.write_text(json.dumps(unique, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported {len(unique)} records to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
