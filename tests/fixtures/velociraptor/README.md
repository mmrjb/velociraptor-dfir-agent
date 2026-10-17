# Velociraptor response fixtures

These fixtures are **synthetic** and contain no data from any real server. Their shapes
follow the Velociraptor v0.77.3 source (tag `v0.77.3`, commit `7d287b0b`), inspected during
implementation:

| Fixture | Verified source of the shape |
|---|---|
| `flows_flat_*.json` | `vql/server/flows/flows.go` (`flows()`), `json/protobuf.go`, `ArtifactCollectorContext` in `flows/proto/artifact_collector.proto` |
| `get_flow_*.json` | `get_flow()` in `vql/server/flows/flows.go` (returns the flat context or NULL) |
| `flow_details_nested*.json` | `FlowDetails` in `api/proto/flows.proto`; protojson omits zero values (`EmitUnpopulated: false` in `json/proto.go`) |
| `hunt_flows_row.json` | `hunt_flows()` in `vql/server/hunts/hunts.go` |
| `flows_flat_cancelled.json` | `CancelFlow()` in `services/launcher/flows.go` (state `ERROR`, status `Cancelled by ...`) |
| `unrecognized_shape.json` | deliberately unsupported shape for diagnostic tests |

No live-server capture was available in this build environment. When a sanitized capture
from the deployment server becomes available, add it here with its server version and keep
the test that loads it.
