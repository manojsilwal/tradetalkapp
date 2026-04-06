# FaultHunter triage (single source)

**Status:** placeholder until the first successful run of [`.github/workflows/faulthunter-report-reminder.yml`](../.github/workflows/faulthunter-report-reminder.yml).

When the workflow runs with `reminder_mode: file` (default), this file is **replaced** with a summary of the report at `FAULTHUNTER_REPORT_URL` plus a checklist. Point Cursor at **this file** + [`AGENTS.md`](../AGENTS.md).

**Raw evaluator output** always lives in the FaultHunter repo (e.g. `reports/latest.md`), not here.
