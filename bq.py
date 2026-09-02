#!/usr/bin/env python3
"""Generic BigQuery population source — ranking / filtering / aggregation over a public-dataset table.

BigQuery public datasets are SQL, i.e. SERVER-AGGREGATE capabilities: they can order, filter and
count across a WHOLE population, which our per-entity REST sources cannot. Rather than a module per
table, each OKF leaf carries a `bq:` config naming the table, the value column, and the entity
column (plus how to turn that entity into a readable name):

    bq:
      table: bigquery-public-data.census_bureau_acs.county_2018_5yr
      field: median_income
      entity_field: geo_id
      entity_kind: fips
      name_table: bigquery-public-data.geo_us_boundaries.counties   # SQL join for names…
      name_key: geo_id
      name_field: county_name
      # …or, for EIN-keyed 990 data, resolve names afterward via ProPublica:
      # name_via: propublica

Two table SHAPES are handled by the same config:

  WIDE   one row per entity, the measure IS a column (census county_2018_5yr.median_income,
         irs_990.totrevenue). `field` names that column.

  LONG   one row per (entity, measure, period) fact, the value lives in a single `value`
         column and which measure it is comes from a filter (SEC sec_quarterly_financials:
         value in `value`, measure chosen by `measure_tag`). Declared by adding to `bq:`:
           value_field: value            # the numeric column
           group_agg: MAX                 # collapse many rows per entity -> one (MAX/SUM/…)
           filter: "measure_tag IN (…) AND form='10-K' AND …"   # picks the measure + de-noises
           value_max / value_min: 1e12    # sanity bounds (drop $10T filing typos)
         An entity may appear on many rows (quarters, restatements); `group_agg` picks one
         value per entity before ranking, so the population is companies, not filings.

CREDENTIAL-GATED: active only when GOOGLE_CLOUD_PROJECT is set (see planner.capabilities); otherwise
the source is invisible and population questions fall back to the honest refusal.
"""
import asyncio, json, os, re, time, uuid

import runtime
import llm

_OPS = {">": ">", ">=": ">=", "<": "<", "<=": "<="}


def available():
    return bool(os.getenv("GOOGLE_CLOUD_PROJECT"))


def _client():
    # A missing project/package is a CredentialError, not a backtrack-able miss: the search must stop
    # and tell the user to set it, not exhaust every other source first (see driver.CredentialError).
    import driver
    proj = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not proj:
        raise driver.CredentialError("BigQuery source needs a GCP project — set GOOGLE_CLOUD_PROJECT "
                                     "and application-default credentials "
                                     "(`gcloud auth application-default login`).")
    try:
        from google.cloud import bigquery
    except ImportError as e:
        raise driver.CredentialError(f"BigQuery source needs `pip install google-cloud-bigquery` ({e})")
    return bigquery.Client(project=proj)


def _rows(sql):
    import driver
    try:
        return [dict(r) for r in _client().query(sql).result()]
    except (SystemExit, driver.CredentialError):
        raise
    except Exception as e:
        raise SystemExit(f"BigQuery query failed: {str(e)[:160]}")


class AsyncBigQueryClient:
    """Direct async BigQuery REST client for service-account key-file deployments."""
    SCOPE = "https://www.googleapis.com/auth/bigquery"

    def __init__(self, project, http_client, credentials_file=None, location=None):
        self.project = project
        self.http = http_client
        self.credentials_file = credentials_file or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        self.location = location or os.getenv("BIGQUERY_LOCATION", "US")
        self.token = None
        self.token_expires_at = 0.0
        self.token_lock = asyncio.Lock()

    async def _access_token(self, context):
        if self.token and time.time() < self.token_expires_at - 60:
            return self.token
        await context.wait(self.token_lock.acquire())
        try:
            if self.token and time.time() < self.token_expires_at - 60:
                return self.token
            if not self.credentials_file:
                raise RuntimeError("async BigQuery requires GOOGLE_APPLICATION_CREDENTIALS")
            from google.auth import jwt
            from google.oauth2 import service_account
            credentials = service_account.Credentials.from_service_account_file(
                self.credentials_file, scopes=[self.SCOPE])
            now = int(time.time())
            assertion = jwt.encode(credentials.signer, {
                "iss": credentials.service_account_email, "scope": self.SCOPE,
                "aud": credentials._token_uri, "iat": now, "exp": now + 3600,
            }).decode()
            response = await context.provider_call("bigquery", lambda: self.http.post(
                credentials._token_uri,
                data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                      "assertion": assertion}, timeout=min(30, context.remaining() or 30)))
            response.raise_for_status()
            payload = response.json()
            self.token = payload["access_token"]
            self.token_expires_at = time.time() + int(payload.get("expires_in", 3600))
            return self.token
        finally:
            self.token_lock.release()

    async def _request(self, context, method, path, **kwargs):
        supplied_headers = kwargs.pop("headers", {})
        for attempt in range(2):
            token = await self._access_token(context)
            headers = {**supplied_headers, "Authorization": f"Bearer {token}"}
            response = await context.provider_call("bigquery", lambda: self.http.request(
                method, "https://bigquery.googleapis.com/bigquery/v2" + path,
                headers=headers, timeout=min(60, context.remaining() or 60), **kwargs))
            if response.status_code != 401 or attempt:
                response.raise_for_status()
                return response.json()
            await context.wait(self.token_lock.acquire())
            try:
                if self.token == token:
                    self.token = None
                    self.token_expires_at = 0
            finally:
                self.token_lock.release()
        raise RuntimeError("BigQuery authentication failed")

    @staticmethod
    def _error(payload):
        status = payload.get("status") or {}
        errors = status.get("errors") or payload.get("errors") or []
        if errors:
            return "; ".join(error.get("message", str(error)) for error in errors)
        return None

    @staticmethod
    def _value(value, field_type):
        if value is None:
            return None
        if field_type in ("RECORD", "STRUCT") or isinstance(value, (dict, list)):
            raise RuntimeError("nested or repeated BigQuery results are not supported")
        if field_type in ("INTEGER", "INT64"):
            return int(value)
        if field_type in ("FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"):
            return float(value)
        if field_type in ("BOOLEAN", "BOOL"):
            return str(value).lower() == "true"
        return value

    async def cancel(self, job_id, location=None):
        try:
            token = self.token
            if not token:
                return
            await self.http.post(
                f"https://bigquery.googleapis.com/bigquery/v2/projects/{self.project}/jobs/"
                f"{job_id}/cancel", params={"location": location or self.location},
                headers={"Authorization": f"Bearer {token}"}, timeout=10)
        except Exception:
            pass

    async def list_tables(self, project, dataset, *, context):
        rows, token = [], None
        while True:
            params = {"maxResults": 1000}
            if token:
                params["pageToken"] = token
            payload = await self._request(
                context, "GET", f"/projects/{project}/datasets/{dataset}/tables", params=params)
            rows.extend(payload.get("tables") or [])
            token = payload.get("nextPageToken")
            if not token:
                return rows

    async def get_table(self, project, dataset, table, *, context):
        return await self._request(
            context, "GET", f"/projects/{project}/datasets/{dataset}/tables/{table}")

    async def dry_run(self, sql, *, context, location=None):
        body = {"jobReference": {"projectId": self.project,
                                 "jobId": uuid.uuid4().hex,
                                 "location": location or self.location},
                "configuration": {"dryRun": True, "query": {
                    "query": sql, "useLegacySql": False}}}
        payload = await self._request(
            context, "POST", f"/projects/{self.project}/jobs", json=body)
        return int(((payload.get("statistics") or {}).get("query") or {})
                   .get("totalBytesProcessed") or 0)

    async def query(self, sql, *, context, location=None):
        job_id = uuid.uuid4().hex
        job_location = location or self.location
        reference = {"projectId": self.project, "jobId": job_id, "location": job_location}
        body = {"jobReference": reference,
                "configuration": {"query": {"query": sql, "useLegacySql": False}}}
        try:
            payload = await self._request(
                context, "POST", f"/projects/{self.project}/jobs", json=body)
            while (payload.get("status") or {}).get("state") != "DONE":
                await context.sleep(0.25)
                payload = await self._request(
                    context, "GET", f"/projects/{self.project}/jobs/{job_id}",
                    params={"location": job_location})
            error = self._error(payload)
            if error:
                raise RuntimeError(f"BigQuery query failed: {error}")
            rows, token, fields = [], None, None
            while True:
                params = {"location": job_location, "maxResults": 10000}
                if token:
                    params["pageToken"] = token
                page = await self._request(
                    context, "GET", f"/projects/{self.project}/queries/{job_id}", params=params)
                error = self._error(page)
                if error:
                    raise RuntimeError(f"BigQuery results failed: {error}")
                fields = fields or (page.get("schema") or {}).get("fields", [])
                for row in page.get("rows") or []:
                    rows.append({field["name"]: self._value(cell.get("v"), field.get("type"))
                                 for field, cell in zip(fields, row.get("f") or [])})
                token = page.get("pageToken")
                if not token:
                    return rows
        except (asyncio.CancelledError, runtime.QueryCancelled):
            await self.cancel(job_id, job_location)
            raise


async def rows_async(sql, *, context):
    client = context.bigquery_client
    if client is None:
        project = os.getenv("GOOGLE_CLOUD_PROJECT")
        if not project or context.http_client is None:
            import driver
            raise driver.CredentialError(
                "async BigQuery needs GOOGLE_CLOUD_PROJECT, GOOGLE_APPLICATION_CREDENTIALS, "
                "and QueryContext.http_client")
        client = AsyncBigQueryClient(project, context.http_client)
        context.bigquery_client = client
    return await client.query(sql, context=context)


_READ_ONLY_SQL = re.compile(r"^\s*(SELECT|WITH)\b", re.I)
_FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|EXPORT|LOAD|CALL|GRANT|REVOKE)\b",
    re.I)


def _json_object(raw):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            raise RuntimeError("SQL planner returned no JSON object")
        return json.loads(match.group(0))


async def answer_dataset_async(fm, question, *, context):
    """Answer one question from a dataset descriptor using its live BigQuery schema.

    Table choice and SQL are generated from publisher metadata at query time. The executor accepts
    one read-only statement, confines table references to the advertised dataset, dry-runs it, and
    refuses work above the configured byte ceiling before creating a billable query job.
    """
    client = context.bigquery_client
    if client is None:
        raise RuntimeError("BigQuery credentials are not configured for this server")
    project = fm.get("project")
    dataset = fm.get("datasetId")
    if not project or not dataset:
        raise RuntimeError("BigQuery descriptor has no project/datasetId")
    tables = await client.list_tables(project, dataset, context=context)
    names = [((table.get("tableReference") or {}).get("tableId")) for table in tables]
    names = [name for name in names if name]
    if not names:
        raise RuntimeError(f"Dataset {project}.{dataset} has no queryable tables")
    selection = _json_object(await llm.chat_async(
        'Return JSON only: {"tables":["table_name", ...]}. Choose at most 3 table names that '
        'can answer the question. Use only names provided; never invent a name.',
        json.dumps({"question": question, "dataset": f"{project}.{dataset}", "tables": names}),
        context=context, json_mode=True, stage="bigquery-table-selection"))
    chosen = [name for name in (selection.get("tables") or []) if name in set(names)][:3]
    if not chosen:
        raise RuntimeError("No table in the selected dataset appears able to answer the question")
    schemas = []
    for name in chosen:
        table = await client.get_table(project, dataset, name, context=context)
        fields = ((table.get("schema") or {}).get("fields") or [])
        schemas.append({"table": name, "description": table.get("description", ""),
                        "fields": [{"name": f.get("name"), "type": f.get("type"),
                                    "mode": f.get("mode"), "description": f.get("description", "")}
                                   for f in fields]})
    planned = _json_object(await llm.chat_async(
        'Return JSON only: {"sql":"..."}. Write one read-only GoogleSQL SELECT that answers the '
        'question from the supplied schemas. Fully qualify every table with backticks. Do not use '
        'DML, DDL, scripting, wildcard tables, INFORMATION_SCHEMA, external functions, or tables '
        'outside the supplied dataset. Return at most 100 rows; add LIMIT 100 unless the query is a '
        'single aggregate row.',
        json.dumps({"question": question, "project": project, "dataset": dataset,
                    "schemas": schemas}), context=context, json_mode=True,
        stage="bigquery-sql-generation"))
    sql = str(planned.get("sql") or "").strip().rstrip(";")
    if not _READ_ONLY_SQL.match(sql) or _FORBIDDEN_SQL.search(sql) or ";" in sql:
        raise RuntimeError("Generated SQL failed the read-only statement policy")
    refs = re.findall(r"`([^`]+)`", sql)
    prefix = f"{project}.{dataset}."
    table_refs = [ref for ref in refs if ref.count(".") >= 2]
    if not table_refs or any(not ref.startswith(prefix) or "*" in ref for ref in table_refs):
        raise RuntimeError("Generated SQL referenced a table outside the selected dataset")
    location = fm.get("location") or client.location
    bytes_processed = await client.dry_run(sql, context=context, location=location)
    ceiling = int(os.getenv("BIGQUERY_MAX_BYTES", str(256 * 1024 * 1024)))
    if bytes_processed > ceiling:
        raise RuntimeError(
            f"Query would process {bytes_processed} bytes, above BIGQUERY_MAX_BYTES={ceiling}")
    rows = await client.query(sql, context=context, location=location)
    return {"dataset": f"{project}.{dataset}", "tables": chosen, "sql": sql,
            "bytes_processed": bytes_processed, "rows": rows[:100], "row_count": len(rows)}


def _label(cfg, row):
    if row.get("name"):
        return str(row["name"])
    eid = row.get("eid")
    if cfg.get("name_via") == "propublica":
        try:
            import nonprofit
            return nonprofit.resolve(str(eid))["name"]
        except Exception:
            return f"EIN {eid}"
    return str(eid)


def _sanity(cfg):
    """Value bounds that de-noise a raw fact table (e.g. drop $10T filing typos)."""
    val = cfg.get("value_field") or cfg["field"]
    clauses = [f"t.{val} IS NOT NULL"]
    if cfg.get("value_max") is not None:
        clauses.append(f"t.{val} < {float(cfg['value_max'])}")
    if cfg.get("value_min") is not None:
        clauses.append(f"t.{val} > {float(cfg['value_min'])}")
    return clauses


def _select(cfg, extra, order, lim, having=None):
    """One SQL SELECT over the population. `extra` = list of extra WHERE clauses.

    LONG tables (a `group_agg` in cfg) collapse many rows per entity to one aggregate value
    before ordering; WIDE tables read the value column directly. Both return (eid, name?, value)."""
    table, ent = cfg["table"], cfg["entity_field"]
    where = "WHERE " + " AND ".join(([cfg["filter"]] if cfg.get("filter") else []) + _sanity(cfg) + extra)
    if cfg.get("group_agg"):
        val = cfg.get("value_field") or cfg["field"]
        sql = (f"SELECT t.{ent} AS eid, {cfg['group_agg']}(t.{val}) AS value FROM `{table}` t "
               f"{where} GROUP BY t.{ent}")
        if having:
            sql += f" HAVING value {having}"
        return sql + f" ORDER BY value {order} LIMIT {lim}"
    field = cfg["field"]
    if cfg.get("name_table"):
        return (f"SELECT t.{ent} AS eid, n.{cfg['name_field']} AS name, t.{field} AS value "
                f"FROM `{table}` t LEFT JOIN `{cfg['name_table']}` n ON t.{ent}=n.{cfg['name_key']} "
                f"{where} ORDER BY t.{field} {order} LIMIT {lim}")
    return (f"SELECT t.{ent} AS eid, t.{field} AS value FROM `{table}` t "
            f"{where} ORDER BY t.{field} {order} LIMIT {lim}")


def rank(cfg, n=10, ascending=False, threshold=None):
    """Top/bottom-N by the field, or those past a threshold — one SQL query over the population."""
    thr = threshold if (threshold and threshold.get("value") is not None) else None
    grouped = bool(cfg.get("group_agg"))
    extra, having = [], None
    if thr:
        clause = f"{_OPS.get(thr.get('op'), '>')} {float(thr['value'])}"
        # A grouped value is only known post-aggregation, so its threshold is a HAVING.
        if grouped:
            having = clause
        else:
            extra.append(f"t.{cfg['field']} {clause}")
    order = "ASC" if ascending else "DESC"
    rows = _rows(_select(cfg, extra, order, 200 if thr else int(n), having=having))
    kind = cfg.get("entity_kind", "id")
    usd = cfg.get("unit") == "USD"
    def disp(v):  # pre-format so the synthesizer quotes a figure rather than a raw float
        return ("${:,.0f}" if usd else "{:,.0f}").format(v) if float(v).is_integer() or usd \
            else ("${:,.2f}" if usd else "{:,.2f}").format(v)
    out = [{"label": _label(cfg, r), "entity": f"{kind}/{r['eid']}",
            "value": float(r["value"]), "value_display": disp(float(r["value"]))}
           for r in rows if r.get("value") is not None]
    res = {"source": cfg.get("source") or f"BigQuery {cfg['table']}",
           "measure": cfg.get("field") or cfg.get("value_field") or "value",
           "complete": True}
    if thr:
        # Pre-format the bound so the synthesizer never mistakes the raw threshold integer for a total.
        op_word = {">": "over", ">=": "at least", "<": "under", "<=": "at most"}.get(thr.get("op"), "over")
        res.update({"threshold_display": f"{op_word} {disp(float(thr['value']))}", "matches": len(out),
                    "ranking": out[:50]})
    else:
        res.update({"ranking": out[:int(n)], "top": out[0] if out else None})
    return res


def aggregate(cfg, agg="count", where=None):
    table = cfg["table"]
    if cfg.get("group_agg"):
        # LONG: count DISTINCT entities (one company files many rows) after the measure filter.
        val = cfg.get("value_field") or cfg["field"]
        clauses = ([cfg["filter"]] if cfg.get("filter") else []) + [f"t.{val} IS NOT NULL"]
        if where:
            clauses.append(where)
        w = " WHERE " + " AND ".join(clauses)
        expr = f"COUNT(DISTINCT t.{cfg['entity_field']})" if agg == "count" else f"{agg.upper()}(t.{val})"
        sql = f"SELECT {expr} AS v FROM `{table}` t{w}"
    else:
        expr = "COUNT(*)" if agg == "count" else f"{agg.upper()}({cfg['field']})"
        sql = f"SELECT {expr} AS v FROM `{table}`" + (f" WHERE {where}" if where else "")
    return {"aggregate": agg, "value": _rows(sql)[0]["v"],
            "source": cfg.get("source") or f"BigQuery {table}"}


async def rank_async(cfg, n=10, ascending=False, threshold=None, *, context):
    thr = threshold if (threshold and threshold.get("value") is not None) else None
    grouped = bool(cfg.get("group_agg"))
    extra, having = [], None
    if thr:
        clause = f"{_OPS.get(thr.get('op'), '>')} {float(thr['value'])}"
        if grouped:
            having = clause
        else:
            extra.append(f"t.{cfg['field']} {clause}")
    order = "ASC" if ascending else "DESC"
    rows = await rows_async(_select(cfg, extra, order, 200 if thr else int(n), having=having),
                            context=context)
    kind, usd = cfg.get("entity_kind", "id"), cfg.get("unit") == "USD"
    def display(value):
        return ("${:,.0f}" if usd else "{:,.0f}").format(value) \
            if float(value).is_integer() or usd else ("${:,.2f}" if usd else "{:,.2f}").format(value)
    ranking = []
    for row in rows:
        if row.get("value") is None:
            continue
        label = str(row.get("name") or row.get("eid"))
        if not row.get("name") and cfg.get("name_via") == "propublica":
            try:
                import nonprofit
                label = (await nonprofit.resolve_async(str(row.get("eid")), context=context))["name"]
            except Exception:
                label = f"EIN {row.get('eid')}"
        ranking.append({"label": label, "entity": f"{kind}/{row['eid']}",
                        "value": float(row["value"]),
                        "value_display": display(float(row["value"]))})
    result = {"source": cfg.get("source") or f"BigQuery {cfg['table']}",
              "measure": cfg.get("field") or cfg.get("value_field") or "value", "complete": True}
    if thr:
        word = {">": "over", ">=": "at least", "<": "under", "<=": "at most"}.get(
            thr.get("op"), "over")
        result.update({"threshold_display": f"{word} {display(float(thr['value']))}",
                       "matches": len(ranking), "ranking": ranking[:50]})
    else:
        result.update({"ranking": ranking[:int(n)], "top": ranking[0] if ranking else None})
    return result


async def aggregate_async(cfg, agg="count", where=None, *, context):
    table = cfg["table"]
    if cfg.get("group_agg"):
        value = cfg.get("value_field") or cfg["field"]
        clauses = ([cfg["filter"]] if cfg.get("filter") else []) + [f"t.{value} IS NOT NULL"]
        if where:
            clauses.append(where)
        expression = (f"COUNT(DISTINCT t.{cfg['entity_field']})" if agg == "count"
                      else f"{agg.upper()}(t.{value})")
        sql = f"SELECT {expression} AS v FROM `{table}` t WHERE " + " AND ".join(clauses)
    else:
        expression = "COUNT(*)" if agg == "count" else f"{agg.upper()}({cfg['field']})"
        sql = f"SELECT {expression} AS v FROM `{table}`" + (f" WHERE {where}" if where else "")
    rows = await rows_async(sql, context=context)
    return {"aggregate": agg, "value": rows[0]["v"],
            "source": cfg.get("source") or f"BigQuery {table}"}
