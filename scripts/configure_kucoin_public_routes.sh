#!/usr/bin/env bash
set -euo pipefail

HOST="${KUCOIN_ROUTE_HOST:-api-futures.kucoin.com}"
IFACE="${KUCOIN_ROUTE_IFACE:-ens3}"

GATEWAY="$(ip route show default | awk -v iface="$IFACE" '$0 ~ (" dev " iface " ") || $0 ~ (" dev " iface "$") {for (i = 1; i <= NF; i++) if ($i == "via") {print $(i + 1); exit}}')"
if [[ -z "$GATEWAY" ]]; then
  GATEWAY="$(ip route show | awk -v iface="$IFACE" '($0 ~ (" dev " iface " ") || $0 ~ (" dev " iface "$")) && $1 != "default" {for (i = 1; i <= NF; i++) if ($i == "via") {print $(i + 1); exit}}')"
fi

if [[ -z "$GATEWAY" ]]; then
  echo "Could not determine gateway for interface $IFACE" >&2
  exit 1
fi

mapfile -t IPS < <(getent ahostsv4 "$HOST" | awk '{print $1}' | sort -u)
if [[ ${#IPS[@]} -eq 0 ]]; then
  echo "Could not resolve IPv4 addresses for $HOST" >&2
  exit 1
fi

for ip in "${IPS[@]}"; do
  ip route replace "$ip/32" via "$GATEWAY" dev "$IFACE"
done

echo "Configured KuCoin public routes via $IFACE ($GATEWAY): ${IPS[*]}"
