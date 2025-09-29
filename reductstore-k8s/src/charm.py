#!/usr/bin/env python3
# Copyright 2025 anthony
# See LICENSE file for licensing details.

import logging
from typing import cast

import ops
from charms.catalogue_k8s.v1.catalogue import CatalogueConsumer, CatalogueItem
from charms.traefik_k8s.v2.ingress import (
    IngressPerAppReadyEvent,
    IngressPerAppRequirer,
    IngressPerAppRevokedEvent,
)
from ops.framework import StoredState

logger = logging.getLogger(__name__)

VALID_LOG_LEVELS = ["info", "debug", "warning", "error", "critical"]


class ReductstoreCharm(ops.CharmBase):
    """Charm for ReductStore."""

    _stored = StoredState()

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        # Persist last known ingress URL
        self._stored.set_default(ingress_url="")

        # Observe pebble + config
        framework.observe(self.on["reductstore"].pebble_ready, self._on_reductstore_pebble_ready)
        framework.observe(self.on.config_changed, self._on_config_changed)

        # Setup ingress (Traefik)
        self.ingress = IngressPerAppRequirer(self, port=8383, strip_prefix=False)
        self.framework.observe(self.ingress.on.ready, self._on_ingress_ready)
        self.framework.observe(self.ingress.on.revoked, self._on_ingress_revoked)

        # Setup catalogue consumer
        self.catalogue = CatalogueConsumer(charm=self, item=self._catalogue_item)

    def _on_reductstore_pebble_ready(self, event: ops.PebbleReadyEvent):
        container = event.workload
        container.add_layer("reductstore", self._pebble_layer, combine=True)
        container.replan()
        self.unit.status = ops.ActiveStatus()

    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        log_level = cast(str, self.model.config["log-level"]).lower()
        if log_level not in VALID_LOG_LEVELS:
            self.unit.status = ops.BlockedStatus(f"invalid log level: '{log_level}'")
            return

        container = self.unit.get_container("reductstore")
        try:
            container.add_layer("reductstore", self._pebble_layer, combine=True)
            container.replan()
        except ops.pebble.ConnectionError:
            self.unit.status = ops.MaintenanceStatus("waiting for Pebble API")
            event.defer()
            return

        self.unit.status = ops.ActiveStatus()
        self.catalogue.update_item(self._catalogue_item)

    def _on_ingress_ready(self, event: IngressPerAppReadyEvent):
        self._stored.ingress_url = event.url
        self.catalogue.update_item(self._catalogue_item)
        logger.info("Ingress is ready: %s", event.url)
        self.unit.status = ops.ActiveStatus(f"Ingress at {event.url}")

    def _on_ingress_revoked(self, event: IngressPerAppRevokedEvent):
        self._stored.ingress_url = ""
        self.catalogue.update_item(self._catalogue_item)
        logger.warning("Ingress revoked")
        self.unit.status = ops.MaintenanceStatus("Waiting for ingress")

    @property
    def external_url(self) -> str:
        return self._stored.ingress_url or ""

    @property
    def _catalogue_item(self) -> CatalogueItem:
        return CatalogueItem(
            name="ReductStore",
            url=self.external_url,
            icon="database",
            description=(
                "ReductStore is a time series object store for high-frequency unstructured data."
            ),
        )

    @property
    def _pebble_layer(self) -> ops.pebble.LayerDict:
        log_level = cast(str, self.model.config["log-level"])
        return {
            "summary": "ReductStore layer",
            "description": "Pebble config layer for ReductStore",
            "services": {
                "reductstore": {
                    "override": "replace",
                    "summary": "ReductStore server",
                    "command": "reductstore",
                    "startup": "enabled",
                    "environment": {
                        "RS_LOG_LEVEL": str(log_level).upper(),
                        "RS_PORT": "8383",
                        "RS_DATA_PATH": "/data",
                        "RS_LICENSE_PATH": str(self.model.config["license-path"] or ""),
                        "RS_API_BASE_PATH": str(
                            self.model.config["api-base-path"]
                            or f"/{self.model.name}-{self.app.name}"
                        ),
                    },
                }
            },
        }


if __name__ == "__main__":
    ops.main(ReductstoreCharm)
