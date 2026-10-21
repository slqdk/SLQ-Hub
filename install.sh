#!/usr/bin/env bash
# SLQ Hub installer. Run inside the Ubuntu 24.04 VM, as root, from the clone:
#
#     sudo bash /opt/slq-hub/install.sh
#
# It works in stages and remembers where it got to. After the first stage it
# reboots once (new kernel + AMD GPU driver); log in again and run the same
# command - it continues. Running it again later only repeats what is missing.
#
#   config   a few questions -> /srv/slq-hub/.env (secrets generated here)
#   system   HWE kernel 7.0, amdgpu-dkms, Docker, the sandbox firewall   (reboot)
#   gpu      checks that the GPU is usable for ROCm (/dev/kfd, PCIe atomics)
#   build    folders and the container images (~30-60 min the first time)
#   models   the default models from Hugging Face (~60 GB)
#   start    starts everything and waits for the Hub to answer
#   selftest loads the main model, renders nothing, checks search and sandbox
#
# Everything is logged to /var/log/slq-hub-install.log.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HUB_ROOT="${HUB_ROOT:-/srv/slq-hub}"
ENV_FILE="$HUB_ROOT/.env"
STATE_DIR="/var/lib/slq-hub"
STATE="$STATE_DIR/install-stages"
AMDGPU_VERSION="${AMDGPU_VERSION:-31.60}"
LOG="/var/log/slq-hub-install.log"
export DEBIAN_FRONTEND=noninteractive

c_head=$'\e[36;1m'; c_ok=$'\e[32m'; c_warn=$'\e[33m'; c_bad=$'\e[31m'; c_off=$'\e[0m'
[[ -t 1 ]] || { c_head=""; c_ok=""; c_warn=""; c_bad=""; c_off=""; }
section() { echo; echo "${c_head}$*${c_off}"; }
ok()      { echo "  ${c_ok}[OK]${c_off}   $*"; }
warn()    { echo "  ${c_warn}[WARN]${c_off} $*"; }
info()    { echo "         $*"; }
die()     { echo; echo "  ${c_bad}[STOP]${c_off} $*"; echo; exit 1; }

done_stage() { grep -qx "$1" "$STATE" 2>/dev/null; }
mark()       { mkdir -p "$STATE_DIR"; echo "$1" >> "$STATE"; }
env_get()    { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | tail -n1; }
env_set() {  # env_set KEY VALUE - replace or append in .env
    local key="$1" val="$2"
    if grep -q "^$key=" "$ENV_FILE"; then
        python3 - "$ENV_FILE" "$key" "$val" <<'PY'
import sys
path, key, val = sys.argv[1:4]
lines = open(path, encoding="utf-8").read().splitlines()
lines = [f"{key}={val}" if l.startswith(key + "=") else l for l in lines]
open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
PY
    else
        echo "$key=$val" >> "$ENV_FILE"
    fi
}
compose() { python3 "$REPO/tools/hub_update.py" compose "$@"; }

[[ $EUID -eq 0 ]] || die "run it with sudo:  sudo bash $REPO/install.sh"
mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1

section "=== SLQ Hub - install ($(cat "$REPO/VERSION" 2>/dev/null || echo '?')) ==="
info "Repo: $REPO   Data: $HUB_ROOT   Log: $LOG"
# shellcheck disable=SC1091
. /etc/os-release
[[ "${VERSION_ID:-}" == "24.04" ]] || warn "this is $PRETTY_NAME - SLQ Hub is built for Ubuntu 24.04."

# ================================================================ config
stage_config() {
    if [[ -f "$ENV_FILE" ]]; then
        ok "settings exist: $ENV_FILE (edit it by hand to change them)"
        return
    fi
    section "Settings"
    info "Enter keeps the [default]."
    local url proxy cidrs user pw1 pw2 klein
    read -r -p "  Address of the web UI (Nginx Proxy Manager) [https://hub.slq.dk]: " url || true
    read -r -p "  IP of Nginx Proxy Manager [30.11.0.103]: " proxy || true
    read -r -p "  Networks allowed to use the model API [30.11.0.0/20,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8]: " cidrs || true
    read -r -p "  Admin user name [soren]: " user || true
    while true; do
        read -r -s -p "  Admin password (at least 10 characters): " pw1 || true; echo
        read -r -s -p "  Once more: " pw2 || true; echo
        [[ "$pw1" == "$pw2" ]] || { warn "the two did not match"; continue; }
        [[ ${#pw1} -ge 10 ]] || { warn "too short"; continue; }
        break
    done
    read -r -p "  Also download the optional FLUX.2 [klein] 4B image model (16 GB)? [y/N]: " klein || true
    local hash
    hash="$(HUB_PW="$pw1" python3 -c '
import base64, hashlib, os
pw = os.environ["HUB_PW"].encode()
salt = os.urandom(16)
n, r, p = 2**15, 8, 1
dk = hashlib.scrypt(pw, salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)
print("scrypt:%d:%d:%d:%s:%s" % (n, r, p, base64.b64encode(salt).decode(), base64.b64encode(dk).decode()))
')"
    unset pw1 pw2
    mkdir -p "$HUB_ROOT"
    umask 077
    cat > "$ENV_FILE" <<EOF
# SLQ Hub - machine settings and secrets (written by install.sh $(date -I)).
# See deploy/env.example for what each line means. Never commit this file.
HUB_ROOT=$HUB_ROOT
TZ=Europe/Copenhagen
RENDER_GID=$(getent group render | cut -d: -f3)
HUB_PORT=8080
HUB_OLLAMA_PORT=11434
HUB_LAN_HOST=$(hostname -I | awk '{print $1}')
HUB_PUBLIC_URL=${url:-https://hub.slq.dk}
HUB_TRUSTED_PROXIES=${proxy:-30.11.0.103}
HUB_LAN_CIDRS=${cidrs:-30.11.0.0/20,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8}
HUB_SECRET_KEY=$(openssl rand -hex 32)
HUB_INTERNAL_TOKEN=$(openssl rand -hex 32)
SEARXNG_SECRET=$(openssl rand -hex 32)
HUB_ADMIN_USER=${user:-soren}
HUB_ADMIN_PASSWORD_HASH='$hash'
LLAMA_IMAGE=ghcr.io/ggml-org/llama.cpp:server-rocm-b11783
COMFYUI_REF=v0.42.3
TORCH_INDEX=https://download.pytorch.org/whl/rocm7.14
TORCH_VERSION=2.14.1
TORCHVISION_VERSION=0.29.1
SEARXNG_TAG=latest
COMFY_ARGS=
SANDBOX_CPUS=6
SANDBOX_MEM=8g
INSTALL_EXTRA_MODELS=$([[ "$klein" =~ ^[yYjJ] ]] && echo flux2-klein-4b || true)
EOF
    umask 022
    chmod 600 "$ENV_FILE"
    ok "settings written to $ENV_FILE"
}

# ================================================================ system
stage_system() {
    section "System: kernel, AMD GPU driver, Docker, firewall"
    apt-get update -q
    apt-get -y -q -o Dpkg::Options::=--force-confold full-upgrade
    apt-get -y -q install ca-certificates curl gnupg git jq python3 openssl wget dkms
    ok "system packages up to date"
    timedatectl set-timezone Europe/Copenhagen 2>/dev/null || true

    # AMD supports Radeon on Ubuntu 24.04 with the HWE kernel (7.0). The
    # generic HWE package brings headers, modules-extra and firmware.
    apt-get -y -q install linux-generic-hwe-24.04
    local hwe
    hwe="$(find /lib/modules -maxdepth 1 -mindepth 1 -printf '%f\n' | sort -V | tail -n1)"
    ok "HWE kernel installed: $hwe"

    # AMD's GPU driver (31.x numbering, compatible with ROCm 7.x - 10.1).
    install -d -m 0755 /etc/apt/keyrings
    wget -qO- https://repo.radeon.com/rocm/rocm.gpg.key | gpg --dearmor --yes -o /etc/apt/keyrings/rocm.gpg
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/amdgpu/$AMDGPU_VERSION/ubuntu noble main" \
        > /etc/apt/sources.list.d/amdgpu.list
    printf 'Package: *\nPin: release o=repo.radeon.com\nPin-Priority: 600\n' > /etc/apt/preferences.d/repo-radeon-pin-600
    apt-get update -q
    apt-get -y -q install amdgpu-dkms
    if ! dkms status 2>/dev/null | grep -q "amdgpu.*$hwe.*installed"; then
        info "building the amdgpu module for $hwe ..."
        dkms autoinstall -k "$hwe" || true
    fi
    dkms status 2>/dev/null | grep -q "amdgpu.*$hwe.*installed" \
        || die "amdgpu-dkms did not build for kernel $hwe. See: dkms status; and /var/lib/dkms/amdgpu/*/build/make.log"
    ok "amdgpu-dkms $AMDGPU_VERSION built for $hwe"

    # Docker Engine from Docker's own repository.
    if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
        chmod a+r /etc/apt/keyrings/docker.asc
        cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-noble}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
        apt-get update -q
        apt-get -y -q install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
    systemctl enable --now docker >/dev/null
    ok "Docker $(docker --version | awk '{print $3}' | tr -d ,)"

    # The sandbox firewall, re-applied every time Docker starts.
    install -m 0755 "$REPO/deploy/firewall/slq-hub-firewall.sh" /usr/local/sbin/slq-hub-firewall.sh
    install -m 0644 "$REPO/deploy/firewall/slq-hub-firewall.service" /etc/systemd/system/slq-hub-firewall.service
    echo br_netfilter > /etc/modules-load.d/slq-hub.conf
    echo "net.bridge.bridge-nf-call-iptables = 1" > /etc/sysctl.d/90-slq-hub.conf
    systemctl daemon-reload
    systemctl enable slq-hub-firewall.service >/dev/null
    ok "sandbox firewall installed (slq-hub-firewall.service)"

    local who="${SUDO_USER:-}"
    if [[ -n "$who" && "$who" != "root" ]]; then
        usermod -aG docker,render,video "$who"
        ok "$who may use Docker and the GPU (after the next login)"
    fi
}

# ================================================================ gpu
stage_gpu() {
    section "GPU check"
    local kver
    kver="$(uname -r)"
    if [[ "${kver%%.*}" -lt 7 ]]; then
        warn "running kernel $kver - expected the HWE kernel 7.x. AMD supports Radeon only with HWE."
    else
        ok "kernel $kver"
    fi
    lsmod | grep -q '^amdgpu' || die "the amdgpu module is not loaded. Check: dmesg | grep -i amdgpu"
    ok "amdgpu module loaded"
    if dmesg 2>/dev/null | grep -qi 'PCI rejects atomics'; then
        die "the kernel skipped the GPU for ROCm: 'PCI rejects atomics'. gfx11 needs PCIe atomics
         in a VM (or newer MEC firmware). Check the VM's hostpci line has pcie=1 and the
         machine type is q35; dmesg | grep -i atomics shows the details."
    fi
    [[ -e /dev/kfd ]] || die "/dev/kfd does not exist - ROCm cannot see the GPU.
         If the VM just got the GPU from VM 104, reboot the Proxmox host once (RDNA3 reset problem)."
    ok "/dev/kfd present"
    local nodes=(/dev/dri/renderD*)
    [[ -e "${nodes[0]}" ]] || die "no /dev/dri/renderD* device."
    ok "render node: ${nodes[*]}"
    local vram=""
    for f in /sys/class/drm/card*/device/mem_info_vram_total; do
        [[ -r "$f" ]] && vram="$(( $(cat "$f") / 1024 / 1024 / 1024 )) GB"
    done
    [[ -n "$vram" ]] && ok "VRAM: $vram"
    local gid
    gid="$(getent group render | cut -d: -f3)"
    [[ -n "$gid" ]] || die "there is no 'render' group."
    env_set RENDER_GID "$gid"
    ok "render group GID $gid written to .env"
}

# ================================================================ build
stage_build() {
    section "Folders and container images"
    mkdir -p "$HUB_ROOT"/{data,backups} \
             "$HUB_ROOT"/data/{workspace/projects,workspace/chats,sandbox-home,logs/llm} \
             "$HUB_ROOT"/models/llm \
             "$HUB_ROOT"/models/comfy/{diffusion_models,text_encoders,vae,loras,checkpoints,upscale_models,model_patches} \
             "$HUB_ROOT"/comfy/{input,output,user}
    chown -R 1000:1000 "$HUB_ROOT/data" "$HUB_ROOT/models" "$HUB_ROOT/comfy"
    ok "folders under $HUB_ROOT (owned by uid 1000, the containers' user)"
    info "Building images - the first time takes a while (ROCm and PyTorch are large)."
    compose pull searxng
    compose build
    ok "images built"
}

# ================================================================ models
stage_models() {
    section "Models"
    local extra
    extra="$(env_get INSTALL_EXTRA_MODELS)"
    # shellcheck disable=SC2086  # extra is a list of ids
    python3 "$REPO/tools/models.py" pull --defaults $extra --root "$HUB_ROOT/models" \
        || die "some downloads failed - run the installer again to resume them."
    chown -R 1000:1000 "$HUB_ROOT/models"
    ok "models in place"
}

# ================================================================ start
stage_start() {
    section "Starting"
    compose up -d
    systemctl restart slq-hub-firewall.service || warn "the sandbox firewall did not apply - see: journalctl -u slq-hub-firewall"
    local port
    port="$(env_get HUB_PORT)"; port="${port:-8080}"
    for _ in $(seq 1 100); do
        if curl -fsS -o /dev/null "http://127.0.0.1:$port/hub/api/health"; then
            ok "the Hub answers on port $port"
            return
        fi
        sleep 3
    done
    compose logs --tail 60 hub || true
    die "the Hub did not answer within 5 minutes (log above)."
}

# ================================================================ selftest
stage_selftest() {
    section "Self-test"
    if compose exec -T hub python -m app.cli selftest; then
        ok "self-test passed"
    else
        warn "the self-test reported problems (above). The Hub runs; fix them and re-run:"
        info "sudo bash $REPO/update.sh compose exec hub python -m app.cli selftest"
    fi
}

# ================================================================ run
if ! done_stage config; then stage_config; mark config; fi
if ! done_stage system; then
    stage_system
    mark system
    section "Reboot needed"
    info "The new kernel and the GPU driver load after a reboot. Log in again and run:"
    info ""
    info "    sudo bash $REPO/install.sh"
    info ""
    read -r -t 30 -p "  Rebooting in 30 s (Enter = now, Ctrl+C = later)... " _ || true
    echo
    systemctl reboot
    exit 0
fi
if ! done_stage gpu;   then stage_gpu;   mark gpu;   fi
if ! done_stage build; then stage_build; mark build; fi
if ! done_stage models; then stage_models; mark models; fi
stage_start
done_stage start || mark start
stage_selftest

ip="$(hostname -I | awk '{print $1}')"
section "=== SLQ Hub is running ==="
cat <<EOF
  Web UI on the LAN : http://$ip:$(env_get HUB_PORT)
  Through the proxy : $(env_get HUB_PUBLIC_URL)   (set it up in Nginx Proxy Manager: docs/INSTALL.md step 4)
  Sign in as        : $(env_get HUB_ADMIN_USER)

  Other apps (FamilyLog, Coagent): create a key in Settings -> API keys, then see docs/API.md.
  Updates           : sudo bash $REPO/update.sh
  Logs              : sudo bash $REPO/update.sh compose logs -f hub
EOF
