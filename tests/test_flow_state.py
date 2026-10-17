"""Flow-state normalization against verified response shapes (Velociraptor v0.77.3)."""

import pytest

from conftest import load_fixture
from vr_dfir.velociraptor.flow_state import (
    FlowState,
    normalize_flow,
    precondition_skipped_names,
)

CID, FID = "C.SYNTH0000000042", "F.SYNTHFIX01"


@pytest.mark.parametrize("fixture,state,shape", [
    ("flows_flat_finished.json", FlowState.COMPLETED, "flat_context"),
    ("flows_flat_running.json", FlowState.SCHEDULED, "flat_context"),
    ("flows_flat_waiting.json", FlowState.QUEUED, "flat_context"),
    ("flows_flat_in_progress.json", FlowState.IN_PROGRESS, "flat_context"),
    ("flows_flat_unresponsive.json", FlowState.UNRESPONSIVE, "flat_context"),
    ("flows_flat_error.json", FlowState.FAILED, "flat_context"),
    ("flows_flat_cancelled.json", FlowState.CANCELLED, "flat_context"),
    ("flows_flat_unset.json", FlowState.UNKNOWN, "flat_context"),
    ("flows_empty.json", FlowState.NOT_FOUND, "empty"),
    ("get_flow_function_row.json", FlowState.COMPLETED, "hunt_flow_row"),
    ("get_flow_null.json", FlowState.NOT_FOUND, "empty"),
    ("flow_details_nested.json", FlowState.COMPLETED, "nested_context"),
    ("flow_details_nested_unset.json", FlowState.UNKNOWN, "nested_context"),
    ("hunt_flows_row.json", FlowState.COMPLETED, "hunt_flow_row"),
    ("unrecognized_shape.json", FlowState.UNKNOWN, "unrecognized"),
])
def test_verified_shapes(fixture, state, shape):
    status = normalize_flow(load_fixture(fixture), client_id=CID, flow_id=FID)
    assert status.state == state
    assert status.response_shape == shape


def test_flat_context_is_not_assumed_nested():
    """The prior LTH failure assumed get_flow() returns {'context': ...}. VQL returns the
    flat context; both forms must normalize to the same state."""
    flat = normalize_flow(load_fixture("flows_flat_finished.json"), client_id=CID, flow_id=FID)
    nested = normalize_flow(load_fixture("flow_details_nested.json"), client_id=CID, flow_id=FID)
    assert flat.state == nested.state == FlowState.COMPLETED
    assert flat.total_collected_rows == nested.total_collected_rows == 160
    assert flat.artifacts == ["LTH.SystemBaseline"]


def test_absent_or_unset_state_is_never_running():
    for fx in ("flows_flat_unset.json", "flow_details_nested_unset.json", "unrecognized_shape.json"):
        status = normalize_flow(load_fixture(fx), client_id=CID, flow_id=FID)
        assert status.state not in (FlowState.SCHEDULED, FlowState.IN_PROGRESS)
        assert not status.terminal
        assert status.diagnostic


def test_raw_state_preserved_and_numeric_enums():
    status = normalize_flow([{"session_id": FID, "client_id": CID, "state": 4}], client_id=CID,
                            flow_id=FID)
    assert status.state == FlowState.IN_PROGRESS and status.raw_state == "IN_PROGRESS"
    status = normalize_flow([{"session_id": FID, "state": "finished"}], client_id=CID, flow_id=FID)
    assert status.state == FlowState.COMPLETED and status.raw_state == "FINISHED"


def test_query_stats_per_source():
    status = normalize_flow(load_fixture("flows_flat_error.json"), client_id=CID, flow_id=FID)
    assert status.stat_for("LTH.SystemBaseline", "Services").failed
    assert status.stat_for("LTH.SystemBaseline", "Processes").result_rows == 160
    assert status.stat_for("LTH.SystemBaseline", "Mounts") is None
    assert "permission denied" in status.stat_for("LTH.SystemBaseline", "Services").error_message


def test_unrecognized_diagnostic_lists_keys_not_values():
    status = normalize_flow([{"Result": "secret-value", "Rows": 3}], client_id=CID, flow_id=FID)
    assert "Result" in status.diagnostic and "secret-value" not in status.diagnostic


def test_scope_mismatch_is_rejected():
    ctx = load_fixture("flows_flat_finished.json")
    status = normalize_flow(ctx, client_id="C.SYNTHOTHER", flow_id=FID)
    assert status.state == FlowState.UNKNOWN and "mismatch" in status.diagnostic


def test_multiple_rows_unrecognized():
    rows = load_fixture("flows_flat_finished.json") * 2
    assert normalize_flow(rows, client_id=CID, flow_id=FID).response_shape == "unrecognized"


def test_permission_hint_in_not_found():
    status = normalize_flow([], client_id=CID, flow_id=FID,
                            logs=["flows: Permission denied: READ_RESULTS"])
    assert "permission" in status.diagnostic.lower()


def test_key_material_never_echoed():
    status = normalize_flow([{"session_id": FID, "state": "ERROR",
                              "status": "-----BEGIN RSA PRIVATE KEY----- abc"}], client_id=CID,
                            flow_id=FID)
    assert "PRIVATE KEY" not in status.raw_status


def test_precondition_skip_names():
    logs = ["INFO:LTH.SystemBaseline/DebianPackages: Skipping query due to preconditions",
            "INFO:Collection LTH.SystemBaseline/Processes is done after 1.2s"]
    assert precondition_skipped_names(logs) == {"LTH.SystemBaseline/DebianPackages"}


def test_single_source_completion_log_does_not_complete_flow(vr):
    """Upstream treated 'Collection X is done' for one source as flow completion. The
    normalized state comes only from the flow context."""
    from support.fake_server import FakeFlow
    flow = FakeFlow(client_id="C.SYNTH0000000001", flow_id="F.SYNTHRUN1", artifacts=["LTH.SystemBaseline"],
                    final_state="IN_PROGRESS",
                    logs=[{"message": "INFO:Collection LTH.SystemBaseline/Processes is done after 1s"}])
    vr.backend.flows["OSYNTHLAB"][(flow.client_id, flow.flow_id)] = flow
    status = vr.flow_status(flow.client_id, flow.flow_id)
    assert status.state == FlowState.IN_PROGRESS and not status.terminal
