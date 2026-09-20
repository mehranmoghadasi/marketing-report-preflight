# Check reference

Ten check types ship with Report Preflight. Every one of them is declared in a client's
`preflight.yml`, reads only files you already have, and returns `pass`, `warn` or `fail`.

A run's verdict is the worst status among checks that have not been waived:

| Run status | Verdict | What it means |
|---|---|---|
| `pass` | Clear to send | Nothing to caveat. |
| `warn` | Send with data notes | Usable figures, with caveats the client should see. |
| `fail` | Do not send yet | Something is wrong enough that the numbers mislead. |

There is no weighted score. `report-preflight run --exit-code` returns `1` when the run
status is `fail`, and `0` otherwise.

If a check cannot run — a missing file, a malformed CSV, a metric that is not present —
it is reported as **fail**, never skipped.

---

## `ga4_response_integrity`

Reads GA4's own `ResponseMetaData` from a saved Data API `runReport` response. Field names
are from the
[v1beta reference](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/ResponseMetaData)
(page last updated 2026-09-14).

| Condition (GA4 field) | Default status | Setting |
|---|---|---|
| `dataTruncationReasons[]` present | `fail` | `on_truncation` |
| `emptyReason` present | `fail` | `on_empty` |
| No rows and no totals | `fail` | `on_no_rows` |
| `subjectToThresholding: true` | `warn` | `on_thresholding` |
| `dataLossFromOtherRow: true` | `warn` | `on_other_row` |
| sampling ratio below threshold | `warn` | `on_sampled`, `min_sampling_ratio` (default `0.95`) |

Sampling ratio is Google's own formula, `samplesReadCount / samplingSpaceSize`, taken as
the **minimum** across date ranges so the worst-sampled range decides.

```yaml
- id: ga4-response-complete
  type: ga4_response_integrity
  source: ga4_sessions
  on_truncation: fail
  on_sampled: warn
  min_sampling_ratio: 0.95
```

## `period_completeness`

`missing_days = every calendar day in the period − days present in the source`

`fail` when `len(missing_days) > fail_above` (default 2), `warn` when `> warn_above`
(default 0). Dates in the source that fall **outside** the period are reported separately
and warn on their own — that pattern means the export used the wrong date range.

## `source_freshness`

`lag_days = period_end − max(date in source)`, per source in `sources:`.

`fail` when `lag_days > fail_after_days` (default 3), `warn` when `> warn_after_days`
(default 1). A negative lag (data dated after the period) fails.

## `metric_delta`

`pct_change = (current_total − previous_total) / previous_total × 100`

`fail` at `abs(pct_change) >= fail_pct`, `warn` at `>= warn_pct`. Thresholds can be set
once for the check or per metric. When the previous total is `0` no percentage exists: a
non-zero current total warns as "no baseline".

```yaml
- id: headline-metrics
  type: metric_delta
  source: metrics
  previous_source: prev_metrics
  metrics:
    sessions: { warn_pct: 20, fail_pct: 45 }
    revenue:  { warn_pct: 20, fail_pct: 50 }
```

## `zero_stream`

`fail` when a metric totals exactly `0` this period after being above `0` last period.
`warn` when it is `0` in both. This is the check that catches a call-tracking number or a
form handler that quietly stopped reporting.

## `consent_shift`

```
denial_rate  = denied / (granted + denied)
shift_points = (denial_rate − previous_denial_rate) × 100
```

`fail` at `abs(shift_points) >= fail_points` (default 15), `warn` at `>= warn_points`
(default 5). A consent-banner or Consent Mode change moves this number and silently
reshapes every metric in the report.

## `event_coverage`

Reads `audit_report.json` from
[ga4-event-auditor](https://github.com/mehranmoghadasi/ga4-event-auditor).

| Finding in that file | Default status | Setting |
|---|---|---|
| event status `missing` | `fail` | `on_missing` |
| event status `param_issues` | `warn` | `on_param_issues` |
| event status `below_expected` | `warn` | `on_below_expected` |
| `naming_issues[]` non-empty | `warn` | `on_naming` |
| `health_score` below a floor | `fail` | `min_health_score` (unset by default) |

`required_events:` narrows the check to a subset of planned events.

## `index_coverage`

Reads the JSON report from
[gsc-coverage-monitor](https://github.com/mehranmoghadasi/gsc-coverage-monitor).

`drop_pct = (previous knownUrls − current knownUrls) / previous knownUrls × 100`, per
site; the worst site decides. `fail` at `>= fail_drop_pct` (default 10), `warn` at
`>= warn_drop_pct` (default 5). Regressions in the report are mapped to statuses by
`severity_map` (default `{high: fail, medium: warn}`). `previous_source` is optional —
without it only regressions are evaluated.

## `duplicate_conversions`

```
duplicate_rows = total_rows − distinct(conversion_id)
duplicate_rate = duplicate_rows / total_rows × 100
```

`fail` at `>= fail_rate_pct` (default 5), `warn` at `>= warn_rate_pct` (default 1). The
detail block names the most-repeated ids and the affected conversion actions.

## `config_drift`

Diffs two JSON settings snapshots at a list of `watched_keys` (dot paths). Any watched
value that changed fails by default (`on_change`); a watched key missing from one snapshot
warns (`on_missing`). Use it for attribution model, conversion window, data filters and
anything else that changes what a metric means without changing its name.

---

## Source types

| `type` | Expects |
|---|---|
| `ga4_report_json` | a saved GA4 Data API `runReport` response |
| `metrics_csv` | long format: `date,metric,value` |
| `consent_csv` | `date,granted,denied` |
| `conversions_csv` | `conversion_id,action,date` |
| `ga4audit_json` | `audit_report.json` from ga4-event-auditor |
| `gsc_monitor_json` | the JSON report from gsc-coverage-monitor |
| `json` | any JSON document (settings snapshots) |

Paths may contain `{period}` and `{prev_period}`, so one config covers every month. Dates
are accepted as `YYYY-MM-DD` or GA4's compact `YYYYMMDD`.
