# Presentation, derived data and comparison rules

This page documents how saved evidence is presented, counted and compared in 0.5.0a1. The
saved bundle (`manifest.json` + `sources/*.jsonl`) remains the only source of truth; everything
below is derived, disposable or analyst metadata kept beside it.

## Renderer registry

A source is mapped to a *domain* by its identity, never by a column name or a sample value:

1. an operator mapping from `VR_DFIR_SOURCE_MAPPINGS` (exact artifact and source);
2. a documented Velociraptor artifact (`Linux.Sys.Pslist`, `Linux.Sys.Users`,
   `Linux.Sys.Groups`, `Linux.Sys.Crontab`, `Linux.Sys.Services`, `Linux.Network.Netstat`,
   `Linux.Debian.Packages`, `Linux.RHEL.Packages`, `Linux.Ssh.AuthorizedKeys`,
   `Linux.Sys.SUID`, `Linux.Search.FileFinder`, `Generic.Client.Info/BasicInformation`, and
   their Windows equivalents where listed in `evidence/presentation.py`);
3. the exact source name of a composite baseline artifact (`Processes`, `Users`,
   `NetworkConnections`, `Crontab`, `Services`, `DebianPackages`, `RHELPackages`, `Mounts`,
   `Groups`, `AuthorizedKeys`, `SshdConfig`, `Client_BasicInformation`, `Client_LinuxInfo`);
4. otherwise the **generic** renderer, which only distinguishes JSON value types and shows the
   first six columns.

Inside a domain each role (for example a process executable or a remote port) is resolved
from a documented list of candidate column names; the most common resolution of the first 200
rows of a source defines its table columns. Nested `Laddr.IP`-style fields are supported.

### Operator mappings

```json
{"mappings": [
  {"artifact": "Custom.Example", "source": "Inventory", "domain": "file",
   "roles": {"owner": "Owner", "hash": ["SHA256"]},
   "event_time": {"field": "Seen", "format": "epoch_s", "meaning": "first seen"}}
]}
```

`domain` is one of `process`, `network`, `user`, `group`, `service`, `cron`, `package`,
`mount`, `ssh_key`, `config`, `file`, `system`. `format` is `iso8601`, `epoch_s`, `epoch_ms`,
`epoch_us` or `epoch_ns`; `assume_utc: true` is allowed only for zone-less ISO text that is
known to be UTC. Changing the file makes the derived indexes stale; they rebuild on demand.

## Event and capture time

* **Event time** comes only from an explicit mapping: process start time, file modification
  time, or an operator mapping. Text must carry a zone (`Z` or `+hh:mm`); numbers need a
  declared unit and are never reinterpreted by magnitude. Values outside 1980-2100 or with an
  invalid offset are excluded. Rows without a usable event time are counted by reason
  (`unmapped`, `missing`, `naive`, `invalid`, `out_of_range`).
* **Capture time** is the flow's collection time. It is never used as event time.
* The `event_time` stored at retrieval (from the profile's field list, with a magnitude
  heuristic for numbers) is shown in Advanced only.
* Displayed times are UTC by default; a local display zone changes presentation only.

## Derived index (`<investigation>/derived/index-v1.sqlite`)

Built by streaming the saved rows once (bounded memory) with the standard-library `sqlite3`
module. It holds row locations, titles, event times and their status, entity keys, process
identifiers, findings and the rows they cite, and per-source verification (rows on disk versus
the manifest, row sequence, content hashes). Its fingerprint covers the manifest, each evidence
file's size and modification time, the report, the normalizer version and the operator
mapping file. A stale index is rebuilt into a temporary file and atomically replaced; builds
can be cancelled and never touch evidence, reviews, pins or notes. A cancelled or failed build
is not restarted by polling views; the view offers "Build the index again" (or changed
evidence starts a new build). Deleting `derived/` is safe.

Entity keys (paths, commands, services, accounts) are the whole normalized value when it fits
in 1000 characters; a longer value keeps a readable prefix plus the SHA-256 of the whole
value, so two long values never merge.

## Counting units

| Unit | Rule |
|---|---|
| Findings | Unique finding IDs; a finding citing several rows counts once |
| Evidence rows | Saved rows in the declared scope (whole dataset, not a page) |
| Hosts | Clients with saved evidence in the scope |
| Sources read | Read completely (rows, empty or precondition-skipped); failed, partial, unread and unverified sources are listed separately |
| Eligible hosts | One observation per host whose complete, verified source carries the field |

## Comparisons (`derived/comparisons-v1.sqlite`)

A capture is one saved source of one flow. Two captures are compatible when the organization
is known and equal, the client and the artifact/source are identical, the data mode
(synthetic or live) matches, and the baseline is strictly older. Policy `compare-2026.10-1`:

| Adapter | Key | Compared | Ignored as volatile |
|---|---|---|---|
| users | account | UID, GID, home, shell, description | - |
| groups | group | GID, members (as a set) | - |
| services | unit | load, active, sub, description, command, account, start mode | - |
| listeners | protocol, local address, local port (listening rows only) | process name | PID, remote endpoint, state |
| cron | owner, file, command | minute, hour, day of month, month, day of week, event | - |
| packages | name, architecture | version, release, status | - |
| mounts | mount point | device, type, options (as a set) | - |
| ssh_keys | owner, key material | key type, comment, options, file | - |
| settings | file, setting | value | - |
| files | canonical path | size, mode, owner, group, hash, modified, changed | accessed |

Normalization removes only representation differences (row order, number-as-text, protocol
case, IPv6 spelling, whitespace in commands, member and option order). Fields outside the
compared set are ignored; entries differing only there are Unchanged and can be shown.
A field present in only one capture is a schema difference and is excluded, not reported as
a modification. Duplicate keys with different values are Unknown. The entity key is the
SHA-256 of the whole normalized key, so long keys never collide.

Removed requires the current capture to be read completely with rows and verified; Added
requires the same of the baseline, and in both cases every row of that side must carry the
key fields (an unkeyed row could be the missing entity). Otherwise the entity is **Unknown**
with the reason (for example "the source failed on the client" or "zero rows, and empty
cannot be told from skipped"). Results keep both evidence IDs; pins of changes keep the exact pair and policy.

## Cohorts and Where else seen

Scope `investigation` uses the hosts of the open investigation. Scope `organization` adds
saved investigations of the same, known organization and data mode, choosing one observation
per host and source (the latest capture inside a 30-day window ending at this investigation's
newest capture). A host is eligible for an entity kind only with a complete, verified source
that carries the field; hosts of another OS family are not compared; an investigation whose
evidence mixes organizations forms no cohort. Outliers are listed only with at least three
eligible hosts and are values seen on at most 20% (minimum one) of them.

## Network labels

Address *ranges* only: loopback, unspecified, link-local, documentation (RFC 5737/3849),
private (RFC 1918), shared (RFC 6598), unique local, multicast, public, and operator ranges in
`VR_DFIR_INTERNAL_NETWORKS`. No lookup, enrichment or reputation is involved.

## Internal Notebook-cell adapter contract (not part of this edition)

The internal edition's Notebook view can reuse the same presentation services. This public
preview ships no Notebook routes or adapter. A future adapter in the internal edition should
pass, per saved cell result, without executing or recalculating anything:

| Input | Meaning |
|---|---|
| `cell_id`, `version`, `title` or analyst question | Cell identity and heading |
| `description` | Checked behavior or source (plain text) |
| `artifact` / `source` or `table_columns` | Source identity for `mapping_for()`; generic otherwise |
| `rows` (bounded page) and `total_rows` | Saved result rows, paged |
| `calculated_at`, `state`, `error` | Freshness and a concise error state |
| `read_state` | Evidence/read state of the cell |

and would receive the same `Mapping`, `column_plan()`, `title()`, `event_time()` and
inspector payload shape as `services/views.py`. Display modes (Table, Summary, Chart) apply
only when the cell's output supports them; expanding a cell or changing its mode must never
execute VQL or launch a collection.
