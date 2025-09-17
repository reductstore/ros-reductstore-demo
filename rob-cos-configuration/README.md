# COS for Robotics Configuration Snap

This repo provides a **configuration snap** for [COS for Robotics](https://ubuntu.com/robotics/cos) with [ReductStore](https://www.reduct.store/).

It bundles configs for:

* `cos-registration-agent` (`device.yaml`, dashboards, alerts, layouts)
* `rob-cos-grafana-agent` (`grafana-agent.river`)
* `ros2-exporter-agent` (`ros2-data-exporter.yaml`)
* `foxglove-bridge` (`foxglove-bridge.yaml`)

## Usage

```bash
snapcraft pack
sudo snap install ./rob-cos-demo-configuration_0.1_amd64.snap --dangerous
sudo snap connect cos-registration-agent:configuration-read rob-cos-demo-configuration:configuration-read
```

📝 Configuration layout and examples were **taken from Canonical’s [rob-cos-demo-configuration](https://github.com/canonical/rob-cos-demo-configuration)**.
See that repo for more details and advanced usage.
