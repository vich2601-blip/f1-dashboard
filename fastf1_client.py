"""
FastF1 Data Client — High-performance wrapper around the FastF1 library.

Provides reliable access to Formula 1 telemetry, lap data, stint data,
and session information without requiring any authentication.  FastF1
reads directly from the official F1 Live Timing backend and caches all
data to disk for near-zero repeat-query latency.
"""

import os
import threading
import fastf1
import numpy as np
import pandas as pd
from datetime import datetime

# Enable disk cache in the project directory
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fastf1_cache")
os.makedirs(_CACHE_DIR, exist_ok=True)
fastf1.Cache.enable_cache(_CACHE_DIR)


class FastF1Client:
    """Thread-safe wrapper around FastF1 for the F1 Dashboard."""

    def __init__(self):
        self._session_cache = {}          # (year, round, session_type) → Session
        self._session_cache_lock = threading.Lock()

    # ── Session Loading ──────────────────────────────────────────

    def _load_session(self, year, round_num, session_type="R"):
        """Load and cache a FastF1 session.
        session_type: 'R'=Race, 'Q'=Qualifying, 'S'=Sprint, 'FP1'/'FP2'/'FP3'
        """
        key = (year, round_num, session_type)
        with self._session_cache_lock:
            if key in self._session_cache:
                return self._session_cache[key]

        try:
            session = fastf1.get_session(year, round_num, session_type)
            session.load(
                telemetry=True,
                laps=True,
                weather=True,
                messages=True,
            )
            with self._session_cache_lock:
                self._session_cache[key] = session
            return session
        except Exception as e:
            print(f"[FastF1] Failed to load session {year} R{round_num} {session_type}: {e}")
            return None

    def _load_session_by_key(self, year, round_num, session_type="R"):
        """Convenience wrapper that returns None on failure."""
        return self._load_session(year, round_num, session_type)

    # ── Schedule ─────────────────────────────────────────────────

    def get_schedule(self, year):
        """Get the full race calendar for a season."""
        try:
            schedule = fastf1.get_event_schedule(year)
            events = []
            for _, row in schedule.iterrows():
                round_num = int(row.get("RoundNumber", 0))
                if round_num == 0:
                    continue  # Skip testing events
                events.append({
                    "round": round_num,
                    "race_name": str(row.get("EventName", "")),
                    "country": str(row.get("Country", "")),
                    "location": str(row.get("Location", "")),
                    "event_date": str(row.get("EventDate", "")),
                    "event_format": str(row.get("EventFormat", "")),
                    "sessions": {
                        "fp1": str(row.get("Session1Date", "")),
                        "fp2": str(row.get("Session2Date", "")),
                        "fp3": str(row.get("Session3Date", "")),
                        "qualifying": str(row.get("Session4Date", "")),
                        "race": str(row.get("Session5Date", "")),
                    },
                })
            return events
        except Exception as e:
            print(f"[FastF1] Schedule error for {year}: {e}")
            return []

    # ── Available Sessions ───────────────────────────────────────

    def get_available_sessions(self, year):
        """Get all sessions available for a year with metadata."""
        try:
            schedule = fastf1.get_event_schedule(year)
            sessions = []
            for _, row in schedule.iterrows():
                round_num = int(row.get("RoundNumber", 0))
                if round_num == 0:
                    continue
                for stype, sname in [
                    ("FP1", "Practice 1"), ("FP2", "Practice 2"), ("FP3", "Practice 3"),
                    ("Q", "Qualifying"), ("S", "Sprint"), ("R", "Race"),
                ]:
                    sessions.append({
                        "year": year,
                        "round": round_num,
                        "event_name": str(row.get("EventName", "")),
                        "country": str(row.get("Country", "")),
                        "location": str(row.get("Location", "")),
                        "session_type": stype,
                        "session_name": sname,
                    })
            return sessions
        except Exception as e:
            print(f"[FastF1] Available sessions error: {e}")
            return []

    # ── Lap Data ─────────────────────────────────────────────────

    def get_lap_data(self, year, round_num, session_type="R"):
        """Get all lap data for a session (lap times, sectors, compounds, etc.)."""
        session = self._load_session(year, round_num, session_type)
        if session is None:
            return []

        try:
            laps = session.laps
            if laps is None or laps.empty:
                return []

            result = []
            for _, lap in laps.iterrows():
                lap_time = lap.get("LapTime")
                s1 = lap.get("Sector1Time")
                s2 = lap.get("Sector2Time")
                s3 = lap.get("Sector3Time")

                result.append({
                    "driver_number": int(lap.get("DriverNumber", 0)) if pd.notna(lap.get("DriverNumber")) else 0,
                    "driver": str(lap.get("Driver", "")),
                    "team": str(lap.get("Team", "")),
                    "lap_number": int(lap.get("LapNumber", 0)) if pd.notna(lap.get("LapNumber")) else 0,
                    "lap_time": lap_time.total_seconds() if pd.notna(lap_time) else None,
                    "sector_1": s1.total_seconds() if pd.notna(s1) else None,
                    "sector_2": s2.total_seconds() if pd.notna(s2) else None,
                    "sector_3": s3.total_seconds() if pd.notna(s3) else None,
                    "compound": str(lap.get("Compound", "")) if pd.notna(lap.get("Compound")) else "",
                    "tyre_life": int(lap.get("TyreLife", 0)) if pd.notna(lap.get("TyreLife")) else 0,
                    "stint": int(lap.get("Stint", 0)) if pd.notna(lap.get("Stint")) else 0,
                    "fresh_tyre": bool(lap.get("FreshTyre", False)),
                    "is_personal_best": bool(lap.get("IsPersonalBest", False)),
                    "position": int(lap.get("Position", 0)) if pd.notna(lap.get("Position")) else 0,
                    "deleted": bool(lap.get("Deleted", False)),
                    "track_status": str(lap.get("TrackStatus", "")) if pd.notna(lap.get("TrackStatus")) else "",
                })
            return result
        except Exception as e:
            print(f"[FastF1] Lap data error: {e}")
            return []

    # ── Driver Telemetry (Speed, Throttle, Brake, Gear, RPM, DRS) ─

    def get_driver_telemetry(self, year, round_num, driver, lap_number, session_type="R"):
        """Get high-frequency telemetry for a specific driver and lap.
        Returns list of dicts with: distance, speed, throttle, brake, gear, rpm, drs, x, y, z.
        """
        session = self._load_session(year, round_num, session_type)
        if session is None:
            return []

        try:
            laps = session.laps
            driver_laps = laps.pick_drivers(driver)
            if driver_laps.empty:
                return []

            lap = driver_laps[driver_laps["LapNumber"] == lap_number]
            if lap.empty:
                return []

            telemetry = lap.iloc[0].get_telemetry()
            if telemetry is None or telemetry.empty:
                return []

            # Downsample to ~200 points for browser performance
            step = max(1, len(telemetry) // 200)
            sampled = telemetry.iloc[::step]

            result = []
            for _, row in sampled.iterrows():
                result.append({
                    "distance": float(row.get("Distance", 0)) if pd.notna(row.get("Distance")) else 0,
                    "speed": float(row.get("Speed", 0)) if pd.notna(row.get("Speed")) else 0,
                    "throttle": float(row.get("Throttle", 0)) if pd.notna(row.get("Throttle")) else 0,
                    "brake": float(row.get("Brake", 0)) if pd.notna(row.get("Brake")) else 0,
                    "gear": int(row.get("nGear", 0)) if pd.notna(row.get("nGear")) else 0,
                    "rpm": float(row.get("RPM", 0)) if pd.notna(row.get("RPM")) else 0,
                    "drs": int(row.get("DRS", 0)) if pd.notna(row.get("DRS")) else 0,
                    "x": float(row.get("X", 0)) if pd.notna(row.get("X")) else 0,
                    "y": float(row.get("Y", 0)) if pd.notna(row.get("Y")) else 0,
                    "z": float(row.get("Z", 0)) if pd.notna(row.get("Z")) else 0,
                })
            return result
        except Exception as e:
            print(f"[FastF1] Telemetry error for {driver} lap {lap_number}: {e}")
            return []

    # ── Two-Driver Telemetry Comparison ──────────────────────────

    def get_telemetry_comparison(self, year, round_num, driver1, driver2, lap1=None, lap2=None, session_type="R"):
        """Compare telemetry for two drivers on their fastest (or specified) laps.
        Returns dict with d1/d2 telemetry arrays and lap info.
        """
        session = self._load_session(year, round_num, session_type)
        if session is None:
            return {"error": "Session not available"}

        try:
            laps = session.laps

            def get_driver_data(driver_id, target_lap):
                d_laps = laps.pick_drivers(driver_id)
                if d_laps.empty:
                    return None, None

                if target_lap:
                    # pick_lap returns a Laps DataFrame subset — grab the first match
                    lap_subset = d_laps[d_laps["LapNumber"] == target_lap]
                    if lap_subset.empty:
                        return None, None
                    lap_row = lap_subset.iloc[0]
                else:
                    # pick_fastest returns a single Lap (Series) or None
                    lap_row = d_laps.pick_fastest()
                    if lap_row is None:
                        return None, None

                telemetry = lap_row.get_telemetry()
                if telemetry is None or telemetry.empty:
                    return None, None

                step = max(1, len(telemetry) // 200)
                sampled = telemetry.iloc[::step]

                lap_time = lap_row.get("LapTime")
                info = {
                    "driver": str(lap_row.get("Driver", driver_id)),
                    "driver_number": int(lap_row.get("DriverNumber", 0)) if pd.notna(lap_row.get("DriverNumber")) else 0,
                    "team": str(lap_row.get("Team", "")),
                    "lap_number": int(lap_row.get("LapNumber", 0)) if pd.notna(lap_row.get("LapNumber")) else 0,
                    "lap_time": lap_time.total_seconds() if pd.notna(lap_time) else None,
                    "compound": str(lap_row.get("Compound", "")) if pd.notna(lap_row.get("Compound")) else "",
                }

                telemetry_data = []
                for _, row in sampled.iterrows():
                    telemetry_data.append({
                        "distance": float(row.get("Distance", 0)) if pd.notna(row.get("Distance")) else 0,
                        "speed": float(row.get("Speed", 0)) if pd.notna(row.get("Speed")) else 0,
                        "throttle": float(row.get("Throttle", 0)) if pd.notna(row.get("Throttle")) else 0,
                        "brake": float(row.get("Brake", 0)) if pd.notna(row.get("Brake")) else 0,
                        "gear": int(row.get("nGear", 0)) if pd.notna(row.get("nGear")) else 0,
                        "rpm": float(row.get("RPM", 0)) if pd.notna(row.get("RPM")) else 0,
                        "drs": int(row.get("DRS", 0)) if pd.notna(row.get("DRS")) else 0,
                    })

                return info, telemetry_data

            d1_info, d1_telemetry = get_driver_data(driver1, lap1)
            d2_info, d2_telemetry = get_driver_data(driver2, lap2)

            return {
                "d1": {"info": d1_info, "telemetry": d1_telemetry or []},
                "d2": {"info": d2_info, "telemetry": d2_telemetry or []},
            }
        except Exception as e:
            print(f"[FastF1] Comparison error: {e}")
            return {"error": str(e)}

    # ── Session Results Summary ──────────────────────────────────

    def get_session_results(self, year, round_num, session_type="R"):
        """Get final classification with driver info."""
        session = self._load_session(year, round_num, session_type)
        if session is None:
            return []

        try:
            results = session.results
            if results is None or results.empty:
                return []

            output = []
            for _, row in results.iterrows():
                output.append({
                    "position": int(row.get("Position", 0)) if pd.notna(row.get("Position")) else 0,
                    "driver_number": int(row.get("DriverNumber", 0)) if pd.notna(row.get("DriverNumber")) else 0,
                    "abbreviation": str(row.get("Abbreviation", "")),
                    "full_name": str(row.get("FullName", "")),
                    "team_name": str(row.get("TeamName", "")),
                    "team_color": str(row.get("TeamColor", "888888")),
                    "grid_position": int(row.get("GridPosition", 0)) if pd.notna(row.get("GridPosition")) else 0,
                    "status": str(row.get("Status", "")),
                    "points": float(row.get("Points", 0)) if pd.notna(row.get("Points")) else 0,
                })
            return output
        except Exception as e:
            print(f"[FastF1] Results error: {e}")
            return []

    # ── Weather Data ─────────────────────────────────────────────

    def get_weather_data(self, year, round_num, session_type="R"):
        """Get weather readings across the session."""
        session = self._load_session(year, round_num, session_type)
        if session is None:
            return {}

        try:
            weather = session.weather_data
            if weather is None or weather.empty:
                return {}

            latest = weather.iloc[-1]
            return {
                "air_temperature": float(latest.get("AirTemp", 0)) if pd.notna(latest.get("AirTemp")) else None,
                "track_temperature": float(latest.get("TrackTemp", 0)) if pd.notna(latest.get("TrackTemp")) else None,
                "humidity": float(latest.get("Humidity", 0)) if pd.notna(latest.get("Humidity")) else None,
                "pressure": float(latest.get("Pressure", 0)) if pd.notna(latest.get("Pressure")) else None,
                "rainfall": bool(latest.get("Rainfall", False)),
                "wind_direction": float(latest.get("WindDirection", 0)) if pd.notna(latest.get("WindDirection")) else None,
                "wind_speed": float(latest.get("WindSpeed", 0)) if pd.notna(latest.get("WindSpeed")) else None,
            }
        except Exception as e:
            print(f"[FastF1] Weather error: {e}")
            return {}

    # ── Race Control Messages ────────────────────────────────────

    def get_race_control_messages(self, year, round_num, session_type="R"):
        """Get all race control messages (flags, penalties, etc.)."""
        session = self._load_session(year, round_num, session_type)
        if session is None:
            return []

        try:
            messages = session.race_control_messages
            if messages is None or messages.empty:
                return []

            result = []
            for _, msg in messages.iterrows():
                ts = msg.get("Time")
                result.append({
                    "time": str(ts) if pd.notna(ts) else "",
                    "category": str(msg.get("Category", "")) if pd.notna(msg.get("Category")) else "",
                    "flag": str(msg.get("Flag", "")) if pd.notna(msg.get("Flag")) else "",
                    "scope": str(msg.get("Scope", "")) if pd.notna(msg.get("Scope")) else "",
                    "message": str(msg.get("Message", "")) if pd.notna(msg.get("Message")) else "",
                    "lap_number": int(msg.get("Lap", 0)) if pd.notna(msg.get("Lap")) else None,
                    "driver_number": int(msg.get("RacingNumber", 0)) if pd.notna(msg.get("RacingNumber")) else None,
                })
            return result
        except Exception as e:
            print(f"[FastF1] Race control error: {e}")
            return []

    # ── Stint Summary ────────────────────────────────────────────

    def get_stint_data(self, year, round_num, session_type="R"):
        """Get tyre stint summary per driver from lap data."""
        session = self._load_session(year, round_num, session_type)
        if session is None:
            return []

        try:
            laps = session.laps
            if laps is None or laps.empty:
                return []

            drivers = laps["Driver"].unique()
            result = []
            for driver in drivers:
                d_laps = laps.pick_drivers(driver)
                if d_laps.empty:
                    continue

                stints = []
                for stint_num in sorted(d_laps["Stint"].dropna().unique()):
                    stint_laps = d_laps[d_laps["Stint"] == stint_num]
                    if stint_laps.empty:
                        continue
                    compound = str(stint_laps.iloc[0].get("Compound", "UNKNOWN"))
                    lap_start = int(stint_laps["LapNumber"].min())
                    lap_end = int(stint_laps["LapNumber"].max())
                    tyre_age = int(stint_laps.iloc[0].get("TyreLife", 0)) if pd.notna(stint_laps.iloc[0].get("TyreLife")) else 0
                    stints.append({
                        "stint_number": int(stint_num),
                        "compound": compound,
                        "lap_start": lap_start,
                        "lap_end": lap_end,
                        "tyre_age_at_start": tyre_age,
                        "laps": lap_end - lap_start + 1,
                    })

                driver_row = d_laps.iloc[0]
                team_color = str(session.results.loc[
                    session.results["Abbreviation"] == driver, "TeamColor"
                ].values[0]) if not session.results.empty and driver in session.results["Abbreviation"].values else "888888"

                result.append({
                    "driver": driver,
                    "driver_number": int(driver_row.get("DriverNumber", 0)) if pd.notna(driver_row.get("DriverNumber")) else 0,
                    "team": str(driver_row.get("Team", "")),
                    "team_color": team_color,
                    "stints": stints,
                })
            return result
        except Exception as e:
            print(f"[FastF1] Stint data error: {e}")
            return []

    # ── Fastest Laps Summary ─────────────────────────────────────

    def get_fastest_laps(self, year, round_num, session_type="R"):
        """Get the fastest lap for each driver in the session."""
        session = self._load_session(year, round_num, session_type)
        if session is None:
            return []

        try:
            laps = session.laps
            if laps is None or laps.empty:
                return []

            result = []
            for driver in laps["Driver"].unique():
                d_laps = laps.pick_drivers(driver)
                fastest = d_laps.pick_fastest()
                if fastest is None:
                    continue

                lap_time = fastest.get("LapTime")
                s1 = fastest.get("Sector1Time")
                s2 = fastest.get("Sector2Time")
                s3 = fastest.get("Sector3Time")

                result.append({
                    "driver": str(driver),
                    "driver_number": int(fastest.get("DriverNumber", 0)) if pd.notna(fastest.get("DriverNumber")) else 0,
                    "team": str(fastest.get("Team", "")),
                    "lap_number": int(fastest.get("LapNumber", 0)) if pd.notna(fastest.get("LapNumber")) else 0,
                    "lap_time": lap_time.total_seconds() if pd.notna(lap_time) else None,
                    "sector_1": s1.total_seconds() if pd.notna(s1) else None,
                    "sector_2": s2.total_seconds() if pd.notna(s2) else None,
                    "sector_3": s3.total_seconds() if pd.notna(s3) else None,
                    "compound": str(fastest.get("Compound", "")) if pd.notna(fastest.get("Compound")) else "",
                    "speed_trap": float(fastest.get("SpeedST", 0)) if pd.notna(fastest.get("SpeedST")) else None,
                })

            result.sort(key=lambda x: x["lap_time"] if x["lap_time"] else 9999)
            return result
        except Exception as e:
            print(f"[FastF1] Fastest laps error: {e}")
            return []

    # ── Position Changes Over Race ───────────────────────────────

    def get_position_changes(self, year, round_num, session_type="R"):
        """Get lap-by-lap position data for all drivers."""
        session = self._load_session(year, round_num, session_type)
        if session is None:
            return {}

        try:
            laps = session.laps
            if laps is None or laps.empty:
                return {}

            result = {}
            for driver in laps["Driver"].unique():
                d_laps = laps.pick_drivers(driver).sort_values("LapNumber")
                positions = []
                for _, lap in d_laps.iterrows():
                    pos = lap.get("Position")
                    if pd.notna(pos):
                        positions.append({
                            "lap": int(lap.get("LapNumber", 0)),
                            "position": int(pos),
                        })

                if positions:
                    driver_row = d_laps.iloc[0]
                    team_color = str(session.results.loc[
                        session.results["Abbreviation"] == driver, "TeamColor"
                    ].values[0]) if not session.results.empty and driver in session.results["Abbreviation"].values else "888888"

                    result[driver] = {
                        "positions": positions,
                        "team_color": team_color,
                        "team": str(driver_row.get("Team", "")),
                    }
            return result
        except Exception as e:
            print(f"[FastF1] Position changes error: {e}")
            return {}
