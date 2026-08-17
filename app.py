from flask import Flask, render_template, jsonify, request
from datetime import datetime, timezone
import re
from database import init_db
from api_client import F1DataClient

app = Flask(__name__)
client = F1DataClient()

# Current season — dynamically computed
CURRENT_SEASON = datetime.now().year

# Team colours for consistent styling
TEAM_COLOURS = {
    "Red Bull": "#3671C6",
    "McLaren": "#FF8000",
    "Ferrari": "#E8002D",
    "Mercedes": "#27F4D2",
    "Aston Martin": "#229971",
    "Alpine": "#FF87BC",
    "Williams": "#64C4FF",
    "RB": "#6692FF",
    "Kick Sauber": "#52E252",
    "Haas": "#B6BABD",
    # Fallbacks with common alternate names
    "AlphaTauri": "#6692FF",
    "Alfa Romeo": "#52E252",
    "Racing Point": "#229971",
    "Sauber": "#52E252",
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
    """Get team colour hex for a constructor name."""
    return TEAM_COLOURS.get(constructor, "#888888")


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
    """Make helper functions available in all templates."""
    return {
        "get_flag": get_flag,
        "get_team_colour": get_team_colour,
        "team_colours": TEAM_COLOURS,
        "now": datetime.now(timezone.utc),
    }


# ── Routes ────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    """Main dashboard — standings overview, next race, recent results."""
    season = CURRENT_SEASON

    driver_standings = client.get_driver_standings(season)
    constructor_standings = client.get_constructor_standings(season)
    next_race = client.get_next_race(season)
    recent_races = client.get_last_completed_races(season, count=3)

    # Get winners for recent races
    recent_results = []
    for race in recent_races:
        results = client.get_race_results(season, race["round"])
        winner = results[0] if results else None
        recent_results.append({
            "race": race,
            "winner": winner,
        })

    return render_template(
        "dashboard.html",
        driver_standings=driver_standings[:5],
        constructor_standings=constructor_standings[:5],
        next_race=next_race,
        recent_results=recent_results,
        season=season,
    )


@app.route("/calendar")
def calendar():
    """Full season calendar with all races."""
    season = CURRENT_SEASON
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
    )


@app.route("/standings")
def standings():
    """Full championship standings page."""
    season = request.args.get("season", CURRENT_SEASON, type=int)
    driver_standings = client.get_driver_standings(season)
    constructor_standings = client.get_constructor_standings(season)

    return render_template(
        "standings.html",
        driver_standings=driver_standings,
        constructor_standings=constructor_standings,
        season=season,
    )


@app.route("/driver/<driver_id>")
def driver_profile(driver_id):
    """Driver profile page with season stats and race-by-race results."""
    season = CURRENT_SEASON
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

    # Gather race-by-race results
    race_results = []
    completed_races = client.get_last_completed_races(season, count=30)
    points_history = []
    cumulative_points = 0

    for race in completed_races:
        results = client.get_race_results(season, race["round"])
        driver_result = None
        for r in results:
            if r.get("driver_code", "").upper() == driver_info.get("driver_code", "").upper():
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
    season = CURRENT_SEASON
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

    # Gather race-by-race for both
    completed_races = client.get_last_completed_races(season, count=30)

    def get_driver_season_data(driver_info):
        positions = []
        quali_positions = []
        points_per_race = []

        for race in completed_races:
            results = client.get_race_results(season, race["round"])
            for r in results:
                if r.get("driver_code", "").upper() == driver_info.get("driver_code", "").upper():
                    positions.append(r.get("position", 0))
                    points_per_race.append({
                        "round": race["round"],
                        "race_name": race.get("race_name", ""),
                        "position": r.get("position", 0),
                        "points": r.get("points", 0),
                    })
                    break

            # Qualifying
            qualifying = client.get_qualifying(season, race["round"])
            for q in qualifying:
                if q.get("driver_code", "").upper() == driver_info.get("driver_code", "").upper():
                    quali_positions.append(q.get("position", 0))
                    break

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
    )


@app.route("/live")
def live_timing():
    """Live timing page with circuit map and timing tower."""
    requested_year = request.args.get("year", CURRENT_SEASON, type=int)
    session_key = request.args.get("session", None, type=int)

    # Get available sessions for session picker
    year = requested_year
    sessions = client.get_openf1_sessions(year)

    # Fallback: if no sessions for requested year, try recent years
    fallback_year = None
    if not sessions:
        for try_year in [2025, 2024, 2023]:
            if try_year == requested_year:
                continue
            sessions = client.get_openf1_sessions(try_year)
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

    # Get initial data for server-side render
    drivers = []
    positions = []
    laps = []
    weather = {}
    race_control = []
    stints = []
    track_outline = []

    if session_key:
        drivers = client.get_openf1_drivers(session_key)
        positions = client.get_positions(session_key)
        laps = client.get_openf1_laps(session_key)
        weather = client.get_weather(session_key)
        race_control = client.get_race_control(session_key)
        stints = client.get_stints(session_key)
        track_outline = client.get_track_outline(session_key)

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
        openf1_auth_configured=client.openf1_auth_configured,
        openf1_error=client.openf1_last_error,
    )


@app.route("/api/live/<int:session_key>")
def api_live_data(session_key):
    """AJAX endpoint — returns live data for polling.
    Use ?full=1 for all data (drivers, weather, stints, race_control).
    Default returns only fast-changing data (positions, locations, intervals).
    """
    full = request.args.get("full", "0") == "1"

    # Always fetch fast-changing data
    positions = client.get_positions(session_key)
    locations = client.get_car_locations(session_key)
    intervals = client.get_intervals(session_key)

    result = {
        "positions": positions,
        "locations": locations,
        "intervals": intervals,
    }

    # Only include slow-changing data on full refresh
    if full:
        result["drivers"] = client.get_openf1_drivers(session_key)
        result["laps"] = client.get_openf1_laps(session_key)
        result["weather"] = client.get_weather(session_key)
        result["race_control"] = client.get_race_control(session_key)
        result["stints"] = client.get_stints(session_key)

    # Do not report an authentication failure as a successful empty update.
    # The browser can then surface a connection warning instead of appearing to
    # be connected with no cars or timing data.
    if client.openf1_last_error:
        result["error"] = client.openf1_last_error
        result["authentication_required"] = not client.openf1_auth_configured
        return jsonify(result), 503

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


# ── Main ──────────────────────────────────────────────────────────

# Ensure database tables exist regardless of how the app is started
# (python app.py, flask run, gunicorn, etc.)
init_db()

if __name__ == "__main__":
    print("🏎️  F1 Dashboard starting on http://localhost:8050")
    app.run(host="127.0.0.1", port=8050)
