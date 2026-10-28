"""The preview must enforce scope below its UI, including persisted legacy jobs."""

import asyncio

import pytest

from support.scenarios import C_APP, ORG
from vr_dfir import mcp_server, vql
from vr_dfir.config import load_settings
from vr_dfir.errors import ConfigurationError, PolicyError
from vr_dfir.velociraptor.service import VelociraptorService


def test_configuration_cannot_enable_backend_mutations():
    settings = load_settings({"ENABLE_DANGEROUS_TOOLS": "true",
                              "VR_DFIR_WORKSPACE_CONFIG": "/ignored.yaml"}, load_dotenv=False)
    assert settings.dangerous_tools is False
    assert settings.workspace_config == ""
    assert settings.ui_port == 8766
    assert settings.state_dir.name == "vr-dfir-ai-preview"
    assert not hasattr(VelociraptorService, "launch_collection")
    assert not hasattr(vql, "q_collect_client")


@pytest.mark.parametrize("provider", ["azure", "openai", "unexpected"])
def test_unsupported_provider_is_refused_before_any_request(provider):
    with pytest.raises(ConfigurationError, match="only ollama or none"):
        load_settings({"VELOCIRAPTOR_MODEL_PROVIDER": provider}, load_dotenv=False)


@pytest.mark.parametrize("forgery", ["mode", "launch"])
def test_shared_service_refuses_forged_plan(synth, forgery):
    plan = synth.plan_existing_flow(org_id=ORG, client_id=C_APP, flow_id="F.SYNTH0002")
    if forgery == "mode":
        plan.mode = "new_collection"
    else:
        plan.collections[0].will_launch = True
    before = synth.store.list()
    with pytest.raises(PolicyError, match="existing evidence"):
        synth.create(plan)
    assert synth.store.list() == before
    assert synth.runtime.fake_server.launches == []


@pytest.mark.parametrize("operation", ["execute", "resume", "retry_analysis", "rebuild_report"])
def test_legacy_collection_jobs_are_not_runnable(synth, operation):
    inv = synth.store.create(mode="new_collection", title="Legacy job", scope={},
                             profile=None, options={}, synthetic=True)
    queries = list(synth.runtime.fake_server.queries)
    with pytest.raises(PolicyError, match="mode is unavailable"):
        getattr(synth, operation)(inv.id)
    assert synth.runtime.fake_server.queries == queries
    assert synth.runtime.fake_server.launches == []


def test_mcp_exposes_only_the_reviewed_read_and_local_analysis_tools(settings):
    ctx = mcp_server.ToolContext(settings)
    tools = asyncio.run(mcp_server.build_server(ctx).list_tools())
    assert {tool.name for tool in tools} == {
        "list_orgs", "client_info", "list_clients", "get_collection_results",
        "get_hunt_results_tool", "flow_status", "list_result_sources", "get_result_page",
        "analyze_existing_flow", "analyze_existing_hunt", "get_investigation",
        "list_investigations", "resume_investigation", "retry_analysis", "get_report",
        "get_evidence",
        # 0.5.0a1: read-only summary views over saved evidence.
        "get_overview", "compare_captures", "where_else_seen",
    }
