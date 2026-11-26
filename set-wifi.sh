#!/bin/zsh
# Saves Wi-Fi details to the Presto's secrets.py over USB, keeping FEED_URL.
set -e
cd "$(dirname "$0")"
read "ssid?Wi-Fi network name: "
read -s "pass?Wi-Fi password: "; echo
SSID="$ssid" PASS="$pass" python3 - <<'EOF' > /tmp/presto-wifi-$$.py
import os, json
print("s = open('secrets.py').read().splitlines()")
print("s = [l for l in s if not l.startswith(('WIFI_SSID', 'WIFI_PASSWORD'))]")
lines = ["WIFI_SSID = " + repr(os.environ["SSID"]), "WIFI_PASSWORD = " + repr(os.environ["PASS"])]
print("s = %s + s" % json.dumps(lines))
print("open('secrets.py', 'w').write(chr(10).join(s) + chr(10))")
print("print('Saved Wi-Fi settings')")
EOF
.venv/bin/mpremote run /tmp/presto-wifi-$$.py
rm -f /tmp/presto-wifi-$$.py
.venv/bin/mpremote reset
