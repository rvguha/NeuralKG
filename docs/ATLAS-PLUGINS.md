# Atlas accessor integration

Enable `plugins.atlas_accessors` in the existing `extensions` list. Install
`plugins/requirements-atlas.txt` in that instance's environment. No cloud SDK is
imported by default: the plugin uses NeuralKG's existing async BigQuery client.

```yaml
extensions:
  - plugins.atlas_accessors
plugin_config:
  atlas_accessors:
    project: your-billing-project
    allowed_tables:
      - your-project.your-dataset.your-table
    byte_cap: 10737418240
    allow_ad_hoc: false
```

Credentials use the existing `GOOGLE_APPLICATION_CREDENTIALS` service-account
configuration; never place credentials in the catalog. Private descriptors are
currently refused pending entitlement integration. The allowlist is mandatory
and administrator-owned; an ARD entry cannot expand it.

Descriptors select `accessor: bigquery_guarded` or `bigquery_sample_llm`. Atlas's
`computation.runtime.sql` and typed `parameters` are retained, including defaults.
The plugin also accepts mechanically namespaced `okf:computation` and related
OKF fields. Bound values arrive in the operation's `params` object. Publish
normal `access.operations` capabilities so the existing planner knows which
parameters and input shapes the operation supplies. No extra descriptor call is
made by the plugin. A logical read operator is not the resource operation name.

The SQL path checks a single read-only statement against an explicit table
allowlist, dry-runs with bound parameters, and executes with a hard byte cap.
Unknown/remote functions are refused conservatively. Earth Engine and arbitrary
UDFs are NOT enabled yet. All pages are read; no LIMIT is added by the plugin.
Returned relations retain execution provenance and BigQuery statistics.
`context.operation_events` records attempts and is shared by child contexts;
it is not yet a persisted monthly budget ledger, and failed jobs may have unknown
final billed bytes. Do not describe this as complete billing enforcement.

The theming path executes the reviewed sample query and invokes the existing
accounted model client. Quotes are checked against their specific cited row;
the original sample and failed quote checks remain in evidence. Substring
verification does not establish truth, relevance, or population representativeness.

This is an incremental port of Atlas's guarded execution and narrative-theming
design (srikanthbelwadi/atlas, Apache-2.0, revision
833b29eb952dde311f17a33fa5bfa2923998aab4), using NeuralKG's existing runtime.
SEC/Data Commons/composites, private policy, UI and deployment parity remain
separate integration work. No claim of live parity is made by offline tests.
