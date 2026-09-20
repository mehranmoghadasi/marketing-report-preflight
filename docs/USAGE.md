# Usage

## Install

```bash
git clone https://github.com/mehranmoghadasi/marketing-report-preflight.git
cd marketing-report-preflight
pip install -e ".[dev]"
report-preflight --version
```

Python 3.10 or newer. Runtime dependencies: PyYAML, FastAPI, uvicorn.

## Set up a client

```
clients/
  acme-dental/
    preflight.yml
    data/2026-08/ga4-sessions.json
    data/2026-08/metrics.csv
    ...
```

Copy `examples/clients/northgate-plumbing/preflight.yml` and edit it. `{period}` and
`{prev_period}` in a source path are substituted at run time, so the config is written once
and works every month. Check settings are documented in [CHECKS.md](CHECKS.md).

Validate a config without data:

```bash
report-preflight clients --clients-dir clients      # loads and validates every config
report-preflight checks                              # every available check type
```

## Run the checks

```bash
report-preflight run --client acme-dental --period 2026-08 \
  --clients-dir clients --out reports/acme-dental/2026-08
```

Writes three files into `--out`:

| File | Use |
|---|---|
| `preflight.json` | machine-readable result, for your own automation |
| `preflight.html` | standalone readiness page — email it, archive it, print it |
| `data-notes.md` | the client-facing caveat block to paste into the report |

Other flags: `--format json` for stdout JSON, `--no-db` to skip history, `--exit-code` for
gating, `--no-color` for plain output.

## Use it as a gate

```bash
report-preflight run --client acme-dental --period 2026-08 --exit-code \
  && agency-report-builder --client acme-dental --period 2026-08
```

Exit codes: `0` clear (pass or warn), `1` a check failed, `2` bad usage or invalid config.

A monthly cron entry that stops before the send step:

```cron
0 7 1 * * cd /srv/preflight && report-preflight run --client acme-dental \
  --period $(date -d "last month" +\%Y-\%m) --exit-code --out /srv/reports || \
  mail -s "Preflight blocked the Acme report" you@yourdomain.com < /srv/reports/data-notes.md
```

## Accept a failing check

When you decide to send anyway, record the decision rather than editing the threshold:

```bash
report-preflight waive --client acme-dental --check period-complete \
  --reason "Analytics outage on 29-31 Aug, client informed by email" \
  --by "Mehran" --expires 2026-09-30
```

The check still reports `fail`, but it no longer blocks, and the reason is quoted in the
client data notes. Without `--expires` the waiver has no end date.

## Dashboard

```bash
report-preflight serve --clients-dir clients --port 8000
```

Then open <http://127.0.0.1:8000>. The dashboard binds to `127.0.0.1` by default and has no
authentication — put it behind a reverse proxy or an SSH tunnel if you expose it.

| Path | Method | Returns |
|---|---|---|
| `/api/health` | GET | version, clients directory, database path |
| `/api/clients` | GET | configured clients with run counts |
| `/api/clients/{slug}` | GET | sources, checks with descriptions, active waivers |
| `/api/clients/{slug}/runs` | GET | run history, newest first |
| `/api/runs` | POST | run the checks: `{"client": "...", "period": "2026-08"}` |
| `/api/runs/{id}` | GET | a stored run with its checks and notes |
| `/api/runs/{id}/notes.md` | GET | the data-notes block as plain text |
| `/api/clients/{slug}/waivers` | POST | record a waiver |

## History

```bash
report-preflight history --client acme-dental --limit 12
```

```
  id  client               period   status checks  fail  warn
   2  acme-dental          2026-08  fail       10     3     5
   1  acme-dental          2026-07  pass       10     0     0
```

## Try it with no data of your own

```bash
bash examples/demo.sh
```

Runs the synthetic `northgate-plumbing` client for August 2026 and rewrites the artefacts in
`examples/output`. Regenerate the sample data with `python examples/make_example_data.py`.
