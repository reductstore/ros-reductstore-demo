# Cloud Deployment (Optional Next Step)

> The main demo runs entirely in Docker Compose — see the top-level
> [`README.md`](../README.md). This document describes how the same data
> flow can instead be deployed to a Kubernetes cluster using the
> [Canonical Observability Stack (COS) Lite](https://charmhub.io/topics/canonical-observability-stack/editions/lite),
> Juju charms, and snaps on the robot. It is **optional** and intended
> only for readers interested in a production-style deployment on a
> fleet of real robots.

The configuration files for this path are intentionally **not kept** in
this repo. This document is a narrative reference; concrete snap
configurations live in Canonical's
[`rob-cos-demo-configuration`](https://github.com/canonical/rob-cos-demo-configuration)
repository.

## What gets deployed where

| Side   | Component                          | Packaged as                                    |
| ------ | ---------------------------------- | ---------------------------------------------- |
| Cloud  | ReductStore                        | Juju charm (see [`charm.md`](charm.md))        |
| Cloud  | Grafana, Prometheus, Loki, Traefik | COS Lite charms                                |
| Cloud  | Foxglove Studio                    | `foxglove-studio-k8s` charm                    |
| Cloud  | Device registry                    | `cos-registration-server-k8s` charm            |
| Robot  | ReductBridge (or reductstore_agent)| Snap (`reduct-bridge` / `reductstore-agent`)   |
| Robot  | COS registration + exporters       | `cos-registration-agent`, `ros2-exporter-agent`, `foxglove-bridge`, `rob-cos-grafana-agent` snaps |

## Cloud side: COS Lite + ReductStore on MicroK8s

### 1. Install MicroK8s

```bash
sudo snap install microk8s --channel 1.34-strict
sudo usermod -a -G microk8s $USER
mkdir -p ~/.kube && sudo chown -f -R $USER ~/.kube
# re-login
microk8s status --wait-ready
sudo microk8s enable dns hostpath-storage
```

### 2. Install Juju and bootstrap a controller

```bash
sudo snap install juju --channel 3.6/stable
mkdir -p ~/.local/share
juju bootstrap microk8s rob-cos-controller

sudo apt update && sudo apt install -y jq
IPADDR=$(ip -4 -j route get 2.2.2.2 | jq -r '.[] | .prefsrc')
sudo microk8s enable metallb:$IPADDR-$IPADDR

juju add-model cos-robotics-model
juju switch cos-robotics-model
```

### 3. Deploy COS Lite with a ReductStore overlay

COS Lite can be deployed with an overlay YAML that adds ReductStore,
Foxglove, and a COS registration server. An example overlay is documented
in Canonical's
[rob-cos-demo-configuration](https://github.com/canonical/rob-cos-demo-configuration)
repo; the minimal shape is:

```yaml
applications:
  reductstore:
    charm: reductstore-k8s
    channel: edge
    trust: true
    resources:
      reductstore-image: reduct/store:main
    scale: 1
  foxglove-studio:
    charm: foxglove-studio-k8s
    channel: edge
    scale: 1
  cos-registration-server:
    charm: cos-registration-server-k8s
    channel: edge
    scale: 1
relations:
  - [reductstore:ingress, traefik:ingress]
  - [foxglove-studio:ingress, traefik:ingress]
  - [cos-registration-server:ingress, traefik:ingress]
  - [reductstore:catalogue, catalogue:catalogue]
  - [grafana:grafana-dashboard, cos-registration-server:grafana-dashboard]
```

Deploy it:

```bash
juju deploy cos-lite --trust --overlay ./robotics-overlay.yaml
```

Attach a ReductStore license if you are using Pro features such as
advanced replication filters:

```bash
juju attach-resource reductstore reductstore-license=/path/to/reduct.lic
```

Find the Traefik-proxied URLs:

```bash
juju run traefik/0 show-proxied-endpoints
```

Get the Grafana admin password:

```bash
juju run grafana/0 get-admin-password
```

See [`charm.md`](charm.md) for how the `reductstore-k8s` charm is built
and published to Charmhub.

## Robot side: snaps and COS for Robotics

Each robot runs a small set of Ubuntu Core snaps that together register
the device with the cloud and stream data:

- **`reduct-bridge`** (or the legacy `reductstore-agent`) — subscribes to
  ROS 2 topics and ships records to the cloud ReductStore.
- **`cos-registration-agent`** — registers the device in the
  `cos-registration-server`.
- **`ros2-exporter-agent`** — Prometheus-style metrics from ROS topics.
- **`foxglove-bridge`** — WebSocket bridge for live Foxglove sessions.
- **`rob-cos-grafana-agent`** — system metrics and logs to Loki/Prometheus.
- **`rob-cos-demo-configuration`** — configuration snap that bundles
  `device.yaml`, dashboards, alert rules and Foxglove layouts for the
  other snaps to consume.

A reference configuration snap is published by Canonical:
[`canonical/rob-cos-demo-configuration`](https://github.com/canonical/rob-cos-demo-configuration).
The setup flow on the robot looks like:

```bash
snap install rob-cos-demo-configuration --beta
snap set rob-cos-demo-configuration device-uid=<uid>
snap set rob-cos-demo-configuration rob-cos-base-url=http://<cos-ip>/<model>

snap install cos-registration-agent --beta
snap connect cos-registration-agent:configuration-read \
             rob-cos-demo-configuration:configuration-read

snap install ros2-exporter-agent --beta
snap install foxglove-bridge --channel=jazzy/stable
snap install rob-cos-grafana-agent --beta

# ReductBridge on the robot
snap install reduct-bridge
```

To simulate a fleet locally, [LXD](https://canonical.com/lxd) containers
can stand in for robots — spawn one Ubuntu 24.04 container per "robot",
install the snaps above, and run `ros2 bag play` against an MCAP file.

## References

- [ReductStore documentation](https://reduct.store/docs)
- [ReductBridge](https://github.com/reductstore/reduct-bridge)
- [Canonical Observability Stack Lite](https://charmhub.io/topics/canonical-observability-stack/editions/lite)
- [rob-cos-demo-configuration](https://github.com/canonical/rob-cos-demo-configuration)
- [Deploy COS for robotics agent on your robot](https://canonical-robotics.readthedocs-hosted.com/en/latest/tutorials/observability/deploy-cos-for-robotics-agent-on-your-robot/)
- [Robotics reference architecture](https://canonical-robotics.readthedocs-hosted.com/en/latest/references/ref_architecture/reference_architecture/)
- [Ubuntu Core 24: Robotics telemetry for your fleet](https://ubuntu.com/blog/ubuntu-core-24-robotics-telemetry)
- [LXD](https://canonical.com/lxd)
- [Traefik Charm Operator](https://github.com/canonical/traefik-k8s-operator)
