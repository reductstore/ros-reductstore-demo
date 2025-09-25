#!/usr/bin/bash

echo "Setting up cos for robotics snaps.."

# snap install rob-cos-demo-configuration --beta
snap install /root/rob-cos-demo-configuration*.snap --dangerous
echo "Installed configuration snap"

echo "Please enter the device-uid:"
read uid
snap set rob-cos-demo-configuration device-uid=$uid

echo "Please enter the rob-cos-base-url (i.e: http://192.168.178.94/cos-robotics-model):"
read url
snap set rob-cos-demo-configuration rob-cos-base-url=$url

snap install rob-cos-data-sharing --beta
echo "Installed rob-cos-data sharing snap"

# Installing this snap to register the device with rob-cos
snap install cos-registration-agent --beta

# Connecting the configuration-read interface to read the device-uid from the configuration snap
sudo snap connect cos-registration-agent:configuration-read rob-cos-demo-configuration:configuration-read

echo "Installed cos-registration agent snap"

snap install ros2-exporter-agent --beta
echo "Installed ros2-exporter-agent snap"

snap install foxglove-bridge --channel=jazzy/stable
echo "Installed foxglove-bridge snap"

snap install rob-cos-grafana-agent --beta
# Connecting all the interfaces to read logs which are not autoconnect
snap connect rob-cos-grafana-agent:hardware-observe
snap connect rob-cos-grafana-agent:log-observe
snap connect rob-cos-grafana-agent:mount-observe
snap connect rob-cos-grafana-agent:network-observe
snap connect rob-cos-grafana-agent:proc-sys-kernel-random
snap connect rob-cos-grafana-agent:system-observe
snap connect rob-cos-grafana-agent:time-control
echo "Installed grafana-agent snap"

echo "Device setup with cos for robotics"
