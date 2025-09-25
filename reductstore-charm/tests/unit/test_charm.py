# Copyright 2025 anthony
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import ops
import ops.pebble
from ops import testing

from charm import ReductstoreCharm


def test_reductstore_pebble_ready():
    ctx = testing.Context(ReductstoreCharm)
    container = testing.Container("reductstore", can_connect=True)
    
    state_in = testing.State(containers={container})

    state_out = ctx.run(ctx.on.pebble_ready(container), state_in)
    model_name = state_out.model.name
    app_name = "reductstore-k8s"
    updated_plan = state_out.get_container(container.name).plan
    expected_plan = {
        "services": {
            "reductstore": {
                "override": "replace",
                "summary": "ReductStore server",
                "command": "reductstore",
                "startup": "enabled",
                "environment": {
                    "RS_LOG_LEVEL": "INFO",
                    "RS_PORT": "8383",
                    "RS_DATA_PATH": "/data",
                    "RS_LICENSE_PATH": "/reduct.lic",
                    "RS_API_BASE_PATH": f"/{model_name}-{app_name}",
                },
            }
        },
    }

    assert expected_plan == updated_plan
    assert (
        state_out.get_container(container.name).service_statuses["reductstore"]
        == ops.pebble.ServiceStatus.ACTIVE
    )
    assert state_out.unit_status == testing.ActiveStatus()


def test_config_changed_valid_can_connect():
    ctx = testing.Context(ReductstoreCharm)
    container = testing.Container("reductstore", can_connect=True)
    state_in = testing.State(
        containers={container},
        config={"log-level": "debug", "license-path": "/custom.lic", "api-base-path": "/newbase"}
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    updated_plan = state_out.get_container(container.name).plan
    assert updated_plan.services["reductstore"].command == "reductstore"
    assert updated_plan.services["reductstore"].environment == {
        "RS_LOG_LEVEL": "DEBUG",
        "RS_PORT": "8383",
        "RS_DATA_PATH": "/data",
        "RS_LICENSE_PATH": "/custom.lic",
        "RS_API_BASE_PATH": "/newbase"
    }
    assert state_out.unit_status == testing.ActiveStatus()


def test_config_changed_valid_cannot_connect():
    ctx = testing.Context(ReductstoreCharm)
    container = testing.Container("reductstore", can_connect=False)
    state_in = testing.State(
        containers={container}, config={"log-level": "debug", "license-path": "/x.lic"}
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert isinstance(state_out.unit_status, testing.MaintenanceStatus)


def test_config_changed_invalid():
    ctx = testing.Context(ReductstoreCharm)
    container = testing.Container("reductstore", can_connect=True)
    invalid_level = "foobar"
    state_in = testing.State(
        containers={container}, config={"log-level": invalid_level, "license-path": "/x.lic"}
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert isinstance(state_out.unit_status, testing.BlockedStatus)
    assert invalid_level in state_out.unit_status.message
