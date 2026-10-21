#!/usr/bin/env bash
# Creates the SLQ Hub VM on a Proxmox VE host and moves the RX 7900 XTX to it
# from the old GPU VM. Run on the Proxmox host as root:
#
#   bash create-vm.sh             asks; Enter keeps the [default]
#   bash create-vm.sh --dry-run   prints every command, changes nothing
#
# Every answer can also be given as an environment variable (VMID=150 ...).
# What it does:
#   1. downloads the Ubuntu 24.04 cloud image (checksum verified)
#   2. enables "snippets" on the local storage and writes a cloud-init
#      vendor file: qemu-guest-agent + git, and clones the repo to /opt/slq-hub
#   3. creates a q35/OVMF VM with the image as a resized boot disk, cloud-init
#      (user, SSH key, IP), no ballooning (passthrough pins RAM)
#   4. shuts the old GPU VM down, sets it to not start at boot, copies its
#      hostpci line(s) to the new VM and starts the new VM
set -euo pipefail

DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

REPO_URL="${REPO_URL:-https://github.com/slqdk/SLQ-Hub}"
IMG_NAME="ubuntu-24.04-server-cloudimg-amd64.img"
IMG_BASE="https://cloud-images.ubuntu.com/releases/noble/release"
IMG_DIR="/var/lib/vz/template/iso"
IMG="$IMG_DIR/$IMG_NAME"
SNIPPET_DIR="/var/lib/vz/snippets"
SNIPPET="slq-hub-vendor.yaml"

c_head=$'\e[36;1m'; c_ok=$'\e[32m'; c_warn=$'\e[33m'; c_bad=$'\e[31m'; c_off=$'\e[0m'
[[ -t 1 ]] || { c_head=""; c_ok=""; c_warn=""; c_bad=""; c_off=""; }
section() { echo; echo "${c_head}$*${c_off}"; }
ok()   { echo "  ${c_ok}[OK]${c_off}   $*"; }
warn() { echo "  ${c_warn}[WARN]${c_off} $*"; }
die()  { echo; echo "  ${c_bad}[STOP]${c_off} $*"; echo; exit 1; }
run()  { echo "  + $*"; [[ $DRY -eq 1 ]] || "$@"; }

ask() {  # ask VAR "question" default  - an exported VAR wins without asking
    local var="$1" q="$2" def="${3:-}" ans
    if [[ -n "${!var:-}" ]]; then
        echo "  $q: ${!var}"
        return
    fi
    read -r -p "  $q [$def]: " ans || true
    printf -v "$var" '%s' "${ans:-$def}"
}

# ---------------------------------------------------------------- checks
[[ $EUID -eq 0 ]] || die "run this as root on the Proxmox host."
command -v qm >/dev/null && command -v pvesh >/dev/null && command -v pvesm >/dev/null \
    || die "qm/pvesh/pvesm not found - this must run on the Proxmox VE host."

section "=== SLQ Hub - create the VM ==="
[[ $DRY -eq 1 ]] && warn "Dry run: nothing is changed."

# ---------------------------------------------------------------- questions
default_storage="$(pvesm status --content images --enabled 1 2>/dev/null \
    | awk 'NR>1 && $3=="active" {print $1}' | grep -x 'local-lvm' || true)"
[[ -n "$default_storage" ]] || default_storage="$(pvesm status --content images --enabled 1 2>/dev/null \
    | awk 'NR>1 && $3=="active" {print $1; exit}')"
default_key=""
[[ -s /root/.ssh/authorized_keys ]] && default_key="/root/.ssh/authorized_keys"

ask VMID     "VM id"                                  "$(pvesh get /cluster/nextid)"
ask NAME     "VM name"                                "slq-hub"
ask GPU_FROM "VM that has the GPU now"                "104"
ask STORAGE  "Storage for the disks"                  "$default_storage"
ask CORES    "CPU cores"                              "8"
ask MEMORY   "RAM in MB"                              "40960"
ask DISK     "Disk size"                              "500G"
ask BRIDGE   "Network bridge"                         "vmbr0"
ask IPADDR   "IP address with prefix, or dhcp"        "dhcp"
GATEWAY="${GATEWAY:-}"
[[ "$IPADDR" == "dhcp" ]] || ask GATEWAY "Gateway" ""
ask DNS      "DNS servers, space separated (empty = the host's)" ""
ask CIUSER   "Login user in the VM"                   "slq"
ask SSHKEYS  "File with the SSH public key(s) for that user" "$default_key"

[[ "$VMID" =~ ^[0-9]+$ ]] || die "VM id must be a number."
qm status "$VMID" >/dev/null 2>&1 && die "VM $VMID already exists."
[[ -n "$STORAGE" ]] || die "no storage for disk images found (pvesm status --content images)."
[[ -s "$SSHKEYS" ]] || die "no SSH public key file at '$SSHKEYS'. Copy your public key to the host first."
if [[ "$IPADDR" != "dhcp" ]]; then
    [[ "$IPADDR" == */* ]] || die "give the IP with its prefix, e.g. 30.11.0.140/20."
    [[ -n "$GATEWAY" ]] || die "a static IP needs a gateway."
fi

# The GPU: every hostpciN line of the old VM. x-vga is dropped - the Hub VM
# is headless and keeps its serial console (cloud images want one).
mapfile -t HOSTPCI < <(qm config "$GPU_FROM" 2>/dev/null | grep -E '^hostpci[0-9]+:' || true)
[[ ${#HOSTPCI[@]} -gt 0 ]] || die "VM $GPU_FROM has no hostpci device. Is it the right VM?"

section "Summary"
echo "  New VM      : $VMID ($NAME) - $CORES cores, $MEMORY MB RAM, $DISK on $STORAGE, $BRIDGE"
echo "  Network     : $IPADDR${GATEWAY:+ via $GATEWAY}${DNS:+, DNS $DNS}"
echo "  Login       : $CIUSER with the key(s) in $SSHKEYS"
for line in "${HOSTPCI[@]}"; do
    echo "  GPU         : $line   (from VM $GPU_FROM)"
done
echo "  VM $GPU_FROM      : will be shut down and set to not start at boot (kept as fallback)"
echo
if [[ $DRY -eq 0 ]]; then
    read -r -p "  Create the VM and move the GPU? [y/N] " yn || true
    [[ "$yn" =~ ^([yY]|[yY]es|[jJ]|[jJ]a)$ ]] || die "nothing was changed."
fi

# ---------------------------------------------------------------- 1. image
section "1. Ubuntu 24.04 cloud image"
mkdir -p "$IMG_DIR"
want_sum=""
if sums="$(wget -qO- "$IMG_BASE/SHA256SUMS")"; then
    want_sum="$(awk -v f="*$IMG_NAME" '$2==f || $2==substr(f,2) {print $1}' <<<"$sums")"
fi
[[ -n "$want_sum" ]] || warn "could not read SHA256SUMS - the image will not be verified."
have_sum=""
[[ -f "$IMG" ]] && have_sum="$(sha256sum "$IMG" | awk '{print $1}')"
if [[ -n "$want_sum" && "$have_sum" == "$want_sum" ]]; then
    ok "already downloaded and verified: $IMG"
else
    run wget -q --show-progress -O "$IMG.part" "$IMG_BASE/$IMG_NAME"
    if [[ $DRY -eq 0 ]]; then
        if [[ -n "$want_sum" ]]; then
            got="$(sha256sum "$IMG.part" | awk '{print $1}')"
            [[ "$got" == "$want_sum" ]] || die "checksum mismatch for the downloaded image."
        fi
        mv "$IMG.part" "$IMG"
    fi
    ok "downloaded${want_sum:+ and verified}: $IMG"
fi

# ---------------------------------------------------------------- 2. snippet
section "2. Cloud-init vendor file"
content="$(awk '/^[a-z]+: /{inblk=($0=="dir: local")} inblk && $1=="content" {print $2}' /etc/pve/storage.cfg)"
if [[ ",$content," != *",snippets,"* ]]; then
    [[ -n "$content" ]] || die "could not read the content types of storage 'local' in /etc/pve/storage.cfg."
    run pvesm set local --content "$content,snippets"     # --content replaces the list
    ok "storage 'local' now also holds snippets"
else
    ok "storage 'local' already holds snippets"
fi
run mkdir -p "$SNIPPET_DIR"
vendor="#cloud-config
package_update: true
packages:
  - qemu-guest-agent
  - git
  - curl
runcmd:
  - [systemctl, enable, --now, qemu-guest-agent]
  - [bash, -c, \"test -d /opt/slq-hub/.git || git clone $REPO_URL /opt/slq-hub\"]
"
if [[ $DRY -eq 1 ]]; then
    echo "  + write $SNIPPET_DIR/$SNIPPET:"; sed 's/^/      /' <<<"$vendor"
else
    printf '%s' "$vendor" > "$SNIPPET_DIR/$SNIPPET"
fi
ok "$SNIPPET_DIR/$SNIPPET"

# ---------------------------------------------------------------- 3. the VM
section "3. Creating VM $VMID"
run qm create "$VMID" --name "$NAME" --ostype l26 --machine q35 --bios ovmf \
    --efidisk0 "$STORAGE:1,efitype=4m,pre-enrolled-keys=0" \
    --cpu host --sockets 1 --cores "$CORES" --memory "$MEMORY" --balloon 0 \
    --scsihw virtio-scsi-single --scsi0 "$STORAGE:0,import-from=$IMG,iothread=1,discard=on,ssd=1" \
    --ide2 "$STORAGE:cloudinit" --boot order=scsi0 --serial0 socket --vga serial0 \
    --net0 "virtio,bridge=$BRIDGE" --agent enabled=1 --onboot 1 --tags slq-hub \
    --description "SLQ Hub - created by proxmox/create-vm.sh from $REPO_URL"
run qm disk resize "$VMID" scsi0 "$DISK"
ipconfig="ip=dhcp"
[[ "$IPADDR" == "dhcp" ]] || ipconfig="ip=$IPADDR,gw=$GATEWAY"
run qm set "$VMID" --ciuser "$CIUSER" --sshkeys "$SSHKEYS" --ipconfig0 "$ipconfig" \
    --cicustom "vendor=local:snippets/$SNIPPET"
if [[ -n "$DNS" ]]; then
    run qm set "$VMID" --nameserver "$DNS"
fi
ok "VM $VMID created"

# ---------------------------------------------------------------- 4. GPU
section "4. Moving the GPU from VM $GPU_FROM"
if qm status "$GPU_FROM" 2>/dev/null | grep -q running; then
    run qm shutdown "$GPU_FROM" --timeout 180 --forceStop 1
    ok "VM $GPU_FROM is shut down"
else
    ok "VM $GPU_FROM is not running"
fi
run qm set "$GPU_FROM" --onboot 0
ok "VM $GPU_FROM will not start at boot (both VMs cannot hold the GPU)"
for line in "${HOSTPCI[@]}"; do
    key="${line%%:*}"
    val="${line#*: }"
    val="$(sed -E 's/(^|,)x-vga=(1|on)//; s/^,//' <<<"$val")"
    run qm set "$VMID" "--$key" "$val"
done
ok "GPU attached to VM $VMID"

section "5. Starting VM $VMID"
if ! run qm start "$VMID"; then
    die "VM $VMID did not start. If the error mentions the PCI device, the RX 7900 XTX
         did not reset after VM $GPU_FROM (a known RDNA3 problem): reboot the Proxmox
         host - VM $GPU_FROM no longer starts at boot, VM $VMID does."
fi
ok "started"

if [[ $DRY -eq 1 ]]; then
    section "Dry run complete - nothing was changed."
    exit 0
fi

# ---------------------------------------------------------------- 6. address
ip=""
if [[ "$IPADDR" != "dhcp" ]]; then
    ip="${IPADDR%/*}"
else
    echo "  Waiting for the guest agent to report an address (first boot installs it)…"
    for _ in $(seq 1 90); do
        ip="$(qm guest cmd "$VMID" network-get-interfaces 2>/dev/null \
            | grep -oE '"ip-address" *: *"[0-9]+(\.[0-9]+){3}"' | grep -oE '[0-9]+(\.[0-9]+){3}' \
            | grep -v '^127\.' | grep -v '^172\.' | head -n1 || true)"
        [[ -n "$ip" ]] && break
        sleep 5
    done
fi

section "=== VM $VMID is up ==="
if [[ -n "$ip" ]]; then
    echo "  Address : $ip"
else
    warn "no address reported yet - see the VM's summary in the Proxmox UI, or your DHCP leases."
    ip="<vm-address>"
fi
cat <<EOF

  Next, from your PC (cloud-init needs a few minutes on the first boot to
  upgrade packages and clone the repo to /opt/slq-hub):

      ssh $CIUSER@$ip

  then, in the VM:

      sudo bash /opt/slq-hub/install.sh

  If the GPU does not show up in the VM (install.sh checks), reboot the
  Proxmox host once: VM $GPU_FROM no longer starts at boot, VM $VMID does.
EOF
