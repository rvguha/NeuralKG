# Instance-local test reports

The instance application serves `/tests/` and `/tests/<run-id>/<stage>.html`.
The four stage names are `understanding`, `ard`, `planning`, and `execution`.
`results.json` contains the saved rows, and `manifest.json` describes the run.

By default, reports live beneath `.local/test-runs/<config-path-hash>` beside the
instance YAML. Configure `tests.results_dir` in that YAML or `TEST_RESULTS_DIR`
to use a persistent volume. Relative paths are relative to the instance YAML.
Different instances must not share an explicitly overridden results directory.
The default is stable across server restarts but isolated by instance config path.

`stage_reports.publish(directory, manifest, rows)` atomically saves artifacts.
Each row has `id`, `question`, and stage results with `status`, `summary`, and
the saved output. Missing stages are displayed as pending, never passed.

Reports are readable by anyone who can reach the instance; use the instance's
access controls. Publish only appropriate test data, never credentials or raw
environment dumps. HTML output is escaped and the serving route rejects paths
and symlinks outside the report root. Reports are not committed to Git.

Current implementation is the report infrastructure only. The 76-template live
runner has not been installed or run: its external payload needs approval.
The current ARD embedding configuration is not OSS, and the replacement catalog
does not yet have a production compiler/executor. The report infrastructure does
not bypass these gaps with the old eleven-shape pipeline.

Offline checks:

    .venv/bin/python -m unittest discover -s tests -p 'test_stage_reports.py' -v
    .venv/bin/python -m unittest discover -s tests -p 'test_serving_architecture.py' -v
