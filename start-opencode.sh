#!/usr/bin/env bash
# Runner script to start OpenCode with MuseSpark in Orca ADE
set -euo pipefail

WORKSPACE="/home/orca/workspaces/nextcloud-talk-agent"

if [ "${1:-}" = "batch" ]; then
    shift
    podman exec -w "$WORKSPACE" orca-ade /home/orca/.opencode/bin/opencode run --auto "$@"
elif [ "${1:-}" = "web" ]; then
    podman exec -w "$WORKSPACE" orca-ade /home/orca/.opencode/bin/opencode web --hostname 0.0.0.0 --port 4096
else
    podman exec -it -w "$WORKSPACE" orca-ade /home/orca/.opencode/bin/opencode "$@"
fi
