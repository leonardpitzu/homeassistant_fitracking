"""Regression tests for the config and options flows."""

from custom_components.fitracking.config_flow import ConfigFlow, OptionsFlowHandler


def test_options_flow_does_not_override_init():
    """OptionsFlow.config_entry is read-only from HA 2024.11 (upstream issue #113)."""
    assert "__init__" not in vars(OptionsFlowHandler)


def test_reauth_is_implemented():
    """The coordinator raises ConfigEntryAuthFailed, which starts a reauth flow.

    Without these steps Home Assistant would fail that flow with UnknownStep.
    """
    assert hasattr(ConfigFlow, "async_step_reauth")
    assert hasattr(ConfigFlow, "async_step_reauth_confirm")
