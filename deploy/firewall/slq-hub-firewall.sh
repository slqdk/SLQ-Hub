#!/usr/bin/env bash
# Keeps the agent's sandbox off the LAN, and the model containers off
# everything outside their own network. Installed by install.sh (and kept in
# step by update.sh) as /usr/local/sbin/slq-hub-firewall.sh and run by
# slq-hub-firewall.service every time Docker starts.
#
# The sandbox container has a fixed address (172.31.250.20, see
# deploy/docker-compose.yml). New connections FROM it are dropped when they
# go to private ranges, your LAN (HUB_LAN_CIDRS), the other containers, or
# the VM itself. The internet stays open (pip, npm, git clone, web pages).
# Replies to connections the Hub opens TO the sandbox are not new, so they pass.
#
# The model containers - llm (172.31.251.11), comfyui (.12) and speech (.13)
# on the "inner" network - need nothing from outside it: new connections from
# them to anything else (the internet, the LAN, the VM) are dropped. The Hub
# reaches them on that network; ComfyUI's maintenance port still answers.
#
# Docker supports iptables (nft backend) but not raw nft rules, so this uses
# iptables. Rules are added only when missing (-C), so it is safe to re-run.
set -euo pipefail

SANDBOX_IP="${SANDBOX_IP:-172.31.250.20}"
INNER_NET="${INNER_NET:-172.31.251.0/24}"
MODEL_IPS="${MODEL_IPS:-172.31.251.11 172.31.251.12 172.31.251.13}"
ENV_FILE="${HUB_ENV_FILE:-/srv/slq-hub/.env}"

lan=""
if [[ -r "$ENV_FILE" ]]; then
    lan="$(sed -n 's/^HUB_LAN_CIDRS=//p' "$ENV_FILE" | tail -n1 | tr -d '"' | tr ',' ' ')"
fi
nets="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10 127.0.0.0/8 $lan"

# Bridged traffic between containers on one network only meets iptables
# with br_netfilter loaded - needed for the rule that keeps the sandbox from
# opening connections to the Hub on their shared network.
modprobe br_netfilter 2>/dev/null || true
sysctl -q -w net.bridge.bridge-nf-call-iptables=1 2>/dev/null || true

# Docker creates DOCKER-USER when it starts; wait for it after a boot.
for _ in $(seq 1 30); do
    iptables -L DOCKER-USER -n >/dev/null 2>&1 && break
    sleep 1
done
if ! iptables -L DOCKER-USER -n >/dev/null 2>&1; then
    echo "slq-hub-firewall: the DOCKER-USER chain does not exist (is Docker running?)" >&2
    exit 1
fi

add() {   # add <chain> <rule...> - insert at the top unless it is already there
    local chain="$1"; shift
    iptables -C "$chain" "$@" 2>/dev/null || iptables -I "$chain" "$@"
}

seen=" "
for net in $nets; do
    [[ -z "$net" || "$seen" == *" $net "* ]] && continue
    seen+="$net "
    add DOCKER-USER -s "$SANDBOX_IP" -d "$net" -m conntrack --ctstate NEW -j DROP
done
# The VM itself (its LAN address and the Docker gateways) is INPUT, not FORWARD.
add INPUT -s "$SANDBOX_IP" -m conntrack --ctstate NEW -j DROP

for ip in $MODEL_IPS; do
    add DOCKER-USER -s "$ip" ! -d "$INNER_NET" -m conntrack --ctstate NEW -j DROP
    add INPUT -s "$ip" -m conntrack --ctstate NEW -j DROP
done

echo "slq-hub-firewall: sandbox $SANDBOX_IP blocked from:$seen and the VM itself"
echo "slq-hub-firewall: model containers ($MODEL_IPS) blocked from everything outside $INNER_NET"
