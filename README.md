# ros-reductstore-demo

Stream ROS 2 data from an MCAP file into [ReductStore](https://www.reduct.store)
via [ReductBridge](https://github.com/reductstore/reduct-bridge), replicate a
filtered slice to a cloud ReductStore, and visualize the result in Grafana and
Foxglove — all from a single `docker compose up`.

![Diagram](docs/diagram.drawio.png)

## Data flow

```
  MCAP file  ──►  ros2 bag play  ──►  ROS 2 topics ──►  reduct-bridge
                                                            │
                                                            ▼
                                                  ReductStore edge (robot)
                                                            │
                                           replication tasks (filtered)
                                                            ▼
                                                  ReductStore cloud
                                                            │
                                      ┌─────────────────────┴─────────────────────┐
                                      ▼                                           ▼
                                   Grafana                                    Foxglove
```

See [`docs/architecture.md`](docs/architecture.md) for the full breakdown.

## Quick start

1. **Clone the repo** and copy the env template:

   ```bash
   cp .env.example .env
   ```

2. *(Optional)* Put a ReductStore license at `./licenses/reduct.lic` if
   you want to use Pro replication features. The demo also works in
   read-only / limited mode without one.

3. **Bring everything up**:

   ```bash
   docker compose pull
   docker compose up -d --build
   ```

   First run builds the `robot` image (ROS 2 Jazzy + ReductBridge).

4. **Add these to `/etc/hosts`** so the Traefik-routed URLs resolve:

   ```
   127.0.0.1  edge.reduct.demo cloud.reduct.demo grafana.reduct.demo foxglove.reduct.demo traefik.reduct.demo
   ```

5. **Open**:

   - http://edge.reduct.demo — edge ReductStore UI (live ingest from the bridge)
   - http://cloud.reduct.demo — cloud ReductStore UI (filtered replica)
   - http://grafana.reduct.demo — dashboards, pre-wired to ReductStore Cloud
   - http://foxglove.reduct.demo — Foxglove Studio
   - http://traefik.reduct.demo — Traefik dashboard

## Services

| Service    | Purpose                                              |
| ---------- | ---------------------------------------------------- |
| `robot`    | `ros2 bag play` on the MCAP file + `reduct-bridge`   |
| `edge`     | ReductStore on the robot — bridge writes here        |
| `cloud`    | ReductStore aggregator — replication destination     |
| `grafana`  | Dashboards (ReductStore Cloud datasource)            |
| `foxglove` | Browser-based ROS viewer                             |
| `traefik`  | Reverse proxy                                        |

## Key files

| Path                              | What it is                                      |
| --------------------------------- | ----------------------------------------------- |
| `docker-compose.yml`              | All services, plus the three replication tasks |
| `docker/Dockerfile`               | Builds the combined ROS 2 + ReductBridge image  |
| `config/reduct-bridge.toml`       | ReductBridge topic subscriptions and remote     |
| `data/example-010-amr.mcap`       | Sample MCAP (Foxglove AMR Treescope example)    |
| `grafana/provisioning/`           | Pre-provisioned datasource + dashboard provider |
| `grafana/dashboards/`             | Dashboard JSON files loaded by the provider     |
| `foxglove/layout.json`            | Example Foxglove layout                         |

## Common operations

Update a single service:

```bash
docker compose pull <service>
docker compose up -d <service>
```

Force a clean rebuild of the robot container (after editing the bridge
config or Dockerfile):

```bash
docker compose up -d --build --force-recreate robot
```

Tail bridge + player output:

```bash
docker compose logs -f robot
```

## Next steps

- [`docs/architecture.md`](docs/architecture.md) — data flow and replication rules
- [`docs/cloud-deployment.md`](docs/cloud-deployment.md) — **optional** Kubernetes / COS Lite deployment
- [`docs/charm.md`](docs/charm.md) — building and publishing the ReductStore Juju charm
- [`docs/ros.md`](docs/ros.md) — Jupyter + ROS 2 setup for the example notebook
- [`docs/debug.md`](docs/debug.md) — troubleshooting MicroK8s / Juju (cloud path)

## References

- [ReductStore](https://reduct.store/docs)
- [ReductBridge](https://github.com/reductstore/reduct-bridge)
- [ROS 2](https://docs.ros.org)
- [Foxglove Studio](https://foxglove.dev)
- [Canonical Observability Stack Lite](https://charmhub.io/topics/canonical-observability-stack/editions/lite)
