FROM ros:jazzy-ros-base

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ros-jazzy-rosbag2-storage-mcap \
        ros-jazzy-rmw-cyclonedds-cpp \
        curl \
 && rm -rf /var/lib/apt/lists/*

# Download the ROS2-Jazzy build of reduct-bridge (the default Docker image
# only ships the shell/ros1 variant and does not include ros2 support).
RUN curl -fsSL https://github.com/reductstore/reduct-bridge/releases/download/v0.1.2/bridge-ros2-jazzy.x86_64-unknown-linux-gnu.tar.gz \
    | tar -xz -C /usr/local/bin/ \
 && chmod +x /usr/local/bin/reduct-bridge

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
    ROS_DOMAIN_ID=0

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
