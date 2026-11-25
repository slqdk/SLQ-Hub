"""A project's repository (0.84.0, repo.py): link, unlink, sync, status. The owner's alone - the gate lets a person's
login reach no project route."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..state import get_hub
from .repo import RepoError, Repos

router = APIRouter()


def _repos(request: Request) -> Repos:
    return get_hub(request).extras["repos"]


class LinkIn(BaseModel):
    repo: str


@router.get("/hub/api/projects/{pid}/repo")
async def repo_view(pid: int, request: Request):
    from . import store
    if store.get_project(pid) is None:
        raise HTTPException(404, "No such project.")
    return _repos(request).view(pid)


@router.put("/hub/api/projects/{pid}/repo")
async def repo_link(pid: int, body: LinkIn, request: Request):
    try:
        return await _repos(request).link(pid, body.repo)
    except RepoError as exc:
        raise HTTPException(exc.status, str(exc))


@router.delete("/hub/api/projects/{pid}/repo")
async def repo_unlink(pid: int, request: Request):
    try:
        return await _repos(request).unlink(pid)
    except RepoError as exc:
        raise HTTPException(exc.status, str(exc))


@router.post("/hub/api/projects/{pid}/repo/sync")
async def repo_sync(pid: int, request: Request, force: bool = False):
    """Sync now - the Sync button (force: deleting many files too), and the project page when it opens. While a chat
    of the project works it waits for the answer to end (409 says so)."""
    repos = _repos(request)
    if repos.busy(pid):
        repos.arm(pid, "", 1.0)
        raise HTTPException(409, "A chat of the project is working on an answer - the folder syncs when it is done.")
    try:
        await repos.sync(pid, force=force)
    except RepoError as exc:
        raise HTTPException(exc.status, str(exc))
    return repos.view(pid)
