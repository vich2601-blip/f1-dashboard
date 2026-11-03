import eventlet
eventlet.monkey_patch()
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime, timezone
import os
import re
import secrets
import threading
import time
from database import init_db
from api_client import F1DataClient
from fastf1_client import FastF1Client
from predictor import RacePredictor

app = Flask(__name__)
# SECRET_KEY must come from the environment in any real deployment — a
# hardcoded value lets anyone forge session cookies / socket auth tokens.
# Falls back to a random per-process key so local dev still works, but
# that means sessions won't survive a restart unless SECRET_KEY is set.
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
if not os.environ.get('SECRET_KEY'):
    print("⚠️  SECRET_KEY not set in environment — using a random ephemeral key. "
          "Set SECRET_KEY for production deployments.")

# Initialize SocketIO with eventlet async mode for true WebSocket support
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins='*')

client = F1DataClient()
ff1 = FastF1Client()
predictor = RacePredictor(client)

# Track connected clients per session room
_connected_clients = {}  # session_key -> set of sid
_clients_lock = threading.Lock()

# Current season — dynamically computed
def current_season():
    """Compute the current season dynamically so a long-running process
    still rolls over correctly at year boundaries."""
    return datetime.now(timezone.utc).year


# Team colours — 2026 grid plus historical aliases
TEAM_COLOURS = {
    "Red Bull": "#3671C6",
    "McLaren": "#FF8000",
    "Ferrari": "#E8002D",
    "Mercedes": "#27F4D2",
    "Aston Martin": "#229971",
    "Alpine": "#FF87BC",
    "Williams": "#64C4FF",
    "RB": "#6692FF",
    "Racing Bulls": "#6692FF",
    "Visa Cash App": "#6692FF",
    "Kick Sauber": "#52E252",
    "Sauber": "#52E252",
    "Audi": "#F50537",
    "Cadillac": "#D4AF37",
    "Haas": "#B6BABD",
    "AlphaTauri": "#6692FF",
    "Alfa Romeo": "#52E252",
    "Racing Point": "#229971",
}

# Country flag emoji mapping
COUNTRY_FLAGS = {
    "Australia": "🇦🇺", "Bahrain": "🇧🇭", "Saudi Arabia": "🇸🇦",
    "Japan": "🇯🇵", "China": "🇨🇳", "USA": "🇺🇸", "Italy": "🇮🇹",
    "Monaco": "🇲🇨", "Canada": "🇨🇦", "Spain": "🇪🇸", "Austria": "🇦🇹",
    "UK": "🇬🇧", "Hungary": "🇭🇺", "Belgium": "🇧🇪",
    "Netherlands": "🇳🇱", "Singapore": "🇸🇬", "Mexico": "🇲🇽",
    "Brazil": "🇧🇷", "Qatar": "🇶🇦", "UAE": "🇦🇪",
    "Azerbaijan": "🇦🇿", "United States": "🇺🇸", "United Kingdom": "🇬🇧",
    "France": "🇫🇷", "Germany": "🇩🇪", "Portugal": "🇵🇹",
    "Russia": "🇷🇺", "Turkey": "🇹🇷", "Emilia Romagna": "🇮🇹",
    "Las Vegas": "🇺🇸", "Miami": "🇺🇸",
}


def get_flag(country):
    """Get flag emoji for a country name."""
    return COUNTRY_FLAGS.get(country, "🏁")


def get_team_colour(constructor):
    """Get team colour hex for a constructor name, with fuzzy matching."""
    if not constructor:
        return "#888888"
    if constructor in TEAM_COLOURS:
        return TEAM_COLOURS[constructor]
    lower = constructor.lower()
    for name, colour in TEAM_COLOURS.items():
        if name.lower() in lower or lower in name.lower():
            return colour
    return "#888888"


_LAP_PATTERN = re.compile(r"^\+\d+ Laps?$", re.IGNORECASE)


def _is_dnf(status):
    """Check if a race status indicates a DNF (retirement/mechanical/crash).
    'Finished', empty, and '+N Lap(s)' are normal finishes, not DNFs."""
    if not status:
        return False
    s = status.strip()
    if s.lower() == "finished":
        return False
    if _LAP_PATTERN.match(s):
        return False
    return True


@app.context_processor
def inject_helpers():
    """Make helper functions and command-palette data available in all templates."""
    season = current_season()
    search_drivers = []
    search_races = []
    try:
        search_drivers = [
            {
                "code": d.get("driver_code", ""),
                "name": d.get("driver_name", ""),
                "team": d.get("constructor", ""),
            }
            for d in (client.get_driver_standings(season) or [])
            if d.get("driver_code")
        ]
        search_races = [
            {
                "round": r.get("round"),
                "name": r.get("race_name", ""),
                "country": r.get("country", ""),
            }
            for r in (client.get_schedule(season) or [])
        ]
    except Exception as err:
        print(f"[search index] {err}")
    return {
        "get_flag": get_flag,
        "get_team_colour": get_team_colour,
        "team_colours": TEAM_COLOURS,
        "now": datetime.now(timezone.utc),
        "app_season": season,
        "search_drivers": search_drivers,
        "search_races": search_races,
    }


@app.errorhandler(404)
def handle_not_found(e):
    return render_template("error.html", code=404, title="Off track",
                           message="That page is not on the calendar."), 404


@app.errorhandler(500)
def handle_server_error(e):
    print(f"[500] {e}")
    return render_template("error.html", code=500, title="Red flag",
                           message="Race data could not be fetched. Try again in a moment."), 500


# ── Routes ────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    """Main dashboard — standings overview, next race, recent results."""
    season = current_season()

    driver_standings = client.get_driver_standings(season)
    constructor_standings = client.get_constructor_standings(season)
    next_race = client.get_next_race(season)
    recent_races = client.get_last_completed_races(season, count=3)
    # Pre-fetch season results in bulk
    season_results = client.get_season_results(season)
    results_by_round = {}
    for r in season_results:
        rnd = r["round"]
        if rnd not in results_by_round:
            results_by_round[rnd] = []
        results_by_round[rnd].append(r)

    recent_results = []
    for race in recent_races:
        round_num = race["round"]
        # Filter results for this round and sort by position
        results = sorted(results_by_round.get(round_num, []), key=lambda x: x["position"])
        winner = results[0] if results else None
        recent_results.append({
            "race": race,
            "winner": winner,
        })
    schedule = client.get_schedule(season)
    completed_rounds = len(recent_races)
    if next_race and schedule:
        completed_rounds = max(0, (next_race.get("round") or 1) - 1)
    leader = driver_standings[0] if driver_standings else None
    chase = driver_standings[1] if len(driver_standings) > 1 else None
    leader_gap = None
    if leader and chase:
        try:
            leader_gap = float(leader.get("points") or 0) - float(chase.get("points") or 0)
        except (TypeError, ValueError):
            leader_gap = None
    constructor_leader = constructor_standings[0] if constructor_standings else None

    return render_template(
        "dashboard.html",
        driver_standings=driver_standings[:8],
        constructor_standings=constructor_standings[:8],
        next_race=next_race,
        recent_results=recent_results,
        season=season,
        schedule_count=len(schedule or []),
        completed_rounds=completed_rounds,
        leader=leader,
        chase=chase,
        leader_gap=leader_gap,
        constructor_leader=constructor_leader,
    )


@app.route("/calendar")
def calendar():
    """Full season calendar with all races."""
    season = current_season()
    schedule = client.get_schedule(season)
    now = datetime.now(timezone.utc)

    # Determine next race index
    next_race = client.get_next_race(season)
    next_round = next_race["round"] if next_race else None

    return render_template(
        "calendar.html",
        schedule=schedule,
        season=season,
        now=now,
        next_round=next_round,
    )



def _build_sector_data(season, round_num, results, id_to_code):
    """Build sector timing data by matching to OpenF1 session.
    Returns a dict with keys: laps_by_number, session_bests, driver_bests, drivers, max_lap.
    Each lap entry has s1, s2, s3, lap_time, and s1_class/s2_class/s3_class
    (purple/green/yellow) for template color coding."""

    # Try to match to an OpenF1 session
    session_key = client.match_openf1_session(season, round_num)
    if not session_key:
        return None

    of1_laps = client.get_openf1_laps(session_key)
    if not of1_laps:
        return None

    # Get driver info from OpenF1
    of1_drivers = client.get_openf1_drivers(session_key)
    dn_to_acronym = {d["driver_number"]: d.get("name_acronym", str(d["driver_number"])) for d in of1_drivers}
    dn_to_colour = {d["driver_number"]: d.get("team_colour", "888") for d in of1_drivers}
    dn_to_team = {d["driver_number"]: d.get("team_name", "") for d in of1_drivers}

    # Filter out pit-out laps and laps with no sector data
    valid_laps = [
        lp for lp in of1_laps
        if not lp.get("is_pit_out_lap")
        and lp.get("duration_sector_1") is not None
        and lp.get("duration_sector_2") is not None
        and lp.get("duration_sector_3") is not None
        and lp.get("lap_number") is not None
        and lp.get("driver_number") is not None
    ]

    if not valid_laps:
        return None

    # Compute session-best sector times
    session_best_s1 = min(lp["duration_sector_1"] for lp in valid_laps)
    session_best_s2 = min(lp["duration_sector_2"] for lp in valid_laps)
    session_best_s3 = min(lp["duration_sector_3"] for lp in valid_laps)

    # Compute per-driver best sectors
    driver_best = {}  # {driver_number: {s1: float, s2: float, s3: float}}
    for lp in valid_laps:
        dn = lp["driver_number"]
        if dn not in driver_best:
            driver_best[dn] = {"s1": lp["duration_sector_1"], "s2": lp["duration_sector_2"], "s3": lp["duration_sector_3"]}
        else:
            if lp["duration_sector_1"] < driver_best[dn]["s1"]:
                driver_best[dn]["s1"] = lp["duration_sector_1"]
            if lp["duration_sector_2"] < driver_best[dn]["s2"]:
                driver_best[dn]["s2"] = lp["duration_sector_2"]
            if lp["duration_sector_3"] < driver_best[dn]["s3"]:
                driver_best[dn]["s3"] = lp["duration_sector_3"]

    # Classify each sector: purple = session best, green = personal best, yellow = slower
    def classify(value, session_best, driver_pb):
        if abs(value - session_best) < 0.001:
            return "purple"
        if abs(value - driver_pb) < 0.001:
            return "green"
        return "yellow"

    # Group by lap number
    max_lap = max(lp["lap_number"] for lp in valid_laps)
    laps_by_number = {}  # {lap_num: [{driver_number, acronym, colour, s1, s2, s3, ...}]}

    for lp in valid_laps:
        dn = lp["driver_number"]
        lap_num = lp["lap_number"]

        entry = {
            "driver_number": dn,
            "acronym": dn_to_acronym.get(dn, str(dn)),
            "team_colour": dn_to_colour.get(dn, "888"),
            "team_name": dn_to_team.get(dn, ""),
            "s1": round(lp["duration_sector_1"], 3),
            "s2": round(lp["duration_sector_2"], 3),
            "s3": round(lp["duration_sector_3"], 3),
            "lap_time": round(lp.get("lap_duration", 0) or 0, 3),
            "s1_class": classify(lp["duration_sector_1"], session_best_s1, driver_best[dn]["s1"]),
            "s2_class": classify(lp["duration_sector_2"], session_best_s2, driver_best[dn]["s2"]),
            "s3_class": classify(lp["duration_sector_3"], session_best_s3, driver_best[dn]["s3"]),
        }

        if lap_num not in laps_by_number:
            laps_by_number[lap_num] = []
        laps_by_number[lap_num].append(entry)

    # Build driver list for the select filter
    all_drivers = sorted(set(dn_to_acronym.keys()))
    driver_list = [{"number": dn, "acronym": dn_to_acronym.get(dn, str(dn)), "colour": dn_to_colour.get(dn, "888")} for dn in all_drivers]

    return {
        "laps_by_number": laps_by_number,
        "session_bests": {
            "s1": round(session_best_s1, 3),
            "s2": round(session_best_s2, 3),
            "s3": round(session_best_s3, 3),
        },
        "driver_bests": {dn: {"s1": round(v["s1"], 3), "s2": round(v["s2"], 3), "s3": round(v["s3"], 3)} for dn, v in driver_best.items()},
        "drivers": driver_list,
        "max_lap": max_lap,
    }


@app.route("/race/<int:season>/<int:round_num>")
def race_detail(season, round_num):
    """Race detail page — results, qualifying, lap chart, pit stops, position chart, tyre strategy."""
    results = client.get_race_results(season, round_num)
    qualifying = client.get_qualifying(season, round_num)
    pit_stops = client.get_pit_stops(season, round_num)

    # Get race info from schedule
    schedule = client.get_schedule(season)
    race_info = None
    for r in schedule:
        if r["round"] == round_num:
            race_info = r
            break

    # Get all laps for this race
    all_laps = client.get_lap_times(season, round_num) if results else []
    total_laps = max((lt.get("lap", 0) for lt in all_laps), default=0) if all_laps else 0

    # Build driver_id → driver_code lookup map
    # Lap times and pit stops use driverId slugs (e.g. "max_verstappen"),
    # while results use 3-letter codes (e.g. "VER"). We need to bridge them.
    id_to_code = {}
    code_to_id = {}
    if results:
        for r in results:
            did = r.get("driver_id", "")
            dcode = r.get("driver_code", "")
            if did and dcode:
                id_to_code[did] = dcode
                code_to_id[dcode] = did

    # Get lap times for chart (top 5 finishers only for readability)
    lap_times = []
    if results:
        for r in results[:5]:
            driver_code = r["driver_code"]
            driver_id = r.get("driver_id", "")
            # Lap times store driverId slug in driver_code field
            driver_laps = sorted(
                [lt for lt in all_laps if lt["driver_code"] == driver_id],
                key=lambda x: x["lap"]
            )
            if driver_laps:
                lap_times.append({
                    "driver_code": driver_code,
                    "constructor": r["constructor"],
                    "laps": driver_laps,
                })

    # Position change data (all drivers)
    position_data = []
    if results and all_laps:
        for r in results[:10]:  # Top 10 for readability
            driver_code = r["driver_code"]
            driver_id = r.get("driver_id", "")
            driver_positions = sorted(
                [{"lap": lt["lap"], "position": lt.get("position", r["position"])}
                 for lt in all_laps if lt["driver_code"] == driver_id and lt.get("position")],
                key=lambda x: x["lap"]
            )
            # Add grid position as lap 0
            if driver_positions:
                grid_pos = r.get("grid", driver_positions[0]["position"])
                # If grid is 0 (pit lane start), use first recorded position
                if grid_pos == 0:
                    grid_pos = driver_positions[0]["position"]
                driver_positions.insert(0, {"lap": 0, "position": grid_pos})
                position_data.append({
                    "driver_code": driver_code,
                    "constructor": r["constructor"],
                    "positions": driver_positions,
                })

    # Tyre stint data from pit stops
    stint_data = []
    if results and pit_stops and total_laps > 0:
        for r in results[:10]:
            driver_code = r["driver_code"]
            driver_id = r.get("driver_id", "")
            # Pit stops store driverId slug in driver_code field
            driver_pits = sorted(
                [ps for ps in pit_stops if ps.get("driver_code") == driver_id],
                key=lambda x: x.get("lap", 0)
            )
            stints = []
            prev_lap = 1
            for ps in driver_pits:
                pit_lap = ps.get("lap", prev_lap)
                stints.append({"start": prev_lap, "end": pit_lap, "compound": "MEDIUM"})
                prev_lap = pit_lap + 1
            # Final stint
            if prev_lap <= total_laps:
                stints.append({"start": prev_lap, "end": total_laps, "compound": "HARD"})
            elif not stints:
                stints.append({"start": 1, "end": total_laps, "compound": "MEDIUM"})
            stint_data.append({
                "driver_code": driver_code,
                "constructor": r["constructor"],
                "stints": stints,
            })

    # Resolve OpenF1 session_key for this race round
    session_key = None
    if race_info:
        of1_sessions = client.get_openf1_sessions(season)
        loc_target = race_info.get("locality", "").lower()
        country_target = race_info.get("country", "").lower()

        for s in of1_sessions:
            if s.get("session_type") == "Race":
                loc_s = s.get("location", "").lower()
                country_s = s.get("country_name", "").lower()
                if (loc_target in loc_s or loc_s in loc_target or 
                    country_target in country_s or country_s in country_target):
                    session_key = s.get("session_key")
                    break

    return render_template(
        "race.html",
        results=results,
        qualifying=qualifying,
        pit_stops=pit_stops,
        race_info=race_info,
        lap_times=lap_times,
        position_data=position_data,
        stint_data=stint_data,
        total_laps=total_laps,
        season=season,
        round_num=round_num,
        sector_data=_build_sector_data(season, round_num, results, id_to_code),
        session_key=session_key,
    )
@app.route("/standings")
def standings():
    """Full championship standings page."""
    season = request.args.get("season", current_season(), type=int)
    driver_standings = client.get_driver_standings(season)
    constructor_standings = client.get_constructor_standings(season)
    # Fetch season schedule to identify simulatable races
    schedule = client.get_schedule(season)
    remaining_races = []

    if schedule:
        now = datetime.now(timezone.utc)
        for race in schedule:
            try:
                race_date_str = race["date"]
                race_time_str = race.get("time") or "14:00:00Z"
                dt_str = f"{race_date_str}T{race_time_str}"
                if not dt_str.endswith("Z"):
                    dt_str += "Z"
                race_dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                if race_dt >= now:
                    remaining_races.append(race)
            except (ValueError, KeyError):
                # Fallback: simple date string check
                try:
                    today_str = now.date().isoformat()
                    if race["date"] >= today_str:
                        remaining_races.append(race)
                except Exception:
                    remaining_races.append(race)
        is_historic_simulation = False
        if not remaining_races:
            remaining_races = schedule[-5:]
            is_historic_simulation = True
    else:
        is_historic_simulation = False

    return render_template(
        "standings.html",
        driver_standings=driver_standings,
        constructor_standings=constructor_standings,
        season=season,
        remaining_races=remaining_races,
        is_historic_simulation=is_historic_simulation,
    )

@app.route("/driver/<driver_id>")
def driver_profile(driver_id):
    """Driver profile page with season stats and race-by-race results."""
    season = current_season()
    driver_standings = client.get_driver_standings(season)
    schedule = client.get_schedule(season)

    # Find driver in standings
    driver_info = None
    for d in driver_standings:
        if d.get("driver_code", "").upper() == driver_id.upper() or d.get("driver_id", "") == driver_id:
            driver_info = d
            break

    if not driver_info:
        return render_template("driver.html", driver=None, season=season)

    # Gather race-by-race results in memory (1 API/DB query!)
    season_results = client.get_season_results(season)
    completed_races = client.get_last_completed_races(season, count=30)

    # Group results by round for fast O(1) lookup
    results_by_round = {}
    for r in season_results:
        rnd = r["round"]
        if rnd not in results_by_round:
            results_by_round[rnd] = []
        results_by_round[rnd].append(r)

    race_results = []
    points_history = []
    cumulative_points = 0
    d_code = driver_info.get("driver_code", "").upper()

    for race in completed_races:
        results = results_by_round.get(race["round"], [])
        driver_result = None
        for r in results:
            if r.get("driver_code", "").upper() == d_code:
                driver_result = r
                break

        if driver_result:
            cumulative_points += driver_result.get("points", 0)
            race_results.append({
                "race": race,
                "result": driver_result,
                "cumulative_points": cumulative_points,
            })
            points_history.append({
                "round": race["round"],
                "race_name": race.get("race_name", f"R{race['round']}"),
                "points": cumulative_points,
                "position": driver_result.get("position", 0),
            })

    # Compute stats
    positions = [rr["result"]["position"] for rr in race_results if rr["result"].get("position")]
    stats = {
        "wins": sum(1 for p in positions if p == 1),
        "podiums": sum(1 for p in positions if p <= 3),
        "top5": sum(1 for p in positions if p <= 5),
        "top10": sum(1 for p in positions if p <= 10),
        "best_finish": min(positions) if positions else "—",
        "avg_finish": round(sum(positions) / len(positions), 1) if positions else "—",
        "dnfs": sum(1 for rr in race_results if _is_dnf(rr["result"].get("status", ""))),
        "races": len(race_results),
    }

    return render_template(
        "driver.html",
        driver=driver_info,
        stats=stats,
        race_results=race_results,
        points_history=points_history,
        season=season,
    )


@app.route("/compare")
def compare_drivers():
    """Head-to-head driver comparison page."""
    season = current_season()
    d1_code = request.args.get("d1", "").upper()
    d2_code = request.args.get("d2", "").upper()

    driver_standings = client.get_driver_standings(season)

    # If no drivers specified, show the picker
    if not d1_code or not d2_code:
        return render_template(
            "compare.html",
            driver_standings=driver_standings,
            d1=None, d2=None,
            season=season,
        )

    # Find both drivers
    d1_info = None
    d2_info = None
    for d in driver_standings:
        code = d.get("driver_code", "").upper()
        if code == d1_code:
            d1_info = d
        if code == d2_code:
            d2_info = d

    if not d1_info or not d2_info:
        return render_template(
            "compare.html",
            driver_standings=driver_standings,
            d1=d1_info, d2=d2_info,
            season=season,
            error="One or both drivers not found.",
        )

    # Gather season results and qualifying in bulk (1 API/DB call each!)
    season_results = client.get_season_results(season)
    season_quali = client.get_season_qualifying(season)
    completed_races = client.get_last_completed_races(season, count=30)

    completed_round_map = {r["round"]: r for r in completed_races}

    def get_driver_season_data(driver_info):
        d_code = driver_info.get("driver_code", "").upper()
        
        # Filter in memory
        d_results = [r for r in season_results if r.get("driver_code", "").upper() == d_code and r["round"] in completed_round_map]
        d_quali = [q for q in season_quali if q.get("driver_code", "").upper() == d_code and q["round"] in completed_round_map]
        
        # Sort by round
        d_results.sort(key=lambda x: x["round"])
        d_quali.sort(key=lambda x: x["round"])
        
        positions = [r["position"] for r in d_results]
        quali_positions = [q["position"] for q in d_quali]
        
        points_per_race = []
        for r in d_results:
            race = completed_round_map[r["round"]]
            points_per_race.append({
                "round": r["round"],
                "race_name": race.get("race_name", ""),
                "position": r["position"],
                "points": r["points"]
            })
            
        return {
            "info": driver_info,
            "positions": positions,
            "quali_positions": quali_positions,
            "points_per_race": points_per_race,
            "wins": sum(1 for p in positions if p == 1),
            "podiums": sum(1 for p in positions if p <= 3),
            "avg_finish": round(sum(positions) / len(positions), 1) if positions else 0,
            "avg_quali": round(sum(quali_positions) / len(quali_positions), 1) if quali_positions else 0,
            "best_finish": min(positions) if positions else 0,
            "best_quali": min(quali_positions) if quali_positions else 0,
        }

    d1_data = get_driver_season_data(d1_info)
    d2_data = get_driver_season_data(d2_info)
    # Head-to-head counts
    h2h_race = 0
    h2h_quali = 0
    min_races = min(len(d1_data["positions"]), len(d2_data["positions"]))
    for i in range(min_races):
        if d1_data["positions"][i] < d2_data["positions"][i]:
            h2h_race += 1

    min_quali = min(len(d1_data["quali_positions"]), len(d2_data["quali_positions"]))
    for i in range(min_quali):
        if d1_data["quali_positions"][i] < d2_data["quali_positions"][i]:
            h2h_quali += 1

    # Points progression calculation
    rounds_set = sorted(list(set(
        [r["round"] for r in d1_data["points_per_race"]] +
        [r["round"] for r in d2_data["points_per_race"]]
    )))

    d1_cumulative = []
    d2_cumulative = []
    progression_labels = []
    d1_total = 0
    d2_total = 0

    d1_lookup = {r["round"]: r for r in d1_data["points_per_race"]}
    d2_lookup = {r["round"]: r for r in d2_data["points_per_race"]}

    for rnd in rounds_set:
        d1_pt = d1_lookup.get(rnd, {}).get("points", 0.0)
        d2_pt = d2_lookup.get(rnd, {}).get("points", 0.0)

        d1_total += d1_pt
        d2_total += d2_pt

        d1_cumulative.append(float(d1_total))
        d2_cumulative.append(float(d2_total))

        race_name = d1_lookup.get(rnd, {}).get("race_name") or d2_lookup.get(rnd, {}).get("race_name") or f"Round {rnd}"
        short_name = race_name.replace(" Grand Prix", "")
        progression_labels.append(f"R{rnd}: {short_name}")

    progression_data = {
        "labels": progression_labels,
        "d1_values": d1_cumulative,
        "d2_values": d2_cumulative,
    }

    return render_template(
        "compare.html",
        driver_standings=driver_standings,
        d1=d1_data,
        d2=d2_data,
        h2h_race=h2h_race,
        h2h_race_total=min_races,
        h2h_quali=h2h_quali,
        h2h_quali_total=min_quali,
        season=season,
        progression_data=progression_data,
    )

@app.route("/live")
def live_timing():
    """Live timing page with circuit map and timing tower."""
    requested_year = request.args.get("year", current_season(), type=int)
    session_key = request.args.get("session", None, type=int)

    # Get available sessions for session picker (OpenF1 → F1 Live Timing)
    year = requested_year
    sessions = client.get_unified_sessions(year)

    # Fallback: if no sessions for requested year, try recent years
    fallback_year = None
    if not sessions:
        for try_year in [2025, 2024, 2023]:
            if try_year == requested_year:
                continue
            sessions = client.get_unified_sessions(try_year)
            if sessions:
                fallback_year = try_year
                year = try_year
                break

    # Default to the latest race session if none specified
    if not session_key and sessions:
        race_sessions = [s for s in sessions if s.get("session_type") == "Race"]
        if race_sessions:
            session_key = race_sessions[-1]["session_key"]
        elif sessions:
            session_key = sessions[-1]["session_key"]

    # Get session info
    session_info = None
    if session_key:
        for s in sessions:
            if s["session_key"] == session_key:
                session_info = s
                break

    # Get initial data for server-side render using unified (free) methods
    drivers = []
    positions = []
    laps = []
    weather = {}
    race_control = []
    stints = []
    track_outline = []

    if session_key:
        drivers = client.get_unified_drivers(session_key)
        positions = client.get_unified_positions(session_key)
        laps = client.get_unified_laps(session_key)
        weather = client.get_unified_weather(session_key)
        race_control = client.get_unified_race_control(session_key)
        stints = client.get_unified_stints(session_key)
        track_outline = client.get_unified_track_outline(session_key)

    # Build driver lookup for template
    driver_map = {d["driver_number"]: d for d in drivers}

    # Get latest lap per driver (last entry)
    latest_laps = {}
    for lap in laps:
        dn = lap.get("driver_number")
        if dn is not None:
            existing = latest_laps.get(dn)
            if existing is None or (lap.get("lap_number") or 0) > (existing.get("lap_number") or 0):
                latest_laps[dn] = lap

    # Best sector times across session for purple highlighting
    best_s1 = None
    best_s2 = None
    best_s3 = None
    for lap in laps:
        s1 = lap.get("duration_sector_1")
        s2 = lap.get("duration_sector_2")
        s3 = lap.get("duration_sector_3")
        if s1 and (best_s1 is None or s1 < best_s1):
            best_s1 = s1
        if s2 and (best_s2 is None or s2 < best_s2):
            best_s2 = s2
        if s3 and (best_s3 is None or s3 < best_s3):
            best_s3 = s3

    # Latest stint per driver
    latest_stints = {}
    for s in stints:
        dn = s.get("driver_number")
        if dn is not None:
            existing = latest_stints.get(dn)
            if existing is None or (s.get("stint_number") or 0) > (existing.get("stint_number") or 0):
                latest_stints[dn] = s

    # Data source indicator for the template
    data_source = client.last_data_source
    data_source_label = "OpenF1" if data_source == "openf1" else "F1 Live Timing" if data_source == "f1_live" else "—"

    return render_template(
        "live.html",
        sessions=sessions,
        session_key=session_key,
        session_info=session_info,
        year=year,
        requested_year=requested_year,
        fallback_year=fallback_year,
        drivers=drivers,
        driver_map=driver_map,
        positions=positions,
        latest_laps=latest_laps,
        best_sectors={"s1": best_s1, "s2": best_s2, "s3": best_s3},
        weather=weather,
        race_control=race_control,
        latest_stints=latest_stints,
        track_outline=track_outline,
        data_source=data_source,
        data_source_label=data_source_label,
    )


@app.route("/api/live/<int:session_key>")
def api_live_data(session_key):
    """AJAX endpoint — returns live data for polling.
    Use ?full=1 for all data (drivers, weather, stints, race_control).
    Default returns only fast-changing data (positions, locations, intervals).
    Uses unified methods: OpenF1 (free) → F1 Live Timing (free) fallback.
    """
    full = request.args.get("full", "0") == "1"

    # Always fetch fast-changing data via unified (free) methods
    positions = client.get_unified_positions(session_key)
    locations = client.get_unified_locations(session_key)
    intervals = client.get_unified_intervals(session_key)

    result = {
        "positions": positions,
        "locations": locations,
        "intervals": intervals,
        "data_source": client.last_data_source,
    }

    # Only include slow-changing data on full refresh
    if full:
        result["drivers"] = client.get_unified_drivers(session_key)
        result["laps"] = client.get_unified_laps(session_key)
        result["weather"] = client.get_unified_weather(session_key)
        result["race_control"] = client.get_unified_race_control(session_key)
        result["stints"] = client.get_unified_stints(session_key)

    return jsonify(result)


@app.route("/api/track/<int:session_key>")
def api_track_outline(session_key):
    """Get track outline coordinates for circuit map rendering."""
    points = client.get_track_outline(session_key)
    return jsonify({"points": points})


@app.route("/api/refresh/<int:season>")
def refresh_data(season):
    """Force-refresh all cached data for a season."""
    from database import clear_table
    tables = [
        "races", "results", "driver_standings",
        "constructor_standings", "qualifying", "pit_stops", "lap_times",
    ]
    for table in tables:
        clear_table(table, {"season": season})

    return jsonify({"status": "ok", "message": f"Cache cleared for {season}"})


@app.route("/api/sectors/<int:season>/<int:round_num>")
def api_sector_data(season, round_num):
    """Get sector timing data for a race. Returns JSON with per-lap sector times."""
    results = client.get_race_results(season, round_num)
    id_to_code = {}
    if results:
        for r in results:
            did = r.get("driver_id", "")
            dcode = r.get("driver_code", "")
            if did and dcode:
                id_to_code[did] = dcode

    data = _build_sector_data(season, round_num, results, id_to_code)
    if not data:
        return jsonify({"error": "No sector data available"}), 404

    # Convert laps_by_number keys to strings for JSON
    data["laps_by_number"] = {str(k): v for k, v in data["laps_by_number"].items()}
    return jsonify(data)


@app.route("/api/telemetry/<int:session_key>")
def api_telemetry_comparison(session_key):
    """API endpoint to fetch lap telemetry comparison for two drivers."""
    d1_num = request.args.get("d1", type=int)
    d2_num = request.args.get("d2", type=int)
    lap_num = request.args.get("lap", type=int)

    if not d1_num or not d2_num or not lap_num:
        return jsonify({"error": "Missing parameters d1, d2, or lap"}), 400

    # Get all laps to search start date & duration
    laps = client.get_unified_laps(session_key)
    
    d1_lap = None
    d2_lap = None
    for lap in laps:
        if lap.get("driver_number") == d1_num and lap.get("lap_number") == lap_num:
            d1_lap = lap
        if lap.get("driver_number") == d2_num and lap.get("lap_number") == lap_num:
            d2_lap = lap
            
    # Fetch telemetry
    d1_telemetry = []
    if d1_lap:
        d1_telemetry = client.get_lap_telemetry(
            session_key, d1_num, d1_lap.get("date_start"), d1_lap.get("lap_duration")
        )
        
    d2_telemetry = []
    if d2_lap:
        d2_telemetry = client.get_lap_telemetry(
            session_key, d2_num, d2_lap.get("date_start"), d2_lap.get("lap_duration")
        )

    return jsonify({
        "d1": {
            "driver_number": d1_num,
            "telemetry": d1_telemetry
        },
        "d2": {
            "driver_number": d2_num,
            "telemetry": d2_telemetry
        }
    })


# ══════════════════════════════════════════════════════════════
# WebSocket Event Handlers
# ══════════════════════════════════════════════════════════════

@socketio.on('connect')
def handle_connect():
    """Client connected via WebSocket."""
    print(f"🔌 Client connected: {request.sid}")
    emit('connection_status', {'status': 'connected', 'sid': request.sid})


@socketio.on('disconnect')
def handle_disconnect():
    """Client disconnected — clean up room memberships."""
    sid = request.sid
    with _clients_lock:
        for sk, sids in list(_connected_clients.items()):
            sids.discard(sid)
            if not sids:
                del _connected_clients[sk]
    print(f"🔌 Client disconnected: {sid}")


@socketio.on('join_session')
def handle_join_session(data):
    """Client subscribes to live updates for a session."""
    session_key = data.get('session_key')
    if not session_key:
        return
    room = f"session_{session_key}"
    join_room(room)
    with _clients_lock:
        if session_key not in _connected_clients:
            _connected_clients[session_key] = set()
        _connected_clients[session_key].add(request.sid)
    print(f"📡 {request.sid} joined session {session_key}")
    emit('joined_session', {'session_key': session_key, 'room': room})


@socketio.on('leave_session')
def handle_leave_session(data):
    """Client unsubscribes from a session's live updates."""
    session_key = data.get('session_key')
    if not session_key:
        return
    room = f"session_{session_key}"
    leave_room(room)
    with _clients_lock:
        if session_key in _connected_clients:
            _connected_clients[session_key].discard(request.sid)


@socketio.on('request_telemetry')
def handle_request_telemetry(data):
    """Client requests driver telemetry for a specific lap via FastF1."""
    year = data.get('year')
    round_num = data.get('round')
    driver = data.get('driver')
    lap_number = data.get('lap')
    session_type = data.get('session_type', 'R')

    if not all([year, round_num, driver, lap_number]):
        emit('telemetry_data', {'error': 'Missing parameters'})
        return

    telemetry = ff1.get_driver_telemetry(year, round_num, driver, lap_number, session_type)
    emit('telemetry_data', {
        'driver': driver,
        'lap': lap_number,
        'telemetry': telemetry,
    })


@socketio.on('request_comparison')
def handle_request_comparison(data):
    """Client requests two-driver telemetry comparison."""
    year = data.get('year')
    round_num = data.get('round')
    d1 = data.get('d1')
    d2 = data.get('d2')
    lap1 = data.get('lap1')
    lap2 = data.get('lap2')
    session_type = data.get('session_type', 'R')

    if not all([year, round_num, d1, d2]):
        emit('comparison_data', {'error': 'Missing parameters'})
        return

    result = ff1.get_telemetry_comparison(year, round_num, d1, d2, lap1, lap2, session_type)
    emit('comparison_data', result)


# ══════════════════════════════════════════════════════════════
# Background Data Emitter — pushes updates to session rooms
# ══════════════════════════════════════════════════════════════

_emitter_state = {}  # session_key -> last data hash


def _background_emitter():
    """Background thread that fetches live data and pushes changes via WebSocket."""
    cycle = 0
    while True:
        try:
            socketio.sleep(2)  # 2-second interval
            cycle += 1

            with _clients_lock:
                active_sessions = list(_connected_clients.keys())

            for session_key in active_sessions:
                room = f"session_{session_key}"
                try:
                    # Fast data (every cycle)
                    positions = client.get_unified_positions(session_key)
                    locations = client.get_unified_locations(session_key)
                    intervals = client.get_unified_intervals(session_key)

                    socketio.emit('positions_update', {
                        'positions': positions,
                        'locations': locations,
                        'intervals': intervals,
                        'data_source': client.last_data_source,
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                    }, room=room)

                    # Slow data (every 15th cycle ≈ 30s)
                    if cycle % 15 == 0:
                        laps = client.get_unified_laps(session_key)
                        weather = client.get_unified_weather(session_key)
                        stints = client.get_unified_stints(session_key)
                        race_control = client.get_unified_race_control(session_key)

                        socketio.emit('full_update', {
                            'laps': laps,
                            'weather': weather,
                            'stints': stints,
                            'race_control': race_control,
                            'data_source': client.last_data_source,
                        }, room=room)

                except Exception as e:
                    print(f"[Emitter] Error for session {session_key}: {e}")

        except Exception as e:
            print(f"[Emitter] Background thread error: {e}")
            socketio.sleep(5)


# ══════════════════════════════════════════════════════════════
# FastF1 API Endpoints
# ══════════════════════════════════════════════════════════════

@app.route("/api/fastf1/sessions/<int:year>")
def api_fastf1_sessions(year):
    """List all available FastF1 sessions for a year."""
    sessions = ff1.get_available_sessions(year)
    return jsonify({"sessions": sessions})


@app.route("/api/fastf1/telemetry/<int:year>/<int:round_num>/<driver>/<int:lap_num>")
def api_fastf1_telemetry(year, round_num, driver, lap_num):
    """Get full lap telemetry (speed, throttle, brake, gear, RPM, DRS) via FastF1."""
    session_type = request.args.get("session", "R")
    telemetry = ff1.get_driver_telemetry(year, round_num, driver, lap_num, session_type)
    if not telemetry:
        return jsonify({"error": "No telemetry data available"}), 404
    return jsonify({"driver": driver, "lap": lap_num, "telemetry": telemetry})


@app.route("/api/fastf1/comparison/<int:year>/<int:round_num>")
def api_fastf1_comparison(year, round_num):
    """Two-driver telemetry comparison via FastF1."""
    d1 = request.args.get("d1", "")
    d2 = request.args.get("d2", "")
    lap1 = request.args.get("lap1", type=int)
    lap2 = request.args.get("lap2", type=int)
    session_type = request.args.get("session", "R")

    if not d1 or not d2:
        return jsonify({"error": "Missing d1 or d2 parameters"}), 400

    result = ff1.get_telemetry_comparison(year, round_num, d1, d2, lap1, lap2, session_type)
    return jsonify(result)


@app.route("/api/fastf1/laps/<int:year>/<int:round_num>")
def api_fastf1_laps(year, round_num):
    """Get all lap data for a session via FastF1."""
    session_type = request.args.get("session", "R")
    laps = ff1.get_lap_data(year, round_num, session_type)
    return jsonify({"laps": laps})


@app.route("/api/fastf1/fastest/<int:year>/<int:round_num>")
def api_fastf1_fastest(year, round_num):
    """Get fastest lap per driver via FastF1."""
    session_type = request.args.get("session", "R")
    fastest = ff1.get_fastest_laps(year, round_num, session_type)
    return jsonify({"fastest_laps": fastest})


@app.route("/api/fastf1/stints/<int:year>/<int:round_num>")
def api_fastf1_stints(year, round_num):
    """Get tyre stint data via FastF1."""
    session_type = request.args.get("session", "R")
    stints = ff1.get_stint_data(year, round_num, session_type)
    return jsonify({"stints": stints})


@app.route("/api/fastf1/race-control/<int:year>/<int:round_num>")
def api_fastf1_race_control(year, round_num):
    """Get race control messages via FastF1."""
    session_type = request.args.get("session", "R")
    messages = ff1.get_race_control_messages(year, round_num, session_type)
    return jsonify({"messages": messages})


# ══════════════════════════════════════════════════════════════
# Race Winner Prediction
# ══════════════════════════════════════════════════════════════

@app.route("/predict")
def predict():
    """Race win predictor page."""
    season = current_season()
    next_race = client.get_next_race(season)
    model_info = predictor.get_model_info()
    return render_template(
        "predict.html",
        season=season,
        next_race=next_race,
        model_info=model_info,
    )


@app.route("/api/predict/<int:season>/<int:round_num>")
def api_predict_race(season, round_num):
    """Predicted win probabilities for every driver using the stacking ensemble.
    Returns predictions, SHAP feature importances, and model metadata.
    Use ?shap=0 to skip SHAP computation for faster responses."""
    include_shap = request.args.get("shap", "1") == "1"
    result = predictor.predict_race(season, round_num, include_shap=include_shap)
    if result.get("error"):
        return jsonify(result), 503
    return jsonify(result)


@app.route("/api/predict/explain/<int:season>/<int:round_num>/<driver_code>")
def api_predict_explain(season, round_num, driver_code):
    """Per-driver SHAP waterfall explanation for the race predictor."""
    result = predictor.predict_race(season, round_num, include_shap=True)
    if result.get("error"):
        return jsonify(result), 503
    shap_data = result.get("shap_by_driver", {}).get(driver_code.upper())
    if shap_data is None:
        return jsonify({"error": f"Driver {driver_code} not found in predictions"}), 404
    # Also include the driver's prediction
    pred = next((p for p in result["predictions"] if p["driver_code"] == driver_code.upper()), None)
    return jsonify({
        "driver_code": driver_code.upper(),
        "prediction": pred,
        "shap_features": shap_data,
        "model_info": {
            "oof_mae": result.get("oof_mae"),
            "n_training_rows": result.get("n_training_rows"),
            "training_seasons": result.get("training_seasons"),
        }
    })


@app.route("/api/predict/model-info")
def api_predict_model_info():
    """Return metadata about the currently loaded ensemble model."""
    return jsonify(predictor.get_model_info())


@app.route("/api/predict/live/<int:season>/<int:round_num>", methods=["POST"])
def api_predict_live(season, round_num):
    """Real-time race predictions taking current race positions into account."""
    data = request.get_json() or {}
    live_standings = data.get("live_standings", [])
    result = predictor.predict_live(season, round_num, live_standings)
    if result.get("error"):
        return jsonify(result), 503
    return jsonify(result)


@app.route("/api/predict/retrain", methods=["POST"])
def api_predict_retrain():
    """Force an immediate model retrain using up to 8 seasons of data.
    Runs in the background — returns immediately with status 'started'."""
    import threading
    yr = datetime.now(timezone.utc).year
    from predictor import DEFAULT_TRAINING_SEASONS
    seasons = list(range(yr - DEFAULT_TRAINING_SEASONS + 1, yr + 1))

    def _retrain():
        ok = predictor.train(seasons=seasons)
        if ok:
            print(f"[Retrain] ✅ Ensemble retrained on {seasons}")
        else:
            print("[Retrain] ❌ Not enough data.")

    t = threading.Thread(target=_retrain, daemon=True)
    t.start()
    return jsonify({"status": "started", "message": f"Retraining on seasons {seasons[0]}–{seasons[-1]}"})


# ── Main ──────────────────────────────────────────────────────────

# Ensure database tables exist regardless of how the app is started
# (python app.py, flask run, gunicorn, etc.)
init_db()

if __name__ == "__main__":
    print("🏎️  F1 Dashboard starting on http://localhost:8050")
    print("   WebSocket: enabled (eventlet)")
    print("   FastF1:    enabled (cache: ./fastf1_cache)")
    print("   Predictor: XGBoost + LightGBM + CatBoost Stacking Ensemble (8 seasons)")
    # Start background emitter thread
    socketio.start_background_task(_background_emitter)
    # Train the ensemble predictor in the background so startup is never blocked.
    # Uses up to 8 seasons of historical data for maximum accuracy.
    from predictor import DEFAULT_TRAINING_SEASONS
    yr = datetime.now(timezone.utc).year
    train_seasons = list(range(yr - DEFAULT_TRAINING_SEASONS + 1, yr + 1))
    threading.Thread(
        target=lambda: predictor.train(seasons=train_seasons),
        daemon=True,
    ).start()
    socketio.run(app, host="127.0.0.1", port=8050, debug=False)