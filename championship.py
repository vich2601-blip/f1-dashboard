"""
Championship mathematics — who can still win, and what it takes to clinch.

Pure functions over standings + schedule rows, so they are trivially
testable and independent of where the data came from.
"""

from datetime import datetime, timezone

# Current regulations (2025+): no fastest-lap point.
GP_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
SPRINT_POINTS = [8, 7, 6, 5, 4, 3, 2, 1]


def _race_dt(race):
    try:
        t = race.get("time") or "14:00:00Z"
        dt = datetime.fromisoformat(f"{race['date']}T{t}".replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (KeyError, ValueError, TypeError):
        return None


def remaining_events(schedule, now=None):
    """Grands Prix that have not started yet, in calendar order."""
    now = now or datetime.now(timezone.utc)
    return [r for r in schedule if (_race_dt(r) or now) > now]


def _event_max(race, top_n):
    """Most points `top_n` cars from one entrant can score at an event
    (1 for drivers, 2 for a constructor's pair)."""
    gp = sum(GP_POINTS[:top_n])
    sprint = sum(SPRINT_POINTS[:top_n]) if race.get("has_sprint") else 0
    return gp + sprint


def title_fight(standings, schedule, name_key, top_n=1, now=None):
    """Analyse one championship.

    Returns a dict describing the leader, every contender's ceiling, and
    the clinch condition at the next event. `top_n` is 1 for the drivers'
    title and 2 for constructors (both cars can score)."""
    standings = [s for s in standings if s.get(name_key)]
    if len(standings) < 2:
        return None

    remaining = remaining_events(schedule, now)
    max_left = sum(_event_max(r, top_n) for r in remaining)
    leader, runner_up = standings[0], standings[1]
    lead_pts = float(leader.get("points") or 0)
    gap = lead_pts - float(runner_up.get("points") or 0)

    contenders = []
    for s in standings[:8]:
        pts = float(s.get("points") or 0)
        ceiling = pts + max_left
        contenders.append({
            "name": s.get(name_key),
            "full_name": s.get("driver_name") or s.get(name_key),
            "team": s.get("constructor", ""),
            "points": pts,
            "ceiling": ceiling,
            "behind": lead_pts - pts,
            # Equal on points goes to countback, so a tie is still "alive".
            "alive": s is leader or ceiling >= lead_pts,
        })
    alive = [c for c in contenders if c["alive"]]

    clinch = None
    decided = gap > max_left
    if not decided and remaining:
        nxt = remaining[0]
        after_next = max_left - _event_max(nxt, top_n)
        # Clinched after the next event iff the lead then exceeds
        # everything still available.
        need_lead = after_next + 1
        clinch = {
            "event": nxt.get("race_name", "next round"),
            "lead_needed": need_lead,
            "outscore_by": max(0, need_lead - int(gap)),
            # When the lead already suffices, how much the rival must gain
            # at this event to keep the title alive.
            "rival_must_gain": max(0, int(gap) - need_lead + 1),
            "possible": need_lead - gap <= _event_max(nxt, top_n),
        }

    scale = max((c["ceiling"] for c in contenders), default=1) or 1
    for c in contenders:
        c["pct_points"] = round(c["points"] / scale * 100, 2)
        c["pct_ceiling"] = round(c["ceiling"] / scale * 100, 2)
    return {
        "leader": contenders[0],
        "runner_up": contenders[1],
        "gap": gap,
        "max_left": max_left,
        "events_left": len(remaining),
        "sprints_left": sum(1 for r in remaining if r.get("has_sprint")),
        "decided": decided,
        "alive_count": len(alive),
        "contenders": contenders,
        "clinch": clinch,
        "leader_line_pct": round(lead_pts / scale * 100, 2),
    }
