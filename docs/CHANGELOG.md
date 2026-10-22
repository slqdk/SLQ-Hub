# Changelog

## 0.1.4 - 2026-10-22

- `install.sh`: the GPU check rounds the VRAM it reports (a 24 GB card showed
  as 23 GB).

## 0.1.3 - 2026-10-22

- `install.sh`: the GPU check reads `/proc/modules` instead of `lsmod | grep
  -q`. Under `set -o pipefail` that pipe can fail when the match is found
  early (SIGPIPE), so it could report amdgpu as not loaded when it was. The
  same fix for the dkms and PCIe-atomics checks, and in `create-vm.sh`. The
  "not loaded" message now says what to check.

## 0.1.2 - 2026-10-22

- `install.sh` keeps the passed-through card awake: it puts
  `amdgpu.runpm=0` on the VM's kernel command line, and the GPU check says so
  if it is not in effect. The VM check shows the card's power state.
- `docs/INSTALL.md`: a VM made by hand gets Ubuntu installed without the card;
  the card goes in after `amdgpu.runpm=0` is set. Troubleshooting for a VM that
  stops by itself (`kvm run failed Bad address`, `stuck in D3`).

## 0.1.1 - 2026-10-22

- `install.sh` checks the VM before it installs anything: the AMD card is
  there, the machine type is q35, Secure Boot is off, and there is room for
  the models and images (on LVM it prints the command that grows `/`). It
  warns about legacy BIOS and a CPU type without AVX2.
- `docs/INSTALL.md`: how to make the VM by hand in the Proxmox UI and install
  Ubuntu from the ISO, as an alternative to `create-vm.sh`.

## 0.1.0 - 2026-10-22

The first version.

- Install path: `proxmox/create-vm.sh` (a q35/OVMF VM with the GPU moved from
  VM 104), `install.sh` (HWE kernel, amdgpu, Docker, firewall, images, models,
  self-test), `update.sh` (backup and automatic rollback).
- One GPU queue for everything: priorities, aging, same-model grouping, two
  slots per model, leases for agent turns.
- Model API for other apps in three formats (Ollama, OpenAI, Anthropic) with
  per-app keys, LAN guard and CORS.
- The assistant: web search and fetch, files, a sandbox for code, images
  (see, generate, edit, cut out, OCR), artifacts, memory, skills, subagents,
  task lists, MCP servers, compaction, cloud escalation where allowed.
- Projects with revision history and SLQ Sync (works with SLQSync.exe).
- Libraries: search documents by text and by what their pages show.
- Web UI: chats, projects, files, libraries, settings, the GPU plate and
  queue; responsive and installable.
