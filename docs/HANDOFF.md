# Handoff

The state of SLQ Hub for whoever works on it next (a person or Claude).
Read `docs/DESIGN.md` for the decisions; this file says what exists, what
has been checked, and - honestly - what has not.

## State (0.1.2, 2026-10-22)

Everything in the design is built: the install path, the Hub core (scheduler,
llama-server supervisor, the Ollama/OpenAI/Anthropic APIs), the agent harness
with all its tools, projects with SLQ Sync, images through ComfyUI, libraries
(multimodal RAG), MCP, cloud escalation, the web UI (responsive, PWA) and the
command line self-test.

**Nothing has run on the real hardware yet.** The first install on the
Proxmox host is the real test. Expect to fix things there; the self-test
(`python -m app.cli selftest`) is the place to start.

The first install does not use `create-vm.sh`: Søren made VM 222 by hand in
the Proxmox UI (at first SeaBIOS, i440fx, CPU `x86-64-v2-AES`, 34 GiB, no
GPU) and installs Ubuntu 24.04.2 from the ISO. He was given the changes to
make before installing (`docs/INSTALL.md`, "Or: make the VM by hand"). His
`qm config 222` afterwards matches: `machine: q35`, `bios: ovmf` with
`efidisk0 ...,efitype=4m,pre-enrolled-keys=0`, `cpu: host`, 12 cores,
`memory: 40960`, `balloon: 0`, `hostpci0: 0000:0a:00,pcie=1` (no `x-vga`),
default display, `agent: enabled=1`, `onboot: 1`, 530G on `VMDisc01`.
`install.sh` now checks the VM before installing anything (stage `vm`).

First real-hardware observation: starting VM 222 right after VM 104 released
the card (GPU at host address `0000:0a:00.0`) failed with
`error writing '1' to '.../0000:0a:00.0/reset': Inappropriate ioctl for device`
and then `kvm: ../hw/pci/pci.c:1654: pci_irq_handler: Assertion '0 <= irq_num
&& irq_num < PCI_NUM_PINS' failed` - the card no longer answered config reads
(the RDNA3 reset problem the design expected). Advised: reboot the Proxmox
host. After the host reboot (07:49) VM 222 started with the card (07:51) and
showed the Ubuntu installer's language screen. The host journal then shows
(host kernel 6.8.12-37-pve, PVE 8.4.19, AMD CPU):

- 07:51:29 `vfio-pci 0000:0a:00.0: vgaarb: deactivate vga console` - the card
  was the host's boot VGA; `snd_hda_intel 0000:0a:00.1: GPU sound probed, but
  not operational` - the host's sound driver had the card's audio function
  until the VM took it (no early vfio-pci binding).
- 07:51:45 `vfio-pci 0000:0a:00.0: Unsupported reset method 'device_specific'`
  - something on the host writes `device_specific` to the card's
  `reset_method` (vendor-reset style; vendor-reset does not support Navi 31).
  Source not found yet.
- 08:25:44, after ~34 idle minutes at the language screen: `QEMU[4519]: error:
  kvm run failed Bad address` with a register dump: the guest was in amdgpu
  (`mov r12d,[r12]`, R12 = rmmio + 0x15198, R14 = register 0x5466) - a plain
  register read on the card's MMIO BAR that the host could not satisfy
  (EFAULT). QEMU stopped the VM; the console froze on its last frame.
- 08:56:36 (a reset from the UI): `kvm: vfio: Unable to power on device, stuck
  in D3`, then `vfio_bar_restore: reset recovery - restoring BARs` for both
  functions. `qm status` afterwards: `status: running`, `qmpstatus: prelaunch`
  (reset after an internal error leaves QEMU paused in prelaunch, so the tree
  shows no "running" overlay).

Working explanation, not proven: the guest's amdgpu runtime power
management (BACO is on by default for Navi 31) put the idle card into D3, and
the card did not come back; the read at 08:25 was the next access. The
alternative is QEMU issue #2574 ("kvm run failed Bad address" with Navi 31
and guest kernels 6.7+, no cause found there); the installer's kernel is 6.8
with the in-box amdgpu, while the Hub runs kernel 7.0 with amdgpu-dkms 31.60.
Precaution taken: `amdgpu.runpm=0` (written by `install.sh`, and by hand
before the card goes back in), and Ubuntu is installed without the card. Asked
Søren for the host's passthrough setup. What it showed:

- The card: `0a:00.0` `[1002:744c]` Sapphire NITRO+ RX 7900 XTX Vapor-X
  `[1da2:e471]` (rev c8), `0a:00.1` HDMI audio `[1002:ab30]`; no other
  functions.
- Host command line: `BOOT_IMAGE=/boot/vmlinuz-6.8.12-37-pve
  root=/dev/mapper/pve-root ro quiet iommu=pt` (GRUB, LVM root).
- `/etc/modprobe.d/vfio.conf`: `options vfio-pci ids=1002:73bf,1002:ab28` -
  the IDs of a Navi 21 card (RX 6800/6900), **not** this one - plus softdeps
  for radeon, amdgpu and snd_hda_intel. amdgpu and radeon are blacklisted.
  So nothing bound the 7900 XTX to vfio-pci at boot: the host's sound driver
  took its audio function and the card was the host's boot console until a
  VM started.
- `/etc/systemd/system/vreset.service` writes `device_specific` (the
  vendor-reset setup, probably for the earlier card).
- `qm config 104` bottom half: 35000 MB, 470G on `VMDisc01`, onboot 0; the top
  (bios, machine, cpu, hostpci, args) was cut off - asked for it again.

Proposed host changes, in the reboot that puts the card back into VM 222:
`options vfio-pci ids=1002:744c,1002:ab30,1002:73bf,1002:ab28
disable_idle_d3=1` (the old IDs kept in case that card is still in the
machine; `disable_idle_d3` because early binding would otherwise park the
unused card in D3), `vfio vfio_iommu_type1 vfio_pci` in `/etc/modules`,
`systemctl disable vreset.service`, `update-initramfs -u -k all`. Check after
the reboot: both functions on vfio-pci before anything else, `Y` in
`/sys/module/vfio_pci/parameters/disable_idle_d3`, no `snd_hda_intel
0000:0a` or `device_specific` lines in the journal.

Applied at 10:27 (the host reboot still to come): `vfio.conf` now reads
`options vfio-pci ids=1002:744c,1002:ab30,1002:73bf,1002:ab28
disable_idle_d3=1` plus the three softdeps; `vreset.service` (its whole job:
`ExecStart=/usr/bin/bash -c 'echo device_specific >
/sys/bus/pci/devices/0000:0a:00.0/reset_method'`, `After=multi-user.target`)
disabled; initramfs rebuilt for every installed kernel (6.8.12-37/-15/-11/-2
and two old 5.15 kernels; GRUB host, so "No /etc/kernel/proxmox-boot-uuids
found" is expected); `hostpci0: 0000:0a:00,pcie=1` back on VM 222. The
after-reboot checks are not done yet.
Whether a plain reboot of VM 222 (which `install.sh` does once) hits the
reset problem is not known yet.

## What has been checked, and how

| Area | Checked by |
|---|---|
| Scheduler | 14 unit tests: priorities, aging, slots, affinity, leases with in-lease switching (two leases that both want ComfyUI, a full residency), cancellation, preload, a crashed model server noticed and loaded again. |
| Model APIs | 23 tests against a fake llama-server: Ollama (chat, generate, show, tags, embed, key in the path), OpenAI (chat, stream, tools, images), Anthropic (messages, stream, tools, thinking), LAN guard, CORS, key priority. |
| Agent | 22 tests: plain turns, tools in chat and project folders, revisions, stopping (in a tool and while writing), retry/edit, SSE replay, attachments, image generation switching the card and back, MCP over HTTP (approval, always-allow, masked secrets) and over stdio in the sandbox, escalation (explicit and automatic), subagents, compaction, skills, memory, artifacts. |
| SLQ Sync | 8 tests (one: writes never follow a symlink planted in the folder), including a round trip with the real `slq_sync.py` client from the SLQ Companion repo against a live server (clone, both-way edits, a conflict, its release). Skipped when that file is not next to this repo. |
| Libraries | 5 tests with keyword-hashing fake embedders: text and page-image indexing, fused search, re-ranking, replacing and deleting documents, GPU indexing through the queue, broken files. |
| Web UI | `tsc` + `vite build`; every page and the main flows clicked through in Chromium (Playwright) against `python -m tests.devserver` (the Hub with fake services): sign-in, chat with markdown/maths/code, tool strip, artifact panel, approvals, subagent, image tool, projects (files, editor, settings, sync), libraries, all settings tabs, dark mode, phone width. |
| Shell scripts | `shellcheck`. `create-vm.sh --dry-run` prints every command. `install.sh`'s VM check: 12 cases against fake `/sys` and `/proc` trees (card present or not, its audio function alone, a card asleep in D3hot, i440fx, bare metal, Secure Boot on, legacy BIOS, no AVX2, low space with and without LVM), and once against a real sysfs (not a Proxmox VM). The `amdgpu.runpm=0` GRUB drop-in, sourced the way `grub-mkconfig` does, appends to the existing command line. The `qm config ... hostpci` one-liner in INSTALL.md against a fake `qm`. |
| Compose | `docker compose config` (v2.40.3) with a `.env` written the way `install.sh` writes it: renders, and the admin password hash arrives intact. |
| llama.cpp | b11783 built from source (CPU): every command line the Hub builds parses; the real supervisor started the real llama-server through the Hub's scheduler, runner and client with a tiny random model - load, streaming (the real chunk format), a killed server noticed and reloaded, unload. Embeddings (strings and `content` arrays) and `/v1/rerank` answer in the shapes the Hub expects. |
| ComfyUI | v0.42.3 on CPU with placeholder model files: all seven workflow graphs (Qwen-Image 2.1 generate, turbo, edit with 1 and 3 images, background removal, FLUX.2 klein generate and edit) pass ComfyUI's own validation. Not rendered (no real weights). |
| MCP over stdio | A real stdio MCP server through the real sandbox daemon (`execd.py`): initialize, a server-to-client ping, tools, an 18 KB result. |

## Not verified - check these on the real system

1. **create-vm.sh** on the real Proxmox host: storage names, the hostpci copy
   from VM 104, cloud-init snippets on `local`, the guest agent IP wait. (The
   first VM was made by hand, so this is still untested.) Likewise the VM
   check in `install.sh` on a real q35/OVMF VM with the card: the sysfs paths
   it reads (PCI vendor/class, host bridge id, the `SecureBoot` EFI variable).
2. **install.sh** end to end on Ubuntu 24.04: HWE kernel 7.0, amdgpu-dkms 31.60
   building against it, `/dev/kfd`, PCIe atomics, Docker CE, the firewall unit.
3. **llama.cpp on the card** (`server-rocm-b11783`): the flags parse (checked
   on a CPU build), but VRAM use (~21 GiB expected for Qwen3.8-27B with an 80K
   pool), ROCm itself and real tokens/s are unknown until the first load.
4. **Qwen3.8 templates**: tool calls parsed by llama-server (`--jinja`),
   `reasoning_content` passed back within a turn, `reasoning_effort`
   (low/medium/xhigh), `enable_thinking`. The Hub also parses text tool calls
   (Qwen3-Coder XML and Hermes JSON) as a fallback.
5. **ComfyUI on the card**: the torch ROCm 7.14 wheels, the Qwen-Image 2.1 int8
   kernels on gfx1100, a real render, VRAM release with `/free` before the LLM
   loads again. (The graphs themselves pass ComfyUI's validation.)
6. **Embeddings and re-ranking** with the real GGUFs: the Qwen3-VL-Embedding
   chat format written out in `app/rag/library.py`, the re-ranker's score scale
   (log-odds or probability: both handled).
7. **Sandbox isolation** on the VM: the self-test checks that the sandbox cannot
   reach the Hub or the proxy, and can reach the internet.
8. **Nginx Proxy Manager**: SSE streaming (`proxy_buffering off`), uploads
   (`client_max_body_size`), the session cookie behind HTTPS.
9. **PWA** install on a phone through hub.slq.dk.
10. **FamilyLog** against the Hub's Ollama API with its real settings, and
    TwinCAT Coagent (format still unknown; all three formats and CORS exist).

## Where things are

| Path | What |
|---|---|
| `backend/app/gpu/scheduler.py` | The queue. Everything that uses the card goes through `acquire()`. |
| `backend/app/gpu/backend.py`, `runner.py`, `aux.py` | Loading/unloading on the card; the llama-server supervisor client; CPU helper servers. |
| `backend/app/api/` | Ollama, OpenAI, Anthropic formats. |
| `backend/app/agent/loop.py` | The agent loop (`Engine`) and turns (`Agent`). |
| `backend/app/agent/tools/` | The tools; `meta_tools.py` has `task` and `escalate`; `mcp.py` the MCP client. |
| `backend/app/projects/` | Projects, folders, the sync store and the `/api/sync` protocol. |
| `backend/app/images/` | ComfyUI workflows and the image pipeline. |
| `backend/app/rag/` | Libraries: extraction, indexing, search, the `library_search` tool. |
| `backend/app/cli.py` | `selftest`, `set-password`, `create-key`, `keys`. |
| `frontend/src/` | The UI: `views/` (chat, project, libraries, settings), `components/`, `lib/turns.ts` (the turn view model for history and live runs). |
| `services/` | The llm supervisor, the ComfyUI image, the sandbox daemon. |

## Working on it

    # backend tests (Python 3.12+)
    cd backend && pip install -r requirements.txt -r requirements-dev.txt
    python -m pytest -q -p no:cacheprovider

    # the Hub with fake services, for UI work (soren / correct horse battery)
    cd frontend && npm install && npx vite build
    cd ../backend && python -m tests.devserver --port 8080
    #   or run `npm run dev` in frontend/ (port 5173, proxies to :8080)

    # checks before a push
    cd frontend && npx tsc --noEmit && npx vite build
    shellcheck install.sh update.sh proxmox/create-vm.sh deploy/firewall/*.sh

## Known limits (by design, for now)

- One user (the admin). API keys for apps.
- Tool calls in a turn run one after another, not in parallel.
- Approvals wait up to ten minutes; the turn keeps its lease meanwhile, so a
  request for another model waits too.
- The run's live events are kept in memory for an hour; the stored messages
  are permanent.
- No custom ComfyUI workflows yet; no speech or video.
