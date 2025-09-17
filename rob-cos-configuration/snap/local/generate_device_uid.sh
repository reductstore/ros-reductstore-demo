#!/usr/bin/bash

machine_id_path="/etc/machine-id"

if [ -f "$machine_id_path" ]; then
    cat "$machine_id_path" | tr -d '\n'
else
    echo "Failed to open $machine_id_path." >&2
    exit 1
fi
