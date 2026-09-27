#!/bin/sh
# Render mounts the data disk as root: give it to the app user, then run as that user.
set -e
mkdir -p /data/workspace
chown -R app:app /data
exec setpriv --reuid=app --regid=app --init-groups env HOME=/home/app "$@"
