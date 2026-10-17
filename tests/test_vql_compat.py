"""Read-only VQL compatibility and distinction between diagnostics and evidence."""
import pytest

from vr_dfir import vql
from vr_dfir.errors import ValidationError
from vr_dfir.velociraptor.backend import QueryResult, log_line_level


def test_hunt_listing_template_has_no_summary_argument_and_keeps_fields_and_limits():
    q = vql.q_hunts_list(7)
    assert "FROM hunts() " in q and "summary" not in q
    assert q.startswith("SELECT hunt_id, hunt_description, create_time, start_time, state, "
                        "artifacts, stats, creator FROM")
    assert q.endswith("ORDER BY create_time DESC LIMIT 7")
    for bad in (0, 501):
        with pytest.raises(ValidationError):
            vql.q_hunts_list(bad)

def test_log_classification_separates_errors_from_info_and_progress():
    assert log_line_level("INFO:Query Stats: {}") == "info"
    assert log_line_level("DEBUG:Query Stats: {\"RowsScanned\":1}") == "info"
    assert log_line_level("Starting query execution.") == "info"
    assert log_line_level("Time 0: vr-dfir: Sending response part 0 3 kB (5 rows).") == "info"
    assert log_line_level("WARN:slow") == "warning"
    assert log_line_level("hunts: Unexpected arg summary while parsing args") == "error"
    assert log_line_level("ERROR:Symbol parse_json_array not found") == "error"
    result = QueryResult(rows=[{"Evidence1": "ERROR: this is evidence text"}],
                         logs=["Starting query execution.", "DEBUG:Query Stats: {}"])
    assert result.server_errors() == []

