# Agent instructions

Read docs/architecture.md first. GUI, CLI and MCP must remain adapters over the shared
investigation service. This edition only reads existing backend evidence and performs local
analysis; do not add collection launching, response actions, notebooks or Master Triage.
Use fixed VQL templates and identifier validation, never user/model queries. Synthetic data
belongs in tests only. Keep secrets, real evidence and runtime output out of git. MCP stdout
is JSON-RPC only. Use an executable build directory when /tmp is mounted noexec. Private git
work may be requested by the owner; public publication requires an explicit owner decision.
