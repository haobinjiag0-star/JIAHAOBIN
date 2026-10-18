#!/usr/bin/env python3
"""Merge scraper shard outputs and ensure every source company is represented."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPANY_DIR = ROOT / "data" / "companies"


def company_key(row: dict[str, str]) -> str:
    number = "".join(ch for ch in (row.get("companyNumber") or "") if ch.isdigit())
    return f"法人番号:{number}" if len(number) == 13 else f"企业名:{(row.get('company') or '').strip()}"


def load_expected_keys() -> set[str]:
    paths = sorted(COMPANY_DIR.glob("companies-*.csv"))
    if not paths:
        raise FileNotFoundError(f"No company CSV files found in {COMPANY_DIR}")
    keys: set[str] = set()
    for path in paths:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("company"):
                    keys.add(company_key(row))
    return keys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("shards", type=Path, help="directory containing jobs-shard-N.json files")
    parser.add_argument("output", type=Path, help="merged jobs.json output path")
    args = parser.parse_args()

    files = sorted(args.shards.glob("jobs-shard-*.json"))
    if not files:
        raise FileNotFoundError(f"No shard outputs found in {args.shards}")
    records: list[dict[str, object]] = []
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"{path} does not contain a JSON array")
        records.extend(payload)

    expected = load_expected_keys()
    found: dict[str, dict[str, object]] = {}
    duplicates: list[str] = []
    for record in records:
        key = str(record.get("companyMatchKey") or "")
        if not key:
            raise ValueError(f"A record is missing its company match key: {record.get('company')}")
        if key in found:
            duplicates.append(key)
        found[key] = record
    missing = expected - found.keys()
    unexpected = found.keys() - expected
    if duplicates or missing or unexpected:
        raise RuntimeError(
            f"Shard completeness check failed: expected={len(expected)}, found={len(found)}, "
            f"missing={len(missing)}, duplicate={len(duplicates)}, unexpected={len(unexpected)}"
        )

    records.sort(key=lambda item: (str(item.get("company") or "").casefold(), str(item.get("companyNumber") or "")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    accessible = sum(bool(item.get("recruitmentSourcePageAccessible")) for item in records)
    open_count = sum(item.get("recruitmentSourceStatus") == "open" for item in records)
    confirmation = sum(item.get("recruitmentSourceStatus") == "needs_confirmation" for item in records)
    print(
        f"Merged {len(records)} companies from {len(files)} shards; "
        f"pages accessible={accessible}; explicitly open={open_count}; needs confirmation={confirmation}"
    )


if __name__ == "__main__":
    main()

