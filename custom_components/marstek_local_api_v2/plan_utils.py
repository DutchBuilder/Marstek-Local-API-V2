"""Marstek plan computation – shared utility used by sensor and binary_sensor platforms."""
from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant


def compute_plan(
    hass: HomeAssistant,
    is_tomorrow: bool,
    market_entity: str | None,
    energy_tax: float = 0.0,
    procurement_fee: float = 0.0,
    plan_hours: int = 3,
    min_spread: float = 0.062,
    *,
    num_batteries: int = 3,
    max_charge_watts_per_bat: int = 800,
    max_discharge_watts_per_bat: int = 800,
    capacity_kwh: float = 15.36,
) -> dict:
    """
    Compute charge/discharge plan for today or tomorrow.

    Returns a dict with keys:
        strategy, plan_hours, min_spread, spread_ok, min_pair_spread,
        charge_hours, discharge_hours,
        charge_watts_per_bat, discharge_watts_per_bat,
        charge_watts_total, discharge_watts_total,
        charge_kwh, discharge_kwh, buy_cost, save_revenue, profit,
        charge_slots, discharge_slots

    Returns {} when no market price data is available.
    """
    if not market_entity:
        return {}
    market_state = hass.states.get(market_entity)
    if market_state is None:
        return {}

    prices_raw = market_state.attributes.get("prices", [])
    btw = 1.21
    n = max(1, int(plan_hours))

    target_date = (
        datetime.now().date() + timedelta(days=1)
        if is_tomorrow
        else datetime.now().date()
    )

    items: list[dict] = []
    for x in prices_raw:
        try:
            from_val = x["from"]
            st = (from_val if isinstance(from_val, datetime) else datetime.fromisoformat(from_val)).astimezone()
            if st.date() == target_date:
                p = (float(x.get("price", 0)) + energy_tax + procurement_fee) * btw
                items.append({"st": st, "p": p})
        except (KeyError, ValueError, TypeError):
            continue

    if len(items) < 2:
        return {"plan_hours": n, "min_spread": min_spread, "items": []}

    sorted_items = sorted(items, key=lambda x: x["p"])
    cheapest = sorted_items[:n]
    most_expensive = list(reversed(sorted_items[-n:]))

    # Spread viability check
    min_pair_spread = 999.0
    spread_ok = True
    for i, c in enumerate(cheapest):
        if i < len(most_expensive):
            spread = most_expensive[i]["p"] - c["p"]
            if spread < min_pair_spread:
                min_pair_spread = spread
            if spread < min_spread:
                spread_ok = False

    charge_watts_total = max_charge_watts_per_bat * num_batteries
    kwh_per_hour_charge = charge_watts_total / 1000

    if spread_ok:
        strategy = "WINSTGEVEND"
        charge_hours = n
        discharge_hours = n
        discharge_watts_per_bat = max_discharge_watts_per_bat
    else:
        strategy = "EIGEN CONSUMPTIE"
        charge_hours = n
        charge_max = max((c["p"] for c in cheapest), default=0)
        threshold = charge_max + min_spread
        high = sorted(
            [x for x in sorted_items if x["p"] >= threshold],
            key=lambda x: x["p"],
            reverse=True,
        )
        discharge_hours = max(len(high), n * 2)
        discharge_hours = min(discharge_hours, 24)
        charged_kwh = min(n * kwh_per_hour_charge, capacity_kwh)
        needed_w_per_bat = (charged_kwh / discharge_hours * 1000) / num_batteries
        discharge_watts_per_bat = min(int(needed_w_per_bat), max_discharge_watts_per_bat)

    ch2 = sorted_items[:charge_hours]
    dis2 = list(reversed(sorted_items[-discharge_hours:]))
    discharge_watts_total = discharge_watts_per_bat * num_batteries

    charge_total_kwh = min(charge_hours * kwh_per_hour_charge, capacity_kwh)
    discharge_kwh_per_h = discharge_watts_total / 1000
    discharge_total_kwh = min(discharge_hours * discharge_kwh_per_h, charge_total_kwh)

    buy_cost = sum(it["p"] * kwh_per_hour_charge for it in ch2)
    save_rev = sum(it["p"] * discharge_kwh_per_h for it in dis2)
    profit = save_rev - buy_cost

    return {
        "strategy": strategy,
        "plan_hours": n,
        "min_spread": round(min_spread, 3),
        "spread_ok": spread_ok,
        "min_pair_spread": round(min_pair_spread, 3),
        "charge_hours": charge_hours,
        "discharge_hours": discharge_hours,
        "charge_watts_per_bat": max_charge_watts_per_bat,
        "discharge_watts_per_bat": discharge_watts_per_bat,
        "charge_watts_total": charge_watts_total,
        "discharge_watts_total": discharge_watts_total,
        "charge_kwh": round(charge_total_kwh, 2),
        "discharge_kwh": round(discharge_total_kwh, 2),
        "buy_cost": round(buy_cost, 2),
        "save_revenue": round(save_rev, 2),
        "profit": round(profit, 2),
        "charge_slots": [
            {
                "start": it["st"].strftime("%H:%M"),
                "end": (it["st"] + timedelta(minutes=59)).strftime("%H:%M"),
                "price": round(it["p"], 3),
            }
            for it in ch2
        ],
        "discharge_slots": [
            {
                "start": it["st"].strftime("%H:%M"),
                "end": (it["st"] + timedelta(minutes=59)).strftime("%H:%M"),
                "price": round(it["p"], 3),
            }
            for it in dis2
        ],
    }


def is_current_hour_in_slots(slots: list[dict]) -> bool:
    """Return True if the current hour (HH:00) matches any slot's start time."""
    now_hhmm = datetime.now().replace(minute=0, second=0, microsecond=0).strftime("%H:%M")
    return any(slot.get("start") == now_hhmm for slot in slots)
