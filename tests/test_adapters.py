"""CLI and MCP adapters reach the same shared view, comparison, cohort and selection services."""

import json

import pytest

from vr_dfir import mcp_server as m
from vr_dfir.cli import EXIT_OK, main
from vr_dfir.evidence.bundle import EvidenceBundle


@pytest.fixture
def state(tmp_path, monkeypatch, no_sleep):
    """Two saved captures of one synthetic host, created through the shared service."""
    from support.synthetic import scenario_service, start_scenario
    svc = scenario_service(tmp_path / "state")
    ids = {}
    for name in ("review", "review_later"):
        inv = start_scenario(svc, name, no_llm=True)
        svc.execute(inv.id)
        ids[name] = inv.id
    monkeypatch.setenv("VR_DFIR_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    return svc, ids


def cli(capsys, *args):
    code = main(list(args))
    out = capsys.readouterr().out
    return code, out


def test_cli_views_changes_pins_and_selected_report(state, capsys):
    svc, ids = state
    code, out = cli(capsys, "overview", ids["review"], "--json")
    assert code == EXIT_OK
    ov = json.loads(out)
    assert [x["id"] for x in ov["metrics"]] == ["needs_review", "hosts", "sources"]
    later = svc.store.get(ids["review_later"])
    users = next(e.source_key for e in EvidenceBundle.open(later.evidence_dir).sources()
                 if e.source == "Users")
    code, out = cli(capsys, "changes", "compare", ids["review_later"], "--source", users)
    assert code == EXIT_OK and "added 1, removed 1, modified 1" in out
    code, out = cli(capsys, "pins", "add", ids["review"], "--kind", "finding", "--ref", "R-001",
                    "--note", "checked")
    assert code == EXIT_OK and "Pinned pin-" in out
    code, out = cli(capsys, "pins", "list", ids["review"])
    assert "R-001" in out and "checked" in out
    code, out = cli(capsys, "report", "--id", ids["review"], "--scope", "selected", "--format",
                    "md", "--output", "-")
    assert code == EXIT_OK and "Selected evidence" in out and "checked" in out
    code, out = cli(capsys, "timeline", ids["review"])
    assert code == EXIT_OK and "160 timed rows" in out
    code, out = cli(capsys, "hosts", ids["review"])
    assert code == EXIT_OK and "1 host(s)" in out


def test_mcp_tools_match_the_services(state, tmp_path):
    from support.runtime import fake_runtime
    from vr_dfir.config import load_settings
    from vr_dfir.services import views
    from vr_dfir.services.derived import IndexManager
    svc, ids = state
    settings = load_settings({"VR_DFIR_STATE_DIR": str(tmp_path / "state")}, load_dotenv=False)
    ctx = m.ToolContext(settings, fake_runtime(settings))
    ov = json.loads(m.t_get_overview(ctx, ids["review"]))
    assert ov["ok"]
    inv = svc.store.get(ids["review"])
    direct = views.overview(inv, IndexManager(settings).require(inv))
    assert ov["data"]["metrics"] == direct["metrics"]
    src = next(s for s in IndexManager(settings).require(
        svc.store.get(ids["review_later"])).sources() if s["full_name"].endswith("/Services"))
    cmp_ = json.loads(m.t_compare_captures(ctx, ids["review_later"], src["source_key"]))
    assert cmp_["ok"] and cmp_["data"]["summary"]["counts"]["added"] == 1
    rows = views.query_rows(inv, IndexManager(settings).require(inv),
                            views.RowQuery(q="kworkerd -o"), limit=1)
    we = json.loads(m.t_where_else_seen(ctx, ids["review"], rows["records"][0]["evidence_id"]))
    assert we["ok"] and we["data"]["entities"]
    assert "where_else_seen" in m.NEW_TOOLS and len(m.LEGACY_TOOLS + m.NEW_TOOLS) == 19
