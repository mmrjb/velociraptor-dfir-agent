# GUI workflow

Open the loopback UI through the SSH tunnel described in INSTALL.md, or use an
administrator-configured [authenticated LAN HTTPS gateway](lan-access.md). The main
destinations are **Investigations**, **Reports** and **Settings**.

## The first minute

1. **Investigations** lists saved investigations with their hosts, organization, when they
   were saved, and their progress ("findings not ready", "3 of 8 reviewed"). Select
   **Continue**, or **Start investigation** to choose an existing collection on a host, an
   existing hunt, or an evidence bundle. Nothing new is collected from endpoints.
2. An investigation opens on **Overview**: a **Next step**, three numbers (Needs review,
   Represented hosts, Sources read), two charts and the findings that need attention. Every
   number and chart segment opens the matching evidence. A low count or no findings is not a
   statement that a host is clean; the coverage line says what was read.
3. **Evidence** shows findings beside one inspector. Select a finding to see what was
   observed, why it needs attention, its evidence rows and the **Review decision**. Select a
   row to open it in the inspector: the important fields first, the reason for attention with
   the matched text marked, then **All fields** and **Advanced** (exact raw JSON, provenance,
   timestamp basis). **Source rows** switches to the saved table of one source (five to seven
   useful columns; the column selector shows the rest).
4. **Changes** answers "What changed?" between this capture and the latest earlier saved
   capture of the same host and source. Only meaningful changes are listed; Unknown means
   absence could not be established (for example the current source failed).
5. **Timeline** shows events by their own recorded time, with rows that have no usable event
   time counted apart. An empty interval is a measured zero only when every in-scope source
   with a mapped event time was read completely; otherwise it is marked as a possible gap.
   Edge intervals are cut to the selected range. **Capture history** shows repeated
   captures by capture time.
6. Pin findings, rows and changes (**Pin to report**), add notes in **Report selection**, and
   build the **Selected evidence** report under Reports. The full investigation report is
   still available.

**Hosts and outliers** opens from the Overview (Represented hosts, or the host chart):
compare hosts and see values seen on only a few eligible hosts, always with the denominator
("Seen on 1 of 4 eligible hosts"). **Where else was this seen?** in the inspector answers the
same question for one row. Low prevalence is a reason to review, not a verdict.

## Behavior to rely on

* Filters, the selected finding or row, the source and the page are in the URL: Back,
  reload and shared links keep them. **Clear filters** never clears pins, notes or review
  decisions. Escape closes the inspector and returns focus to the row.
* Evidence values are untrusted text: never rendered as HTML, copied exactly as saved
  (large integers included), with automatic text direction for Persian and other scripts.
* Loading, failed, incomplete, empty and "not ready" states are shown differently; none of
  them is shown as a zero.
* Times are shown in UTC with the zone written out; Settings can switch the display to the
  browser's local time (presentation only).
* AI explanations appear only when a model is configured, and run only when you select
  an explicit action. Charts, filters, the inspector, comparisons and reports never call a
  model.

This is a local single-user analyst application; keep it on loopback. Remote access uses SSH
or the optional authenticated HTTPS gateway; gateway logins share one workspace.

Browser acceptance cases are in `frontend/e2e/` (start the test-only server with the command
in CONTRIBUTING.md). They use a synthetic backend and fake model; they are not a substitute
for checks against a real server or for usability testing with analysts.

## Current chart types

Composition is shown as a stacked bar with a selectable legend, comparisons as horizontal
bars, events as a time-bucket histogram, and capture history as bars for individual saved
captures. Pie/donut charts, connected line charts and a configurable dashboard builder
are not implemented in this candidate.
