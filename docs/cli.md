# CLI

Use `<preview>/venv/bin/vr-dfir` and add `--env-file <preview>/preview.env` to commands.
`--help` is available at each level; `--json` prints one JSON result, with progress on stderr.

```bash
vr-dfir doctor --probe --probe-model
vr-dfir orgs list
vr-dfir clients search web --org-id root
vr-dfir flows list --client-id C.EXAMPLE01 --org-id root
vr-dfir hunts list --org-id root
vr-dfir hunts show H.EXAMPLE01 --org-id root
vr-dfir analyze flow --client-id C.EXAMPLE01 --flow-id F.EXAMPLE01 --org-id root
vr-dfir analyze hunt --hunt-id H.EXAMPLE01 --org-id root --max-clients 50 --no-llm
vr-dfir analyze bundle --input /absolute/path/evidence --no-llm
vr-dfir investigations list
vr-dfir investigations show inv-20260101-000000-abc123
vr-dfir findings list inv-20260101-000000-abc123
vr-dfir evidence sources inv-20260101-000000-abc123
vr-dfir report --id inv-20260101-000000-abc123 --format html --output /private/report.html
vr-dfir overview inv-20260101-000000-abc123
vr-dfir timeline inv-20260101-000000-abc123
vr-dfir changes sources inv-20260101-000000-abc123
vr-dfir changes compare inv-20260101-000000-abc123 --source 'ORG/CLIENT/FLOW/Linux.Sys.Users/'
vr-dfir hosts inv-20260101-000000-abc123 --scope organization
vr-dfir where-else inv-20260101-000000-abc123 ev-0123456789abcdef0123
vr-dfir pins add inv-20260101-000000-abc123 --kind finding --ref R-001 --note "checked"
vr-dfir report --id inv-20260101-000000-abc123 --scope selected --format html --output sel.html
```

`overview`, `timeline`, `changes`, `hosts` and `where-else` read saved evidence through the
same services as the UI (building the derived index on first use). `pins` edits the report
selection; `report --scope selected` builds the Selected evidence report.

Use actual IDs from your environment. `resume --id <ID>` continues local retrieval;
`retry-analysis --id <ID>` repeats analysis over saved evidence. Neither starts collection.
Use `findings review --help` and `findings explain --help` for local review state and cited
AI interpretation. Explanation is a separate, user-triggered model action.

Exit codes: 0 success; 1 failure; 2 argument error; 3 partial/resumable result; 4 configuration;
5 policy/stale request refusal; 130 interruption. Check coverage and errors on partial runs.
