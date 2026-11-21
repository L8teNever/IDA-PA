#!/bin/sh
# IDA Health: startet WARP (für Cloudflare Mesh) im Hintergrund – nur, wenn es installiert ist und der Container es darf
# (cap_add NET_ADMIN + /dev/net/tun). Sonst, oder wenn WARP abstürzt, läuft Health ganz normal weiter.
# Die Mesh-Registrierung liegt in /data/warp (Volume), damit sie Updates und Neustarts übersteht.
if [ "${HEALTH_WARP:-1}" != 0 ] && command -v warp-svc >/dev/null 2>&1 && [ -c /dev/net/tun ]; then
  mkdir -p /data/warp
  if [ ! -L /var/lib/cloudflare-warp ]; then
    rm -rf /var/lib/cloudflare-warp
    ln -s /data/warp /var/lib/cloudflare-warp
  fi
  mkdir -p /run/dbus
  [ -e /run/dbus/system_bus_socket ] || dbus-daemon --system --fork --nopidfile >/dev/null 2>&1 || true
  (
    while true; do
      warp-svc --accept-tos >/tmp/warp-svc.log 2>&1
      echo "health: warp-svc beendet ($?), neuer Versuch in 30 s" >&2
      sleep 30
    done
  ) &
else
  echo "health: ohne WARP (Cloudflare Mesh für Health aus – siehe Einstellungen → Cloudflare Mesh)" >&2
fi
exec "$@"
