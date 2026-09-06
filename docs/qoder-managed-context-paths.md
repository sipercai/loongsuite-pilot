# Managed Qoder invocation context and intercept directories

Managed workers may keep Qoder sessions and Pilot logs on an object-storage filesystem.
Invocation Context uses atomic hard links, which some such filesystems do not support.
Keep its atomicity and conflict checks: do not silently fall back to overwriting JSON files.

Set `LOONGSUITE_PILOT_INVOCATION_CONTEXT_ROOT` to an absolute directory on a local
filesystem supporting hard links. Both `invocation-context put` and Qoder Stop Hook
must inherit the same value. Records are stored at `<root>/<agent>/<uuid>.json`.
Without the variable, the existing `<pilot-data>/state/invocation-contexts` layout is unchanged.
This override does not move settings, Hook history, checkpoints, or exporter failure queues.
If the local volume is ephemeral, Context does not survive Pod destruction: delayed hooks
in a replacement Pod cannot correlate old UUIDs, while new attempts register fresh UUIDs.
The normal 48-hour TTL and read-only Hook behavior are unchanged.

Qoder CLI/CN preload and collector now both honor `LOONGSUITE_PILOT_DATA_DIR` for
`logs/qodercli-intercept.jsonl` / `logs/qoderclicn-intercept.jsonl`. This allows their
HOME values to differ while sharing token and system-prompt capture files. When the
variable is absent, each retains the standard HOME-based default.

This fixes path selection only; it does not certify that every Qoder SDK version
preserves message UUID or emits all token/system-prompt sources. Validate real model
requests and cloud spans separately from the filesystem and fixture tests.
