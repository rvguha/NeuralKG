#!/usr/bin/env python3
"""Materialize the public Atlas GitHub catalog for NeuralKG's local ARD.

Atlas has two catalog inputs: checked-in OKF documents and a checked-in
BigQuery crawl allowlist.  The upstream crawler stores the latter directly in
BigQuery; this script writes the same deterministic table descriptions as
Markdown so NeuralKG's file-backed ARD can index the complete public pack.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import shutil
from collections import defaultdict

import yaml
from google.cloud import bigquery


ROOT = pathlib.Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "instances" / "atlas" / "catalog"
UPSTREAM_COMMIT = "833b29eb952dde311f17a33fa5bfa2923998aab4"
PUBLIC_TARGETS = (
    ("bigquery-public-data", "covid19_open_data"),
    ("bigquery-public-data", "census_bureau_acs"),
    ("bigquery-public-data", "world_bank_health_population"),
    ("bigquery-public-data", "world_bank_wdi"),
    ("bigquery-public-data", "epa_historical_air_quality"),
    ("bigquery-public-data", "noaa_gsod"),
    ("bigquery-public-data", "google_trends"),
    ("bigquery-public-data", "bls"),
    ("bigquery-public-data", "chicago_crime"),
    ("bigquery-public-data", "san_francisco"),
    ("bigquery-public-data", "new_york"),
    ("bigquery-public-data", "openaq"),
    ("bigquery-public-data", "usa_names"),
    ("bigquery-public-data", "fec"),
)
LARGE_TABLE_BYTES = 50 * 1024**3


def upstream_targets(atlas_repo: pathlib.Path) -> tuple[tuple[str, str], ...]:
    """Load the public allowlist from a checked-out Atlas repository."""
    target_file = atlas_repo / "backend" / "crawler" / "targets.py"
    spec = importlib.util.spec_from_file_location("atlas_crawl_targets", target_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {target_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple((row[0], row[1]) for row in module.CRAWL_TARGETS if "public" in row[2])


def copy_static_catalog(atlas_repo: pathlib.Path) -> list[str]:
    source = atlas_repo / "okf-catalog" / "attested-computations"
    destination = DESTINATION / "attested-computations"
    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    for path in sorted(source.glob("*.md")):
        shutil.copy2(path, destination / path.name)
        copied.append(path.name)
    worked = atlas_repo / "okf-catalog" / "bigquery" / "covid19_open_data" / "table.md"
    worked_dest = DESTINATION / "bigquery" / "covid19_open_data" / "table.md"
    worked_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(worked, worked_dest)
    return copied + ["bigquery/covid19_open_data/table.md"]


def dataset_rows(client: bigquery.Client, project: str, dataset: str):
    dataset_ref = client.get_dataset(f"{project}.{dataset}")
    location = dataset_ref.location
    storage_sql = f"""
      SELECT table_name, row_count, total_logical_bytes
      FROM `{project}.{dataset}`.INFORMATION_SCHEMA.TABLE_STORAGE
    """
    try:
        tables = list(client.query(storage_sql, location=location).result(timeout=120))
    except Exception:
        tables = [
            {"table_name": item.table_id, "row_count": None, "total_logical_bytes": None}
            for item in client.list_tables(dataset_ref)
        ]
    columns_sql = f"""
      SELECT table_name, column_name, data_type, ordinal_position
      FROM `{project}.{dataset}`.INFORMATION_SCHEMA.COLUMNS
      ORDER BY table_name, ordinal_position
    """
    columns = defaultdict(list)
    for row in client.query(columns_sql, location=location).result(timeout=180):
        columns[row.table_name].append((row.column_name, row.data_type))
    return tables, columns


def value(row, name):
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def render_descriptor(project: str, dataset: str, row, columns) -> str:
    table = value(row, "table_name")
    count = value(row, "row_count")
    size = value(row, "total_logical_bytes")
    size_gb = (size / 1024**3) if size else None
    full_name = f"{project}.{dataset}.{table}"
    schema_summary = ", ".join(f"{name} ({kind})" for name, kind in columns[:60])
    description = (
        f"BigQuery table `{full_name}`. "
        f"{count if count is not None else 'unknown'} rows"
        + (f", approximately {size_gb:.1f} GB. " if size_gb else ". ")
        + f"Columns: {schema_summary}"
    )
    frontmatter = {
        "id": f"bq.{full_name}",
        "type": "Table",
        "title": f"{dataset}.{table}",
        "description": description[:4000],
        "trust": "machine-confirmed",
        "pack": "public",
        "visibility": "public",
        "source": {"kind": "bigquery", "project": project, "dataset": dataset, "table": table},
        "row_count": count,
        "size_gb": round(size_gb, 2) if size_gb else None,
        "large_table": bool(size and size > LARGE_TABLE_BYTES),
    }
    body = [
        "",
        "## Schema confirmed from BigQuery",
        "",
        f"Fully qualified table: `{full_name}`.",
        "",
    ]
    body.extend(f"- `{name}` ({kind})" for name, kind in columns)
    body.extend([
        "",
        "This descriptor is generated mechanically from BigQuery INFORMATION_SCHEMA by",
        "`scripts/sync_atlas_catalog.py`, following Atlas's checked-in crawler allowlist.",
        "",
    ])
    return "---\n" + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True) + "---\n" + "\n".join(body)


def crawl(client: bigquery.Client) -> dict[str, int]:
    counts = {}
    for project, dataset in PUBLIC_TARGETS:
        tables, columns = dataset_rows(client, project, dataset)
        folder = DESTINATION / "bigquery" / dataset
        folder.mkdir(parents=True, exist_ok=True)
        written = 0
        for row in sorted(tables, key=lambda item: value(item, "table_name")):
            table = value(row, "table_name")
            target = folder / f"{table}.md"
            # Preserve Atlas's hand-authored worked example at its upstream path.
            if dataset == "covid19_open_data" and table == "covid19_open_data":
                continue
            target.write_text(render_descriptor(project, dataset, row, columns.get(table, [])))
            written += 1
        counts[dataset] = written + (1 if dataset == "covid19_open_data" else 0)
        print(f"{dataset}: {counts[dataset]} descriptors", flush=True)
    return counts


def verify_catalog() -> tuple[bool, str]:
    manifest_path = DESTINATION / "atlas-source.json"
    if not manifest_path.exists():
        return False, "Atlas catalog manifest is missing"
    manifest = json.loads(manifest_path.read_text())
    targets = tuple((item["project"], item["dataset"]) for item in manifest.get("public_targets", []))
    if manifest.get("commit") != UPSTREAM_COMMIT or targets != PUBLIC_TARGETS:
        return False, "Atlas catalog manifest does not match the pinned upstream inputs"
    for relative in manifest.get("static_documents", []):
        path = DESTINATION / (relative if relative.startswith("bigquery/") else f"attested-computations/{relative}")
        if not path.exists():
            return False, f"Atlas static document is missing: {relative}"
    actual = {}
    for _project, dataset in PUBLIC_TARGETS:
        actual[dataset] = len(list((DESTINATION / "bigquery" / dataset).glob("*.md")))
    if actual != manifest.get("table_descriptor_counts"):
        return False, f"Atlas crawled descriptor counts differ: expected {manifest.get('table_descriptor_counts')}, got {actual}"
    total = len(list(DESTINATION.glob("**/*.md"))) - 1  # README is not an ARD document.
    if total != manifest.get("total_documents"):
        return False, f"Atlas document total differs: expected {manifest.get('total_documents')}, got {total}"
    return True, f"Atlas public catalog verified: {total} documents from {len(PUBLIC_TARGETS)} datasets"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atlas-repo", type=pathlib.Path,
                        help="optional checkout of github.com/srikanthbelwadi/atlas; also refreshes static OKF")
    parser.add_argument("--project", help="billing/quota project for read-only metadata jobs")
    parser.add_argument("--verify", action="store_true", help="check the materialized corpus without network access")
    args = parser.parse_args()
    if args.verify:
        ok, message = verify_catalog()
        print(message)
        raise SystemExit(0 if ok else 1)
    if args.atlas_repo:
        actual = upstream_targets(args.atlas_repo)
        if actual != PUBLIC_TARGETS:
            raise SystemExit(f"Atlas public targets changed; update this importer first:\n{actual!r}")
        copied = copy_static_catalog(args.atlas_repo)
    else:
        copied = [path.name for path in sorted((DESTINATION / "attested-computations").glob("*.md"))]
        copied.append("bigquery/covid19_open_data/table.md")
    client = bigquery.Client(project=args.project) if args.project else bigquery.Client()
    counts = crawl(client)
    manifest = {
        "upstream": "https://github.com/srikanthbelwadi/atlas",
        "commit": UPSTREAM_COMMIT,
        "static_documents": copied,
        "public_targets": [{"project": p, "dataset": d} for p, d in PUBLIC_TARGETS],
        "table_descriptor_counts": counts,
        "total_documents": len(copied) + sum(counts.values()) - 1,
    }
    (DESTINATION / "atlas-source.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Atlas catalog synchronized: {manifest['total_documents']} documents")


if __name__ == "__main__":
    main()
