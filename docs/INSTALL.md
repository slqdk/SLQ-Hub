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

    curl -fsSL https://raw.githubusercontent.com/slqdk/SLQ-Hub/main/proxmox/create-vm.sh -o /root/create-vm.sh

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

### Or: make the VM by hand

A VM made in the Proxmox UI and installed from the Ubuntu Server ISO works
too, if it matches what the script makes. Set this up **before** installing
Ubuntu: the BIOS type cannot be changed afterwards without reinstalling.

| Setting | Value | Why |
|---|---|---|
| BIOS | OVMF (UEFI), EFI disk with **Pre-Enroll keys off** | Secure Boot off: AMD's driver module is not signed |
| Machine | `q35` | PCI Express for the card; ROCm needs its atomics |
| Processors | type **host**, 8–12 cores | AVX2 and the rest of the real CPU |
| Memory | 40960 MB, ballooning off | passthrough locks all of it anyway |
| Disk | 500 GB, VirtIO SCSI single | models and images need ~150 GB to begin with |
| Display | Default | the Proxmox console |
| PCI device | the RX 7900 XTX: All Functions, ROM-Bar, PCI-Express, **not** Primary GPU - **added after Ubuntu is installed** (below) | |
| Options | Start at boot on, QEMU Guest Agent on | |

Install Ubuntu **without the card**. On the first install the VM stopped
while the installer sat idle with the card: the card stopped answering and
was then stuck in D3, its power-saving state (see Troubleshooting). The likely
cause is the GPU driver putting the idle card to sleep, so the card goes in
once the installed system is told to keep it awake.

For a VM that already exists, on the host (here VM 222 with its disks on
`VMDisc01`):

    qm stop 222
    qm set 222 --machine q35 --bios ovmf --efidisk0 VMDisc01:1,efitype=4m,pre-enrolled-keys=0 --cpu host --memory 40960 --balloon 0 --agent enabled=1 --onboot 1
    qm config 222

In the Ubuntu Server 24.04 installer:

1. Language English, your keyboard.
2. **Ubuntu Server** (not minimized). No third-party drivers.
3. Network: a static IPv4 address (the one Nginx Proxy Manager will forward to).
4. Storage: **Use an entire disk**, and **untick "Set up this disk as an LVM
   group"**. With LVM the installer gives `/` only part of the disk. (Already
   installed with LVM? `install.sh` notices and prints the command that grows it.)
5. Your user, and tick **Install OpenSSH server**.
6. Featured snaps: none. Not `docker` in particular: `install.sh` installs
   Docker CE itself.

After the reboot, log in over SSH, get the repo, and tell the kernel to keep
the card awake (`amdgpu.runpm=0`; `install.sh` writes the same file later):

    sudo apt update && sudo apt install -y git qemu-guest-agent
    sudo git clone https://github.com/slqdk/SLQ-Hub /opt/slq-hub
    sudo mkdir -p /etc/default/grub.d && echo 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT amdgpu.runpm=0"' | sudo tee /etc/default/grub.d/90-slq-hub.cfg && sudo update-grub
    sudo poweroff

Now move the card, on the host. This shuts VM 104 down, keeps it from
starting at boot, and copies its GPU lines without `x-vga`:

    qm shutdown 104 --timeout 180 --forceStop 1
    qm set 104 --onboot 0
    qm config 104 | grep '^hostpci' | while read -r key val; do val="$(sed -E 's/(^|,)x-vga=(1|on)//; s/^,//' <<<"$val")"; [[ $val == *pcie=1* ]] || val="$val,pcie=1"; qm set 222 --"${key%:}" "$val"; done
    qm start 222

If the start fails with a reset error (`Inappropriate ioctl for device`, then
a `pci_irq_handler` assertion), reboot the Proxmox host: the card did not
reset after VM 104. Then continue in step 2 at "Then start the installer".
The installer checks the VM first (the card, `q35`, Secure Boot, disk space)
and stops with what to fix.

## 2. Install (in the new VM)

(A VM made by hand: you have cloned the repo already; skip to "Then start the
installer".)

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

Under **Advanced** (long answers stream, and uploads can be large - the
largest is a LoRA file of up to 3 GB):

    client_max_body_size 3200m;
    proxy_request_buffering off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_buffering off;

Without it the proxy refuses a big upload with its own page, "413 Request
Entity Too Large". `proxy_request_buffering off` passes an upload straight
on, instead of storing it on the proxy's disk first.

The model API (`/v1`, `/api`) is refused when it arrives through the proxy
from outside. That is deliberate: apps use the LAN address. A key set to
**At home and away** (Settings → Apps and keys) is the exception - for a
person's phone, so the chat page works at `https://hub.slq.dk/chat` from
anywhere; the key dialog's phone link then uses `HUB_PUBLIC_URL` from
`/srv/slq-hub/.env` (`install.sh` asks for it). The Advanced block above
matters for that page: long answers stream, and a picture can take a minute.

SLQ Sync (`/api/sync`) works through the proxy with a device token, so PCs
outside the LAN can sync.

## 5. Connect FamilyLog

1. In the Hub: **Settings → Apps and keys → New key**. Use the name `familylog`
   and priority `normal`, then copy the key (it is shown once). The page also
   shows the exact addresses to use.
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

## 6. People in the household

Give someone their own login: **Settings → People → Add a person**.

1. Their name (what the assistant calls them), a user name and a password
   (at least 10 characters; they can change it themselves).
2. **Where they can sign in**: at home (the LAN and WireGuard), or at home
   and away (also `https://hub.slq.dk`).
3. **May run code**: off by default. On, the assistant may run bash and
   Python in their chats, inside each answer's temporary folder - only where
   the self-test's "sandbox for people" line passes.

They sign in at the Hub's address, on a phone ideally from the home screen
(iPhone: Safari → Share → **Add to Home Screen**; Android: Chrome → ⋮ →
**Add to Home screen**). They get the same chat as you - models, pictures,
thinking, web search, attachments, artifacts - but none of your chats,
projects, libraries, memory or settings. Their chats are kept in their
browser, not on the Hub: each device has its own, and **Export all chats** /
**Import chats** in their account menu moves them. In the list: New password,
Sign out everywhere, Remove.

## 7. The phone chat page (with a key)

The Hub also has a chat page made for phones, `http://30.11.0.140:8080/chat`,
for anyone without a login: plain chat, photos and pictures, no agent. It
uses the model API with its own key, so it works only on the home network
(or away, with a key set to At home and away) and cannot see your chats,
projects or settings. Its chats are kept on the phone.

1. In the Hub: **Settings → Apps and keys → New key**. Name it after the
   person, choose **Interactive**, press **Only the home network** and make
   the key.
2. Press **Copy the phone link** and send the link to the phone (Messages,
   mail).
3. On the phone, on the home Wi-Fi, open the link: the chat opens.
4. Add it to the home screen: on an iPhone Safari → Share → **Add to Home
   Screen**, on Android Chrome → ⋮ → **Add to Home screen**. If the page asks
   for the key when opened from the home screen, paste the link there.

Switch the key off or delete it under Apps and keys to stop it.

Phone apps work too, each with its own key: for example Enchanted (iPhone,
an Ollama app) with the server `http://30.11.0.140:11434` and the key as
**Bearer Token**.

## 8. Retire VM 104 later

VM 104 stays as a fallback: to go back, shut the Hub VM down and start 104.
When the Hub has run well for a while, delete VM 104 on the host:

    qm destroy 104

## 9. Sound: speech, music and sound effects

The assistant makes audio files - an announcement, a song, a sound effect -
once a model for it is downloaded: **Settings → Models → Sound, music and
speech**.

| For | Model | Download | Runs on |
|---|---|---|---|
| Danish speech | Røst v3 Turbo (or Røst v3: more natural to CoRal's listeners, 1.5 times slower) | 3.0 GB (3.2 GB) | the CPU |
| Speech in other languages | Chatterbox Multilingual V3 (shares 1 GB with Røst v3) | 3.2 GB | the CPU |
| Songs and instrumentals | ACE-Step 1.5 XL Turbo (or ACE-Step 1.5 Turbo: half the size) | 19.9 GB (10.0 GB) | the card |
| Sound effects, ambience, loops | Stable Audio 3 Medium | 10.4 GB | the card |

Speech runs in its own container on the CPU, so the chat model stays loaded;
music and sound effects switch the card to ComfyUI, as pictures do. Then ask
in a chat - "Lav en speak der siger tredive sekunder tilbage af tredje
periode", "A two-minute club song with these lyrics: ...", "An ice hockey
arena horn". **Settings → Assistant → Sound** chooses the model for each kind.

After downloading a voice model, check it and see how fast it speaks on this
machine:

    sudo bash /opt/slq-hub/update.sh compose exec hub python -m app.cli selftest --speech

It speaks the same sentence with each voice model and shows how long each
took. Then open **Settings → Assistant → Sound**: under Speech samples each
one plays, with its real-time factor (seconds of work per second of speech).
Choose the **Voice model for Danish** by ear - on VM 222 Røst v3 Turbo
sounded best and is the quicker one (about 4 seconds of work per second of
speech), and it is what Danish gets when nothing is chosen. Chatterbox
Multilingual V3 speaks Danish with an English accent; keep it for other
languages.

The files are in `/srv/slq-hub/data/selftest/` on the VM too. To copy them to
your PC (PowerShell; the last argument is where they go):

    scp "adminsorenlyder@30.11.0.222:/srv/slq-hub/data/selftest/*.mp3" "$HOME\Downloads"

The speech container uses all CPU cores but two. `SPEECH_THREADS` and
`SPEECH_MEM` in `.env` change that (then `sudo bash /opt/slq-hub/update.sh
compose up -d`).

## 10. The assistant and the internet

By default the assistant searches the web, reads pages and runs code with
internet access (`pip install`, `git clone`) when a task needs it - never the
LAN. To have it ask first: **Settings → Assistant → Web → Internet for the
assistant → Ask first**, then Save changes.

Asking first, the chat stops at the step and asks, with what it wants to
search, the page it wants to read or the command it wants to run:

- **Allow once** - this call only.
- **Allow in this chat** - the same kind of call in this chat without asking
  again (since 0.16.0; the chat's ⋯ menu → Allowed in this chat lists it, with
  Take back).
- **Always allow web search** / **Always allow www.dr.dk** (per site) /
  **Always allow internet for code** - from now on without asking.
- **Deny** - the assistant hears it was declined and goes on without it.

While a question waits, the card is free for other work (pictures, other
chats); after your answer the chat may wait its turn for a moment. Web pages
and apps the assistant makes as artifacts load nothing from the web while it
asks first (since 0.16.0; reload the page after switching to Ask first).

Code runs without the network unless the assistant says the command needs
it, which asks (the self-test's "offline code" line shows that this works on
the VM). A page that redirects to another site asks again, and pictures from
the web in answers show as links until you open them. What you always allow
is listed under **Always allowed online** in the same section; **Ask again**
takes it back. People are asked in their own chats, once each time.

The model containers (llm, comfyui, speech) have no way out at all since
0.15.0: the VM's firewall stops them from connecting to the internet, the
LAN or the VM. They need nothing outside - the Hub downloads the models.

**Once, after updating to 0.15.0** (the update itself runs the old updater,
which does not know the new rules yet):

    sudo bash /opt/slq-hub/update.sh firewall

It installs the new firewall script, applies it and tries each model
container's way out; all three should say "no way out". From the next update
on, this happens by itself.

## 11. Windows programs (C#)

The assistant writes C# in the sandbox (the .NET 10 SDK) and, since 0.16.0,
looks at Windows Forms and WPF programs it builds: they run under Wine on a
virtual screen in the sandbox, and it gets screenshots, clicks and types into
them (`windows_app`). Nothing to set up - the update to 0.16.0 builds it into
the sandbox image (about 1 GB more; downloads from the Ubuntu archive and
conda-forge). The self-test's "windows programs" line checks it: a small
program built offline and run, its button found and clicked.

What Wine shows is close to Windows, not the same (fonts, some controls). To
use a program, ask for it "published": the folder with its .exe goes into the
chat's (or project's) files; it runs on a PC with the .NET 10 Desktop Runtime.

## 12. Windows in a container (optional)

For Windows-only tools the Hub should use - an MCP server on Windows, say
(Settings → MCP servers) - the VM can run Windows itself, in a container
(dockur/windows: a Windows VM on the VM's KVM). Off unless you turn it on.

1. Nested virtualisation: on the Proxmox host
   `cat /sys/module/kvm_amd/parameters/nested` gives `1`, and the Hub VM's CPU
   type is **host**. In the VM, `ls /dev/kvm` must exist (stop and start the
   VM after changing the CPU type).
2. Room: Windows takes 8 GB of the VM's RAM and up to 64 GB of disk (plus the
   ISO, about 5 GB) under `/srv/slq-hub/windows/storage` - not in the backups.
3. In `/srv/slq-hub/.env`: `COMPOSE_PROFILES=windows`, and if you like
   `WINDOWS_VERSION` (`10l` Windows 10 LTSC is the default, `11l` Windows 11
   LTSC), `WINDOWS_USER` and `WINDOWS_PASSWORD`. Without a password the next
   step writes a random one into `.env` and shows it.
4. `sudo bash update.sh compose up -d windows`. The first start downloads and
   installs Windows by itself (20-30 minutes; follow it with
   `sudo bash update.sh compose logs -f windows`, or the web viewer through
   `ssh -L 8006:127.0.0.1:8006 <vm>` and http://localhost:8006).
5. Connect with Remote Desktop to the VM's address (port 3389), as
   `WINDOWS_USER` with `WINDOWS_PASSWORD`. Activate Windows with your own key.

Windows is on the sandbox's network under the sandbox's firewall rules: it
reaches the internet (updates, downloads), not the LAN, the other containers
or the VM. The Hub reaches it at `172.31.250.30` - an MCP server in Windows on
port 8765 is `http://172.31.250.30:8765/mcp` in Settings → MCP servers.
Files: `/srv/slq-hub/windows/shared` is the Shared folder (Z:) in Windows,
and `/srv/slq-hub/windows/oem` is copied to `C:\OEM` at the installation - an
`install.bat` there runs at its end (put it there before the first start).
Stop it with `sudo bash update.sh compose stop windows`; it shuts down properly.

## Daily operation

All commands run in the VM from `/opt/slq-hub`:

| What | Command |
|---|---|
| Update (backup + automatic rollback) | `sudo bash /opt/slq-hub/update.sh` |
| Update without waiting until nobody uses the Hub | `sudo bash /opt/slq-hub/update.sh --now` |
| Follow an update after its window was closed | `sudo tail -f /srv/slq-hub/update.log` |
| Stop an update that still builds or waits | `sudo bash /opt/slq-hub/update.sh cancel` |
| Status (containers, health, firewall, the model containers' way out) | `sudo bash /opt/slq-hub/update.sh status` |
| Install and apply this version's firewall | `sudo bash /opt/slq-hub/update.sh firewall` |
| Follow the Hub's log | `sudo bash /opt/slq-hub/update.sh compose logs -f hub` |
| A model server's log | `sudo bash /opt/slq-hub/update.sh compose logs -f llm` |
| The speech service's log | `sudo bash /opt/slq-hub/update.sh compose logs -f speech` |
| Backups / roll back | `sudo bash /opt/slq-hub/update.sh backups` / `... rollback` |
| Run the self-test again | `sudo bash /opt/slq-hub/update.sh compose exec hub python -m app.cli selftest` |
| More language models | Settings → Models → Add from Hugging Face (GGUF repositories; gated ones need `HF_TOKEN` in `.env`) |
| More picture models | Settings → Models → Add image model (an SDXL checkpoint on Civitai or Hugging Face; Civitai files for signed-in users need `CIVITAI_TOKEN` in `.env`) |
| A model from your computer | Settings → Models → Upload (a .gguf language model or an SDXL checkpoint), Upload on a listed model that is not downloaded (its files), or Base model files on a klein training. Sent in 32 MiB pieces, so the proxy's body limit does not apply and an interrupted upload goes on when the same file is chosen again |
| FLUX.2 klein 9B (the picture model, and LoRA training on it) | Gated on Hugging Face: sign in there, accept the licence on huggingface.co/black-forest-labs/FLUX.2-klein-9b-fp8 (the picture model) and huggingface.co/black-forest-labs/FLUX.2-klein-base-9B (training), create a read token and put it in `.env` as `HF_TOKEN=...`, then `update.sh compose up -d` |

Settings that belong to the machine (ports, proxy IP, LAN networks, image
versions) are in `/srv/slq-hub/.env`. After editing it, run:

    sudo bash /opt/slq-hub/update.sh compose up -d

## Troubleshooting

| Symptom | What to do |
|---|---|
| `create-vm.sh`: "PCI device already in use" | VM 104 is still running. Shut it down in the Proxmox UI and run the script again. |
| The installer stops at "VM check" | It says what to change (the card, `q35`, Secure Boot, disk space). Fix it and run the installer again. |
| The VM stops by itself: the console freezes, Proxmox no longer shows it as running (`qm status 222 --verbose` says `internal-error` or `prelaunch`), and the host log (`journalctl -b \| grep QEMU`) has `kvm run failed Bad address` or `stuck in D3` | The card went to sleep and did not wake up. Stop the VM and reboot the host. In the VM, `cat /proc/cmdline` must show `amdgpu.runpm=0`. |
| The installer stops at "GPU check" with `/dev/kfd` missing | Reboot the Proxmox host once (RDNA3 reset problem), then run the installer again. |
| The installer stops with "PCI rejects atomics" | The VM must be q35 with `pcie=1` on the GPU's hostpci line. Check `qm config <id>` on the host. |
| "amdgpu-dkms did not build" | `dkms status` and the `make.log` it names. Send them to me. |
| A model download fails | Run the installer again: downloads resume. |
| The self-test fails at "load model" | `sudo bash /opt/slq-hub/update.sh compose logs llm` shows llama-server's own words. |
| Speech fails with "the speech service is not answering" | `sudo bash /opt/slq-hub/update.sh compose ps speech` and `... compose logs speech`. |
| `update.sh status` says a model container "reaches the internet" | Its firewall rule is missing: `sudo bash /opt/slq-hub/update.sh firewall`. |
| A code step says it "ran without the network" | Internet for the assistant is set to Ask first; the assistant can ask to run it with internet access. |
| The self-test's "offline code" line fails | Keep Internet for the assistant on Allowed and send me the line; asking first then cannot keep code offline. |
| An update stops at "the build failed" | Nothing was changed; the build's output just above that line says why. Send it to me. |
| The UI works on the LAN but not through the proxy | Check the proxy host's port (8080) and that Websockets support is on. |
| A chat behaves strangely (loops, odd answers, stops) | The chat's ⋯ menu → **Export for troubleshooting**, and send me the ZIP. On the phone page: menu → **Export this chat**. |
