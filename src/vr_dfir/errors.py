"""Typed errors shared by the CLI, MCP tools, and the UI API.

Each error carries a short ``code`` and an optional ``next_action`` so every
interface can explain the problem and one useful next step without leaking
stack traces or credentials.
"""

from __future__ import annotations


class DfirError(Exception):
    """Base class for expected, user-facing failures."""

    code = "error"
    next_action = ""

    def __init__(self, message: str, *, next_action: str = "", details: dict | None = None):
        super().__init__(message)
        self.message = message
        if next_action:
            self.next_action = next_action
        self.details = details or {}

    def to_dict(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.next_action:
            out["next_action"] = self.next_action
        if self.details:
            out["details"] = self.details
        return out


class ValidationError(DfirError):
    code = "validation_error"


class ConfigurationError(DfirError):
    code = "configuration_error"
    next_action = "Run 'vr-dfir doctor' to check the configuration."


class ScopeError(DfirError):
    """Org/client/flow/hunt scope is missing, ambiguous, or mismatched."""

    code = "scope_error"


class AmbiguousClientError(ScopeError):
    code = "ambiguous_client"
    next_action = "Choose one of the listed client IDs explicitly."


class NotFoundError(DfirError):
    code = "not_found"


class PolicyError(DfirError):
    """An operator policy (allowlist, dangerous-tool gate) refused the action."""

    code = "policy_refused"


class VelociraptorError(DfirError):
    code = "velociraptor_error"
    next_action = "Check the Velociraptor API connection with 'vr-dfir doctor --probe'."


class VelociraptorUnavailable(VelociraptorError):
    code = "velociraptor_unavailable"


class LaunchOutcomeUnknown(VelociraptorError):
    """A launch request may or may not have created a collection."""

    code = "launch_outcome_unknown"
    next_action = (
        "Do not relaunch. Reconcile with 'vr-dfir resume --checkpoint ...' which checks "
        "the server for a matching flow first."
    )


class UnrecognizedResponse(VelociraptorError):
    code = "unrecognized_response"
    next_action = "Capture a sanitized fixture of this response and report it."


class ModelError(DfirError):
    code = "model_error"
    next_action = (
        "Evidence was saved. Retry analysis after checking the configured model."
    )


class ModelUnavailable(ModelError):
    code = "model_unavailable"


class StaleRequestError(DfirError):
    code = "stale_plan"
    next_action = "Review the plan again and start from the refreshed plan."
