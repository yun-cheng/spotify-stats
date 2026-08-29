#!/bin/sh
# Install the launchd agent that polls every 30 minutes.
# Generates the plist from this checkout's location, so it works anywhere.
set -e
DIR=$(cd "$(dirname "$0")/.." && pwd)
PY=$(command -v python3)
LABEL=local.spotify-stats
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$HOME/Library/LaunchAgents" "$DIR/var/logs"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$DIR/collector/poll.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartInterval</key><integer>1800</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$DIR/var/logs/poll.log</string>
  <key>StandardErrorPath</key><string>$DIR/var/logs/poll.err</string>
</dict>
</plist>
PLIST_EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL — polling every 30 minutes."
echo "Logs: $DIR/var/logs/poll.log"
