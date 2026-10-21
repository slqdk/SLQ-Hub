# Installing SLQ Hub

From an empty Proxmox host slot to a running Hub. Most of the time is
spent waiting for downloads and image builds: plan on 1.5–2.5 hours, of
which a few minutes are typing.

Every command below is a single line. Paste one at a time.

## Before you start

- **VM 104 has the RX 7900 XTX** passed through and works. The script
  copies that passthrough to the new VM.
- **Your PC's SSH public key is on the Proxmox host**, in
  `/root/.ssh/authorized_keys` or in any file you point the script at. The new
  VM's login uses it.
- **About 520 GB is free** on the storage that will hold the VM disk.
- **Pick an address** for the VM, e.g. `30.11.0.140/20` with your router as
  gateway, or use DHCP and reserve the lease afterwards.

## 1. Create the VM (on the Proxmox host)

Open a shell on the Proxmox host (web UI → node → Shell, or SSH as root):

    curl -fsSL https://raw.githubusercontent.com/slqdk/slq-hub/main/proxmox/create-vm.sh -o /root/create-vm.sh

To see every command the script would run, without changing anything:

    bash /root/create-vm.sh --dry-run

Then for real:

    bash /root/create-vm.sh

It asks for the following. Enter keeps the default shown in brackets.

| Question | Suggested answer |
|---|---|
| VM id | the next free one |
| VM name | `slq-hub` |
| VM that has the GPU now | `104` |
| Storage | `local-lvm` (or your VM storage) |
| CPU cores / RAM / disk | `8` / `40960` / `500G` |
| IP address | `30.11.0.140/20` (or `dhcp`) |
| Gateway / DNS | your router / empty (uses the host's) |
| Login user | `slq` |
| SSH key file | `/root/.ssh/authorized_keys` |

What happens next:

1. The script shows a summary and asks once.
2. It downloads the Ubuntu 24.04 cloud image and checks it.
3. It creates the VM.
4. It **shuts down VM 104**, sets it to not start at boot, and moves the GPU.
5. It starts the new VM and prints its address.

> **If the new VM does not start, or later has no GPU:** the 7900 XTX does not
> always reset when it moves between VMs, which is a known RDNA3 problem.
> Reboot the Proxmox host once. VM 104 no longer starts at boot, and the Hub
> VM does.

## 2. Install (in the new VM)

From your PC:

    ssh slq@30.11.0.140

On the first boot, cloud-init upgrades packages and clones this repo to
`/opt/slq-hub`. Wait for it to finish:

    cloud-init status --wait

Then start the installer:

    sudo bash /opt/slq-hub/install.sh

It asks a few things:

| Question | Default |
|---|---|
| Address of the web UI | `https://hub.slq.dk` |
| IP of Nginx Proxy Manager | `30.11.0.103` |
| Networks allowed to use the model API | `30.11.0.0/20,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8` |
| Admin user name / password | `soren` / your choice |
| Optional FLUX.2 klein image model | no |

Next it installs the HWE kernel, AMD's GPU driver (amdgpu 31.60), Docker and
the sandbox firewall, and then **reboots**. Log in again and run the same
command:

    sudo bash /opt/slq-hub/install.sh

It continues where it stopped:

1. **GPU check.** Stops with an explanation if ROCm cannot use the card.
2. **Images.** Builds them; 30–60 minutes the first time.
3. **Models.** Downloads about 60 GB. An interrupted download resumes when
   you run the command again.
4. **Start and self-test.** Starts everything and runs a self-test: it loads
   Qwen3.8 and answers a question, then checks ComfyUI, search and the sandbox.

It ends with the address to open.

## 3. Sign in

Open `http://30.11.0.140:8080` on the LAN and sign in with the admin user.

## 4. Nginx Proxy Manager

Add a proxy host:

| Field | Value |
|---|---|
| Domain | `hub.slq.dk` |
| Scheme / host / port | `http` / `30.11.0.140` / `8080` |
| Block common exploits | on |
| Websockets support | on |
| SSL | Let's Encrypt certificate, Force SSL, HTTP/2 |

Under **Advanced** (long answers stream, and uploads can be large):

    client_max_body_size 200m;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_buffering off;

The model API (`/v1`, `/api`) is refused when it arrives through the proxy.
That is deliberate: apps use the LAN address.

## 5. Connect FamilyLog

1. In the Hub: **Settings → API keys → New key**. Use the name `familylog` and
   priority `normal`, then copy the key (it is shown once).
2. In FamilyLog: **Settings**, then set **Ollama address** to
   `http://30.11.0.140:11434/k/<the key>` and press **Test**. The Hub's models
   appear.
3. Choose the models per role:
   - `qwen3.8-27b` for chat and vision.
   - `qwen3.6-35b-a3b` for categorising.

   Calls to the model that is already loaded never wait for a model swap.
4. Turn **off** "Free ComfyUI first" and leave the ComfyUI address empty. The
   Hub manages the GPU now.

TwinCAT Coagent and anything else: see [API.md](API.md).

## 6. Retire VM 104 later

VM 104 stays as a fallback: to go back, shut the Hub VM down and start 104.
When the Hub has run well for a while, delete VM 104 on the host:

    qm destroy 104

## Daily operation

All commands run in the VM from `/opt/slq-hub`:

| What | Command |
|---|---|
| Update (backup + automatic rollback) | `sudo bash /opt/slq-hub/update.sh` |
| Status | `sudo bash /opt/slq-hub/update.sh status` |
| Follow the Hub's log | `sudo bash /opt/slq-hub/update.sh compose logs -f hub` |
| A model server's log | `sudo bash /opt/slq-hub/update.sh compose logs -f llm` |
| Backups / roll back | `sudo bash /opt/slq-hub/update.sh backups` / `... rollback` |
| Run the self-test again | `sudo bash /opt/slq-hub/update.sh compose exec hub python -m app.cli selftest` |

Settings that belong to the machine (ports, proxy IP, LAN networks, image
versions) are in `/srv/slq-hub/.env`. After editing it, run:

    sudo bash /opt/slq-hub/update.sh compose up -d

## Troubleshooting

| Symptom | What to do |
|---|---|
| `create-vm.sh`: "PCI device already in use" | VM 104 is still running. Shut it down in the Proxmox UI and run the script again. |
| The installer stops at "GPU check" with `/dev/kfd` missing | Reboot the Proxmox host once (RDNA3 reset problem), then run the installer again. |
| The installer stops with "PCI rejects atomics" | The VM must be q35 with `pcie=1` on the GPU's hostpci line. Check `qm config <id>` on the host. |
| "amdgpu-dkms did not build" | `dkms status` and the `make.log` it names. Send them to me. |
| A model download fails | Run the installer again: downloads resume. |
| The self-test fails at "load model" | `sudo bash /opt/slq-hub/update.sh compose logs llm` shows llama-server's own words. |
| The UI works on the LAN but not through the proxy | Check the proxy host's port (8080) and that Websockets support is on. |
