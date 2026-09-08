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

Run the 76-template understanding evaluation with the approved OpenRouter
credentials configured:

    LLM_PROVIDER=openrouter .venv/bin/python tests/template_stage_run.py

This uses only `openai/gpt-oss-20b`. It checkpoints every case and publishes
HTML every ten completions. `--run-id <existing-id>` resumes a run with the same
prompt; `--workers N` controls concurrency. Default corpus: 76 authored examples,
308 common questions, 350 extended questions and 45 classification negatives.
Conditional migrated labels and disputed negatives are marked review, not scored
as exact gold. Entity/measure/parameter extraction is saved but not scored.

Only query understanding runs live in this runner. Subsequent pages explicitly
record blocked stages, not fabricated discovery/planning/execution results.
The current ARD embedding configuration is not OSS, and the replacement catalog
does not yet have a production compiler/executor. The report infrastructure does
not bypass these gaps with the old eleven-shape pipeline.

Offline checks:

    .venv/bin/python -m unittest discover -s tests -p 'test_stage_reports.py' -v
    .venv/bin/python -m unittest discover -s tests -p 'test_serving_architecture.py' -v
