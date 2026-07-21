#!/usr/bin/env bash
# Render deploy/mediamtx.yml with the Dahua camera password, then exec MediaMTX
# on the rendered config (plans/cameras-plan.md).
#
# Why a wrapper: MediaMTX v1.19.2 does NOT interpolate ${VAR} inside config
# values — a ${...} left in a source URL reaches Go's url.Parse literally and
# crash-loops the service with "'rtsp://...' is not a valid URL". So the yaml
# ships as a template with a ${GPS_DAHUA_PASSWORD_URLENC} placeholder and this
# wrapper does the substitution the hub won't, at startup.
#
# GPS_DAHUA_PASSWORD_URLENC is the URL-percent-encoded twin of the raw camera
# password (the password has a char illegal in a URL's userinfo). Both live in
# the root-600 /etc/default/gps-dahua, injected via the unit's EnvironmentFile;
# the raw GPS_DAHUA_PASSWORD stays for the dahua_reader's digest auth. Absent
# secret => empty substitution => a valid (auth-less) URL, so the service still
# starts dormant, matching the enabled-gated model.
#
# The rendered config carries the plaintext password, so it is written 0600 into
# the unit's tmpfs RuntimeDirectory (/run/mediamtx) — never persistent disk, and
# removed by systemd on stop.
#
# NOTE: the deploy hook restarts mediamtx only when its unit or mediamtx.yml
# changes, not this script — bundle any edit here with a unit/yaml touch (or
# restart manually) to redeploy it.
set -euo pipefail

TEMPLATE=/mnt/nvme/gps-dashboard/deploy/mediamtx.yml
RENDERED="${RUNTIME_DIRECTORY:?RuntimeDirectory= must be set on the unit}/mediamtx.yml"

python3 - "$TEMPLATE" "$RENDERED" <<'PY'
import os
import sys

template, rendered = sys.argv[1], sys.argv[2]
text = open(template).read().replace(
    '${GPS_DAHUA_PASSWORD_URLENC}', os.environ.get('GPS_DAHUA_PASSWORD_URLENC', '')
)
fd = os.open(rendered, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write(text)
PY

exec /mnt/nvme/mediamtx/mediamtx "$RENDERED"
