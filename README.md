# SLQ Hub

A self-hosted assistant in the spirit of Claude, running on one AMD Radeon
RX 7900 XTX in a Proxmox VM — and the single owner of that GPU, so every
other app on the network calls models through the Hub's queue.

- **Web UI**: chats and projects with an agent that searches the web, works
  on project files, runs code in a sandbox, sees, generates and edits images,
  builds live artifacts, uses skills and memory, and can hand hard tasks to a
  cloud model where you allow it.
- **Model API** for other apps (FamilyLog, TwinCAT Coagent, …) in three
  formats — Ollama, OpenAI and Anthropic — all through one priority queue.
- **SLQ Sync** for project files, protocol-compatible with SLQSync.exe.
- **Models**: Qwen3.8-27B and Qwen3.6-35B-A3B on llama.cpp (ROCm), Qwen-Image
  2.1 on ComfyUI, Qwen3 embeddings for document libraries.

| Document | |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Step-by-step: Proxmox VM → running Hub |
| [docs/API.md](docs/API.md) | Connecting other apps |
| [docs/DESIGN.md](docs/DESIGN.md) | Architecture and the decisions behind it |
| [docs/HANDOFF.md](docs/HANDOFF.md) | Current state, what is verified and what is not |
