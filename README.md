# ros-reductstore-demo

Demonstrate how [ReductStore](https://www.reduct.store) integrates with the [Canonical Observability Stack (COS) Lite](https://charmhub.io/topics/canonical-observability-stack/editions/lite).

See demo's [setup instructions](docs/setup.md) for installing MicroK8s, Juju, COS Lite, ReductStore, and demo applications.

## Overview

MicroK8s is used to run a local Kubernetes cluster on a single machine (e.g., a laptop or server). Juju is used to deploy and manage applications on the Kubernetes cluster. 

LXD containers simulate multiple robots on the same machine, each running a ROS 2 Foxglove demo application that plays back recorded data.

![Diagram](docs/diagram.drawio.png)

- **Canonical Observability Stack (COS) Lite** – Monitoring and logging with Grafana, Prometheus, Loki, and Alertmanager.
- **ReductStore** – Time series object storage for MCAP files, images, and telemetry.
- **reductstore_agent** – ROS 2 node for recording and uploading data into ReductStore.
- **Foxglove** – Visualization of live and recorded robot data.
- **Jupyter Notebook** – Example queries and analysis with ReductStore's Python SDK.
- **Browsing Dashboard** – Minimal web demo to explore stored data.
- **Registration Server** – Integrates robots into COS catalogue.

## Demo Flow

1. **Robot (LXD container snapshot)** plays back an MCAP file using `ros2 bag play`.
2. **reductstore_agent** records selected topics and uploads MCAPs and telemetry into ReductStore.
3. **ReductStore server** stores the data and can replicate it to a central archive or cloud storage.
4. **COS Registration** exposes robot metrics and logs into the COS dashboard.
5. **Foxglove Studio** connects to visualize ROS topics.
6. **Jupyter Notebook & Browsing Dashboard** show how to query and visualize stored data.

## Goals

- Provide a reproducible demo setup for robotics data pipelines.
- Show how ReductStore fits into an observability stack with COS Lite.
- Demonstrate integrations with MCAP, ROS 2, Foxglove, and Jupyter.
- Enable replication of robot-collected data to a central or cloud environment.

## References

- [ReductStore Documentation](https://reduct.store/docs)
- [Canonical Observability Stack Lite](https://charmhub.io/topics/canonical-observability-stack/editions/lite)
- [ROS 2 Documentation](https://docs.ros.org)
- [Foxglove Studio](https://foxglove.dev)
- [LXD Containers](https://canonical.com/lxd)
