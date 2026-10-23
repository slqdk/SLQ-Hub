"""People: accounts the owner adds for others in the household (Settings ->
People). A person uses the chat from their own device - their chats and files
are kept there - and may be limited to the home network and to no code."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import auth

router = APIRouter()


def _owner(request: Request) -> None:
    if auth.is_member(getattr(request.state, "user", None)):
        raise HTTPException(403, "Only the owner of the Hub can do that.")


class PersonIn(BaseModel):
    display_name: str = ""
    username: str
    password: str
    away: bool = False                   # also through the proxy (hub.slq.dk), not only at home
    may_run_code: bool = False           # bash in their chats (confined to the chat's temporary folder)


class PersonPatch(BaseModel):
    display_name: str | None = None
    username: str | None = None
    password: str | None = None
    away: bool | None = None
    may_run_code: bool | None = None


@router.get("/hub/api/people")
async def list_people(request: Request):
    _owner(request)
    return auth.list_people()


@router.post("/hub/api/people")
async def add_person(body: PersonIn, request: Request):
    _owner(request)
    try:
        return auth.create_person(body.display_name, body.username, body.password, body.away, body.may_run_code)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.patch("/hub/api/people/{user_id}")
async def change_person(user_id: int, body: PersonPatch, request: Request):
    _owner(request)
    try:
        p = auth.update_person(user_id, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if p is None:
        raise HTTPException(404, "No such person.")
    return p


@router.post("/hub/api/people/{user_id}/sign-out")
async def sign_out(user_id: int, request: Request):
    _owner(request)
    if auth.get_person(user_id) is None:
        raise HTTPException(404, "No such person.")
    return {"sessions": auth.sign_out_everywhere(user_id)}


@router.delete("/hub/api/people/{user_id}")
async def remove_person(user_id: int, request: Request):
    """Their chats are on their own devices; the Hub keeps nothing of theirs but the account."""
    _owner(request)
    if not auth.delete_person(user_id):
        raise HTTPException(404, "No such person.")
    return {"ok": True}
