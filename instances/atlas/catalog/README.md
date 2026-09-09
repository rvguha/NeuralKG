# Atlas public OKF catalog

This directory is the public Atlas catalog imported mechanically from
`srikanthbelwadi/atlas` revision `833b29eb952dde311f17a33fa5bfa2923998aab4`.

The source repository and these documents are Apache-2.0 licensed. NeuralKG serves them through
its ordinary ARD finder; no Atlas orchestration code is used.

Atlas's public catalog is not limited to the ten Markdown files in its repository. Its crawler
also names fourteen live BigQuery datasets. `scripts/sync_atlas_catalog.py` preserves the static
documents byte-for-byte and materializes deterministic, machine-confirmed OKF descriptors from
the schemas of every table in that allowlist. `atlas-source.json` records the exact source commit,
targets and counts. Regenerate it with configured Google application credentials:

```sh
.venv/bin/python scripts/sync_atlas_catalog.py --atlas-repo /path/to/atlas
```

Private and finance-pack descriptors are deliberately not included in this public instance;
those require their own pack selection and entitlement policy.
