#!/bin/sh
# Updates SLQ Hub on its VM: pull, build, back up the data, restart, check -
# and put everything back if the new version does not start. The work is in
# tools/hub_update.py (standard-library Python, tested).
#   sudo bash update.sh            update (asks first; stops the Hub once nobody has used it for a minute)
#   sudo bash update.sh --yes      update without asking
#   sudo bash update.sh --now      do not wait for the Hub to be free
#   sudo bash update.sh cancel     stop an update that still builds or waits
#   sudo bash update.sh status | backups | rollback [name] | compose <args>
exec python3 "$(dirname "$0")/tools/hub_update.py" "$@"
