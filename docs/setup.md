# ReductStore Demo Setup Guide

This document describes how to set up a demo architecture using MicroK8s, Juju, and LXD to simulate robots and deploy the Canonical Observability Stack (COS) Lite for monitoring and logging.


## Install MicroK8s

Install a stricly confined snap version of MicroK8s to avoid issues with Juju:

```bash
sudo snap install microk8s --channel 1.34-strict
```

Add your user to the `microk8s` group to avoid using `sudo` for every command, and get access to the `.kube` caching directory

```bash
sudo usermod -a -G microk8s $USER

mkdir -p ~/.kube
sudo chown -f -R $USER ~/.kube
```

Log out and log back in for the group change to take effect.

```bash
su - $USER
```

Verify that `microk8s` is running:

```bash
microk8s status --wait-ready
```

Enable necessary MicroK8s add-ons (DNS, simple storage, and MetalLB for load balancing):

```bash
sudo microk8s enable dns hostpath-storage
```

## Install Juju

[Juju](https://juju.is/docs/installing) is a tool for deploying and managing applications in Kubernetes and other environments.

```bash
sudo snap install juju --channel 3.6/stable
mkdir -p ~/.local/share
```

Create (bootstrap) a Juju controller using MicroK8s:

```bash
juju bootstrap microk8s rob-cos-controller
```

The `metallb` add-on requires a valid IP address range for load balancing:

```bash
sudo apt update && sudo apt install -y jq
IPADDR=$(ip -4 -j route get 2.2.2.2 | jq -r '.[] | .prefsrc')

sudo microk8s enable metallb:$IPADDR-$IPADDR
```

Create a new Juju model for ROS demo:

```bash
juju add-model cos-robotics-model
juju switch cos-robotics-model
```

## Deploy COS Lite

[COS (Canonical Observability Stack) Lite](https://charmhub.io/topics/canonical-observability-stack/editions/lite) is a lightweight version of the Canonical Observability Stack, which includes Prometheus, Grafana, and Loki for monitoring and logging.

```bash
curl -L https://raw.githubusercontent.com/ubuntu-robotics/rob-cos-overlay/main/robotics-overlay.yaml -O

juju deploy cos-lite --trust --overlay ./config/demo-overlay.yaml
```

Find the IP address of the dashboard:

```bash
juju run traefik/0 show-proxied-endpoints | grep catalogue
```

For example at `http://192.168.178.94/cos-robotics-model-catalogue`

**Note**: to whipe out the demo from MicroK8s, you can run:

```bash
juju switch cos-robotics-model
juju destroy-model cos-robotics-model --destroy-storage --force --no-wait
```

## Get Grafana access

Username is `admin`. Get the password with:

```bash
juju run grafana/0 get-admin-password
```

## Use LXD to simulate robots

[LXD](https://canonical.com/lxd) will be used to simulate multiple robots on a single machine.

[Install LXD](https://documentation.ubuntu.com/lxd/latest/installing/) using snap:

```bash
sudo snap install lxd
```

Allow your user to use LXD:

```bash
getent group lxd | grep -qwF "$USER" || sudo usermod -aG lxd "$USER"
newgrp lxd
```

Initialize LXD with default settings:

```bash
lxd init
```

Configure LXD as follow:

```bash
Would you like to use LXD clustering? (yes/no) [default=no]: no

Do you want to configure a new storage pool? (yes/no) [default=yes]: yes
Name of the new storage pool [default=default]: (press enter)
Name of the storage backend to use (dir, zfs, btrfs, lvm, ceph) [default=zfs]: dir
(→ simplest choice, just stores containers in /var/snap/lxd/...)
Would you like to connect to a MAAS server? (yes/no) [default=no]: no
Would you like to create a new local network bridge? (yes/no) [default=yes]: yes
What should the new bridge be called? [default=lxdbr0]: (press enter)
What IPv4 address should be used? (CIDR subnet notation, “auto” or “none”) [default=auto]: auto
What IPv6 address should be used? (CIDR subnet notation, “auto” or “none”) [default=auto]: auto
Would you like the LXD server to be available over the network? (yes/no) [default=no]: no
Would you like stale cached images to be updated automatically? (yes/no) [default=yes]: yes
Would you like a YAML "lxd init" preseed to be printed? (yes/no) [default=no]: no
```

Check available Ubuntu images:

```bash
lxc image list ubuntu: 24.04 architecture=$(uname -m)
```

Launch a new LXD container with Ubuntu 24.04:

```bash
lxc launch ubuntu:24.04 robot1
```

Check the status of the container:

```bash
lxc list
```

Get an mcap file with some ROS 2 data, for example from [Autonomous Mobile Robot (Treescope)](https://foxglove.dev/examples).

Then push the mcap file into the container:

```bash
 sudo lxc file push example-010-amr.mcap robot1/root/
```

## Install robotics snaps in the robot


Access the container's shell:

```bash
lxc exec robot1 -- /bin/bash
``` 

[Install ROS 2 Jazzy](https://docs.ros.org/en/jazzy/Installation/Alternatives/Ubuntu-Development-Setup.html) (ros-base only, no desktop GUI necessary):

```bash
sudo apt update
sudo apt install -y \
  ros-jazzy-ros-base \
  ros-jazzy-ros2bag \
  ros-jazzy-rosbag2-storage-mcap \
  ros-jazzy-rosbag2-storage-sqlite3
```

Install ReductStore snap:

```bash
sudo snap install reductstore
```

Install `reductstore_agent` (better if snapped, but not available yet) and run it:

```bash
ros2 run reductstore_agent recorder --ros-args --params-file ./config.yaml
```

Run the bag play command in loop mode:

```bash
ros2 bag play example-010-amr.mcap --loop
```

Then you need to install some snaps on in the robot with the following script:

```bash
curl -L https://raw.githubusercontent.com/canonical/rob-cos-device-setup/main/setup-robcos-device.sh -O
sudo bash setup-robcos-device.sh
```

Enter the following URL when prompted: `http://192.168.178.94/cos-robotics-model`.

## References

- [A look into Ubuntu Core 24: Robotics telemetry for your fleet](https://ubuntu.com/blog/ubuntu-core-24-robotics-telemetry)
- [Deploy COS for robotics agent on your robot](https://canonical-robotics.readthedocs-hosted.com/en/latest/tutorials/observability/deploy-cos-for-robotics-agent-on-your-robot/)
- [Robotics reference architecture](https://canonical-robotics.readthedocs-hosted.com/en/latest/references/ref_architecture/reference_architecture/)
- [COS (Canonical Observability Stack) Lite](https://charmhub.io/topics/canonical-observability-stack/editions/lite)
- [Packaging ROS 2 applications with snaps](https://canonical-robotics.readthedocs-hosted.com/en/latest/tutorials/)
- [Prevent connectivity issues with LXD and Docker](https://documentation.ubuntu.com/lxd/latest/howto/network_bridge_firewalld/#prevent-connectivity-issues-with-lxd-and-docker)