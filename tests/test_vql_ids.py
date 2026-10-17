import pytest

from vr_dfir import ids, vql
from vr_dfir.errors import ValidationError


def test_literal_escaping():
    assert vql.literal("a'b") == "'a\\'b'"
    assert vql.literal("x\\'); DROP") == "'x\\\\\\'); DROP'"
    assert vql.literal("line\nbreak") == "'line\\nbreak'"
    assert vql.literal(True) == "TRUE" and vql.literal(3) == "3"
    assert vql.literal(["a", 1]) == "['a', 1]"
    with pytest.raises(ValidationError):
        vql.literal(None)
    with pytest.raises(ValidationError):
        vql.literal(float("nan"))


@pytest.mark.parametrize("bad", ["C.1' OR 1=1", "C.abc def", "", "F.1", "../etc", "C.\nx"])
def test_client_id_rejects_injection(bad):
    with pytest.raises(ValidationError):
        ids.client_id(bad)


def test_ids_accept_valid():
    assert ids.client_id("C.1a2b3c") == "C.1a2b3c"
    assert ids.client_id("server") == "server"
    assert ids.flow_id("F.CRUU3KIE5D73G") == "F.CRUU3KIE5D73G"
    assert ids.flow_id("F.CRUU3KIE5D73G.H") == "F.CRUU3KIE5D73G.H"
    assert ids.hunt_id("H.CRUU3KIE5D73G")
    assert ids.org_id(None) == "root" and ids.org_id("O123") == "O123"
    assert ids.artifact_name("LTH.SystemBaseline")


def test_templates_only_contain_validated_values():
    q = vql.q_source_page("C.1a", "F.2b", "LTH.SystemBaseline", "Processes", 500, 100)
    assert "start_row=500" in q and "count=100" in q
    assert "artifact='LTH.SystemBaseline/Processes'" in q
    with pytest.raises(ValidationError):
        vql.q_source_page("C.1a", "F.2b", "LTH.X'", None, 0, 1)
    with pytest.raises(ValidationError):
        vql.q_source_page("C.1a", "F.2b", "A.B", None, -1, 1)


def test_client_search_is_plain_text():
    q = vql.q_clients_search("web.01 (prod)", 10)
    assert "web\\\\.01 \\\\(prod\\\\)" in q  # regex metacharacters escaped inside the literal




def test_safe_filename():
    a = ids.safe_filename("../../etc/passwd")
    assert "/" not in a and ".." not in a.split("-")[0]
    assert ids.safe_filename("host A") != ids.safe_filename("host_A")
    assert len(ids.safe_filename("x" * 500)) <= 80
