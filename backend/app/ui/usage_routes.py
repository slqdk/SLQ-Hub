"""Settings -> Usage (0.24.0): who used how much of the card - answers, tokens, energy, cost - and the card's
power now. The owner's only."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import auth, usage
from ..state import get_hub

router = APIRouter()

ORDER = {"owner": 0, "person": 1, "app": 2, "tests": 3, "library": 4, "system": 5, "idle": 6, "other": 7}


@router.get("/hub/api/usage")
async def get_usage(request: Request, period: str = "month"):
    hub = get_hub(request)
    if period not in usage.PERIODS:
        raise HTTPException(400, f"period is one of {', '.join(usage.PERIODS)}")
    meter = hub.extras.get("energy")
    if meter is not None:
        await meter.sample()                           # the reading now, not the idle one of up to a minute ago
    found = {r["who"]: r for r in usage.totals(period)}
    for who in ["owner", *(f"user:{p['id']}" for p in auth.list_people())]:
        found.setdefault(who, {"who": who, **{f: 0 for f in usage.FIELDS}, "since": None})   # everyone, used or not
    rows = []
    for r in found.values():
        name, kind = usage.label(hub, r["who"])
        rows.append({**r, "label": name, "kind": kind})
    rows.sort(key=lambda r: (ORDER.get(r["kind"], 9), r["label"].lower()))
    power = None
    if meter is not None and meter.power is not None:
        power = {"now_w": meter.power, "cap_w": meter.cap, "source": meter.source}
    return {"period": period, "since": usage.first_day(period), "price_per_kwh": usage.price(hub),
            "other_watts": float(hub.settings.get("costs", "other_watts") or 0), "power": power, "rows": rows}
