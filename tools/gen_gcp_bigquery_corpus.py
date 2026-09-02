#!/usr/bin/env python3
"""Generate the standalone GCP BigQuery public/free-Marketplace OKF and ARD corpus.

The inputs are checked-in catalog snapshots. Generation is deliberately offline: refreshing a
catalog and rendering a reviewed snapshot are separate operations, so an ordinary build cannot
silently add, remove, or reprice a Marketplace product.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import yaml


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog"
CORPUS = ROOT / "corpora" / "gcp-bigquery"
OKF = CORPUS / "okf"
ARD = CORPUS / "ard"

ARD_CONTEXT = "https://agenticresourcediscovery.org/context/v1"
OKF_NS = "https://github.com/GoogleCloudPlatform/knowledge-catalog/okf/ns#"
ARD_FIELDS = {"title", "description", "tags", "representativeQueries", "trust"}


def slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "dataset"


def display_name(dataset_id: str) -> str:
    return re.sub(r"\s+", " ", dataset_id.replace("_", " ").replace("-", " ")).strip().title()


def semantic_tokens(text: str) -> list[str]:
    stop = {"and", "data", "dataset", "for", "from", "gcp", "google", "public", "the", "with"}
    return [word for word in re.findall(r"[a-z0-9]+", text.lower()) if len(word) > 2 and word not in stop][:6]


def write_okf(path: Path, frontmatter: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(f"---\n{rendered}---\n\n{body.rstrip()}\n", encoding="utf-8")


def read_okf(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    _, raw_frontmatter, body = text.split("---", 2)
    return yaml.safe_load(raw_frontmatter) or {}, body.lstrip("\r\n")


def mechanical_ard(access_path: Path, leaf_path: Path, family: str, authority: str) -> dict:
    access, _ = read_okf(access_path)
    leaf, body = read_okf(leaf_path)
    effective = dict(access)
    effective.update(leaf)

    leaf_name = leaf_path.stem
    entry = {
        "@context": [ARD_CONTEXT, {"okf": OKF_NS}],
        "identifier": f"urn:air:{authority}:okf:{family}.{leaf_name}",
        "displayName": leaf.get("title", ""),
        "type": "application/okf-table+markdown",
        "description": leaf.get("description", ""),
        "representativeQueries": leaf.get("representativeQueries") or [],
        "tags": leaf.get("tags") or [family],
        "okf:sourceDocument": str(leaf_path.relative_to(ROOT)),
        "okf:sourceDirectory": family,
        "okf:accessDescriptor": str(access_path.relative_to(ROOT)),
    }
    trust = effective.get("trust") or {}
    if trust.get("identity"):
        entry["trustManifest"] = {key: value for key, value in trust.items() if value}
    for key, value in effective.items():
        if value in (None, "", [], {}):
            continue
        if key in ARD_FIELDS and not (key == "trust" and "trustManifest" not in entry):
            continue
        entry[f"okf:{key}"] = value
    entry["data"] = {"content": body}
    return entry


def public_access() -> dict:
    return {
        "type": "Data Source",
        "title": "Google BigQuery public datasets (access)",
        "description": "Datasets in Google's directly queryable bigquery-public-data project.",
        "publisher": "Google Cloud",
        "trust": {"identity": "did:web:cloud.google.com", "identityType": "did"},
        "access": {
            "auth": "gcp",
            "operations": {
                "listTables": {
                    "method": "GET",
                    "url": "https://bigquery.googleapis.com/bigquery/v2/projects/"
                           "bigquery-public-data/datasets/{datasetId}/tables",
                    "capability": {"requires_env": "GOOGLE_CLOUD_PROJECT"},
                },
                "getDataset": {
                    "method": "GET",
                    "url": "https://bigquery.googleapis.com/bigquery/v2/projects/"
                           "bigquery-public-data/datasets/{datasetId}",
                    "capability": {"requires_env": "GOOGLE_CLOUD_PROJECT"},
                },
                "query": {
                    "method": "POST",
                    "url": "https://bigquery.googleapis.com/bigquery/v2/projects/"
                           "{billingProject}/queries",
                    "capability": {
                        "requires_env": "GOOGLE_CLOUD_PROJECT",
                        "language": "GoogleSQL",
                        "paths": ["key", "filter", "order", "enumerate"],
                        "grain": "entity",
                        "population": {"complete": True},
                    },
                },
            },
        },
        "costNotice": "No data-product subscription fee; normal BigQuery processing and egress "
                      "charges can apply, subject to Google Cloud free-tier allowances.",
        "entityType": "a Google-hosted public BigQuery dataset",
    }


def marketplace_access() -> dict:
    return {
        "type": "Data Source",
        "title": "Free BigQuery data products on Google Cloud Marketplace (access)",
        "description": "Data products returned by the Google Cloud Marketplace Data + Free filters.",
        "publisher": "Google Cloud Marketplace",
        "trust": {"identity": "did:web:cloud.google.com", "identityType": "did"},
        "access": {
            "auth": "gcp-marketplace-account",
            "operations": {
                "viewListing": {"method": "GET", "url": "{marketplaceUrl}"},
                "subscribe": {
                    "method": "INTERACTIVE",
                    "url": "{marketplaceUrl}",
                    "capability": {"createsLinkedDataset": True, "subscriptionRequired": True},
                },
            },
        },
        "costNotice": "Marketplace price is Free; subscription can still be required and normal "
                      "BigQuery processing and egress charges can apply.",
        "entityType": "a free BigQuery data product listed on Google Cloud Marketplace",
    }


def load_inputs() -> tuple[list[dict], list[dict]]:
    public = json.loads((CATALOG / "gcp-bigquery-public-datasets.json").read_text(encoding="utf-8"))
    public_meta = json.loads(
        (CATALOG / "gcp-bigquery-public-datasets.meta.json").read_text(encoding="utf-8"))
    marketplace_doc = json.loads(
        (CATALOG / "gcp-marketplace-free-datasets.json").read_text(encoding="utf-8"))
    if public_meta["result_count"] != len(public):
        raise ValueError("public BigQuery snapshot count does not match its metadata")
    if marketplace_doc["result_count"] != len(marketplace_doc["products"]):
        raise ValueError("Marketplace snapshot count does not match its metadata")
    return public, marketplace_doc["products"]


def generate() -> dict[str, int]:
    public, marketplace = load_inputs()
    public_okf = OKF / "public"
    market_okf = OKF / "marketplace-free"
    public_ard = ARD / "public"
    market_ard = ARD / "marketplace-free"
    for directory in (public_okf, market_okf, public_ard, market_ard):
        directory.mkdir(parents=True, exist_ok=True)

    write_okf(public_okf / "_access.md", public_access(),
              "# Access\n\nThis access document is inherited by every Google public-dataset leaf.")
    write_okf(market_okf / "_access.md", marketplace_access(),
              "# Access\n\nThis access document is inherited by every free Marketplace listing leaf.")

    market_by_normalized_slug = {}
    for product in marketplace:
        product_slug = urlparse(product["url"]).path.rstrip("/").split("/")[-1]
        market_by_normalized_slug.setdefault(slug(product_slug).replace("-", ""), []).append(product)

    public_names = set()
    for dataset in public:
        ref = dataset["datasetReference"]
        dataset_id = ref["datasetId"]
        leaf_name = slug(dataset_id)
        if leaf_name in public_names:
            raise ValueError(f"duplicate public leaf slug: {leaf_name}")
        public_names.add(leaf_name)
        matches = market_by_normalized_slug.get(leaf_name.replace("-", ""), [])
        listing = matches[0] if len(matches) == 1 else None
        title = (listing or {}).get("title") or dataset.get("friendlyName") or display_name(dataset_id)
        description = ((listing or {}).get("description") or
                       f"Google-hosted public BigQuery dataset `{ref['projectId']}.{dataset_id}`.")
        tags = ["gcp", "bigquery", "public-data", "dataset", *semantic_tokens(title)]
        frontmatter = {
            "type": "BigQuery Public Dataset",
            "title": title,
            "description": description,
            "tags": list(dict.fromkeys(tags)),
            "source": "./_access.md",
            "project": ref["projectId"],
            "datasetId": dataset_id,
            "resource": f"{ref['projectId']}.{dataset_id}",
            "location": dataset.get("location"),
            "datasetType": dataset.get("type"),
            "labels": dataset.get("labels") or {},
            "accessCost": "free-access; BigQuery usage charges may apply",
            "representativeQueries": [
                f"What data is available in the {title} BigQuery dataset?",
                f"Show me the tables in {ref['projectId']}.{dataset_id}",
                f"How can I query the {title} public dataset?",
            ],
        }
        if listing:
            frontmatter["marketplaceUrl"] = listing["url"]
            frontmatter["marketplacePublisher"] = listing.get("publisher")
        leaf = public_okf / f"{leaf_name}.md"
        write_okf(leaf, frontmatter,
                  f"# Dataset\n\n`{ref['projectId']}.{dataset_id}` is located in "
                  f"`{dataset.get('location', 'unknown')}`.\n")
        entry = mechanical_ard(public_okf / "_access.md", leaf,
                               "gcp-bigquery-public", "cloud.google.com")
        (public_ard / f"{leaf_name}.json").write_text(
            json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    market_names = set()
    for product in marketplace:
        parts = urlparse(product["url"]).path.rstrip("/").split("/")
        leaf_name = slug("-".join(parts[-2:]))
        if leaf_name in market_names:
            raise ValueError(f"duplicate Marketplace leaf slug: {leaf_name}")
        market_names.add(leaf_name)
        title = product["title"]
        description = product.get("description") or f"Free data product published by {product.get('publisher') or 'an external provider'}."
        frontmatter = {
            "type": "Google Cloud Marketplace Data Product",
            "title": title,
            "description": description,
            "tags": list(dict.fromkeys([
                "gcp", "bigquery", "marketplace", "free-data-product", *semantic_tokens(title)
            ])),
            "source": "./_access.md",
            "publisher": product.get("publisher") or "Publisher not displayed",
            "marketplaceUrl": product["url"],
            "marketplacePrice": "free",
            "accessCost": "free listing; BigQuery usage charges may apply",
            "representativeQueries": [
                f"What data does the {title} Marketplace product provide?",
                f"Who publishes the {title} BigQuery data product?",
                f"How can I access the free {title} dataset on Google Cloud Marketplace?",
            ],
        }
        leaf = market_okf / f"{leaf_name}.md"
        write_okf(leaf, frontmatter,
                  f"# Marketplace listing\n\n[{title}]({product['url']}) is listed as Free in the "
                  "Google Cloud Marketplace Data catalog. Subscription and BigQuery usage terms "
                  "remain those shown by Google and the publisher.\n")
        entry = mechanical_ard(market_okf / "_access.md", leaf,
                               "gcp-marketplace-free", "cloud.google.com")
        (market_ard / f"{leaf_name}.json").write_text(
            json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    expected = {
        public_okf: {f"{name}.md" for name in public_names} | {"_access.md"},
        public_ard: {f"{name}.json" for name in public_names},
        market_okf: {f"{name}.md" for name in market_names} | {"_access.md"},
        market_ard: {f"{name}.json" for name in market_names},
    }
    for directory, keep in expected.items():
        for path in directory.iterdir():
            if path.is_file() and path.name not in keep:
                path.unlink()

    summary = {"public": len(public_names), "marketplace_free": len(market_names)}
    (CORPUS / "manifest.json").write_text(json.dumps({
        "name": "GCP BigQuery public and free Marketplace data",
        "inputs": {
            "public": "catalog/gcp-bigquery-public-datasets.json",
            "marketplace_free": "catalog/gcp-marketplace-free-datasets.json",
        },
        "counts": summary,
        "okf_entries": sum(summary.values()),
        "ard_entries": sum(summary.values()),
        "cost_semantics": "Free means no dataset/listing subscription price; BigQuery usage "
                          "charges can still apply.",
    }, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    summary = generate()
    print(f"generated {summary['public']} public + {summary['marketplace_free']} free Marketplace "
          "OKF entries and the same number of mechanical ARD entries")


if __name__ == "__main__":
    main()
