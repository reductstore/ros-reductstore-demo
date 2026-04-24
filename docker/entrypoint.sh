#!/usr/bin/env bash
set -eo pipefail

# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash

set -u

CONFIG=${BRIDGE_CONFIG:-/config/reduct-bridge.toml}
MCAP_FILE=${MCAP_FILE:-/data/example-010-amr.mcap}

echo "[robot] playing $MCAP_FILE in loop"
ros2 bag play "$MCAP_FILE" --loop &
PLAYER_PID=$!

# Give ros2 bag play a moment to advertise topic types in the DDS domain
# before reduct-bridge tries to discover them.
sleep 5

echo "[robot] starting reduct-bridge with $CONFIG"
reduct-bridge "$CONFIG" &
BRIDGE_PID=$!

# Exit if either process dies so docker can restart us.
wait -n "$BRIDGE_PID" "$PLAYER_PID"
kill "$BRIDGE_PID" "$PLAYER_PID" 2>/dev/null || true
exit 1
