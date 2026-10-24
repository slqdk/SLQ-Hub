# SLQ Hub

A self-hosted assistant in the spirit of Claude, running on one AMD Radeon
RX 7900 XTX in a Proxmox VM — and the single owner of that GPU, so every
other app on the network calls models through the Hub's queue.

- **Web UI**: chats and projects with an agent that searches the web, works
  on project files, runs code in a sandbox (Python, Node, C# - and Windows
  programs it can look at under Wine), sees, generates and edits images,
  speaks text and makes music and sound effects, builds live artifacts, uses
  skills and memory, and can hand hard tasks to a cloud model where you allow
  it.
- **Model API** for other apps (FamilyLog, TwinCAT Coagent, …) in three
  formats — Ollama, OpenAI and Anthropic — all through one priority queue.
- **People**: others in the household get the same chat with their own
  login, at home or also away; their chats stay in their own browser and
  the Hub keeps nothing of them.
- **Chat page for phones** (`/chat`): chat, photos and pictures with an app
  key, no login; the chats stay on the phone.
- **SLQ Sync** for project files, protocol-compatible with SLQSync.exe.
- **Models**: Qwen3.8-27B and Qwen3.6-35B-A3B on llama.cpp (ROCm), Qwen-Image
  2.1 on ComfyUI, Qwen3 embeddings for document libraries; optional sound
  models - Røst v3 and Chatterbox for speech (on the CPU), ACE-Step 1.5 for
  songs and Stable Audio 3 for sound effects (on the card).

| Document | |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Step-by-step: Proxmox VM → running Hub |
| [docs/API.md](docs/API.md) | Connecting other apps |
| [docs/DESIGN.md](docs/DESIGN.md) | Architecture and the decisions behind it |
| [docs/HANDOFF.md](docs/HANDOFF.md) | Current state, what is verified and what is not |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | What changed in each version |

## Development

    cd backend && pip install -r requirements.txt -r requirements-dev.txt
    python -m pytest -q -p no:cacheprovider        # the tests
    cd ../frontend && npm install && npx vite build
    cd ../backend && python -m tests.devserver      # the Hub with fake services on :8080

`tests.devserver` runs the whole app against fake llama-server, ComfyUI,
sandbox, speech and search services, so the UI can be worked on without a GPU
(sign in as `soren` / `correct horse battery`).
