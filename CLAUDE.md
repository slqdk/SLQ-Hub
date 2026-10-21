# Working on SLQ Hub

Read `docs/DESIGN.md` (decisions) and `docs/HANDOFF.md` (current state) first.

## Ground rules

- Nothing TwinCAT-specific in this repo. Domain knowledge belongs in skills.
- The Hub is the only owner of the GPU. Anything that loads a model or runs
  ComfyUI goes through `backend/app/gpu/scheduler.py` — never call
  llama-server or ComfyUI from a code path that skipped the scheduler.
- Repo docs, UI text and code are English. Søren writes Danish or English.
- Measure before theorising: ask for the log, the screenshot, the output of
  the command, then diagnose. Do not guess at hardware behaviour.
- Never write secrets into the repo. They live in `/srv/slq-hub/.env` on the VM.
- `docs/HANDOFF.md` is the continuity mechanism: keep it current, including
  honest notes on what has NOT been verified on the real hardware.

## Layout

- `proxmox/create-vm.sh` — runs on the Proxmox host.
- `install.sh`, `update.sh` — run inside the VM (root).
- `deploy/` — compose file, env example, SearXNG settings, firewall unit.
- `services/llm|comfyui|sandbox` — the non-Hub container images.
- `backend/` — FastAPI app (`app/`) and tests (`tests/`).
- `frontend/` — React + TypeScript + Vite.

## Checks before every push

    cd backend && python -m pytest -q -p no:cacheprovider
    cd frontend && npx tsc --noEmit && npx vite build
    shellcheck install.sh update.sh proxmox/create-vm.sh deploy/firewall/*.sh

## Versioning

`VERSION` holds the app version (semver: patch = fixes, minor = features).
Bump it and add a `docs/CHANGELOG.md` entry with every user-visible change.
