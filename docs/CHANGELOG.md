# Changelog

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
