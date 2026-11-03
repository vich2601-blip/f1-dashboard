"""
F1 Data Client — Wrapper around Jolpica and Formula 1 Live Timing with caching.
Performance-optimized with in-memory cache and thread-safe rate limiting.
"""

import base64
import json
import math
import os
import time
import threading
import requests
import zlib
from datetime import datetime, timezone, timedelta
from database import get_cached, upsert, clear_table


class F1DataClient:
    """Unified client for Jolpica and Formula 1 Live Timing with caching."""

    JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1"
    F1_LIVE_BASE = "https://livetiming.formula1.com/static"
    OPENF1_BASE = "https://api.openf1.org/v1"

    # SQLite cache durations (in hours)
    CACHE_LIVE = 1
    CACHE_HISTORICAL = 24
    CACHE_SCHEDULE = 12

    # In-memory cache TTLs (in seconds)
    MEM_TTL_POSITIONS = 15       # Positions change each lap
    MEM_TTL_WEATHER = 60         # Weather changes slowly
    MEM_TTL_RACE_CONTROL = 20    # Flags/messages
    MEM_TTL_INTERVALS = 15       # Gap data
    MEM_TTL_LOCATIONS = 10       # Car coordinates
    MEM_TTL_DRIVERS = 300        # Driver metadata rarely changes
    MEM_TTL_STINTS = 30          # Tyre strategy
    MEM_TTL_LAPS = 15            # Lap/sector times
    MEM_TTL_TELEMETRY = 5        # Telemetry data

    # Maximum in-memory cache entries before LRU eviction
    MEM_CACHE_MAX_SIZE = 200

    def __init__(self):
        self._mem_cache = {}
        self._cache_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._openf1_auth_lock = threading.Lock()
        self._last_request_time = 0
        self._backoff_until = 0  # Timestamp until which we should back off (429 handling)
        self._session = requests.Session()  # Use HTTP Keep-Alive to avoid TCP exhaustion
        # OpenF1 requires authentication for real-time data. A long-lived
        # username/password pair can refresh tokens automatically; an access
        # token is also accepted for deployments that manage it externally.
        self._openf1_access_token = os.getenv("OPENF1_ACCESS_TOKEN")
        self._openf1_username = os.getenv("OPENF1_USERNAME")
        self._openf1_password = os.getenv("OPENF1_PASSWORD")
        self._openf1_token_expires_at = 0
        self._openf1_last_error = None
        self._live_session_paths = {}
        self._live_timing_last_error = None

    # ── Rate Limiter ─────────────────────────────────────────────

    def _rate_limit(self):
        """Ensure at least 0.5s between API calls. Thread-safe.
        Respects backoff from 429 responses."""
        with self._rate_lock:
            now = time.time()
            # If we're in a backoff period from a 429, wait it out
            if now < self._backoff_until:
                wait = self._backoff_until - now
                time.sleep(wait)
            else:
                elapsed = now - self._last_request_time
                if elapsed < 1.2:
                    time.sleep(1.2 - elapsed)
            self._last_request_time = time.time()

    @property
    def openf1_auth_configured(self):
        """Whether credentials are available for OpenF1 real-time requests."""
        return bool(
            self._openf1_access_token
            or (self._openf1_username and self._openf1_password)
        )

    @property
    def openf1_last_error(self):
        """Latest OpenF1 authentication error, safe to display to a user."""
        return self._openf1_last_error

    def _get_openf1_access_token(self, force_refresh=False):
        """Return a valid OpenF1 OAuth token without exposing credentials."""
        # A supplied token is managed by the deployment and cannot be refreshed
        # here because no username/password was provided.
        if self._openf1_access_token and not (
            self._openf1_username and self._openf1_password
        ):
            return self._openf1_access_token

        if not (self._openf1_username and self._openf1_password):
            return None

        with self._openf1_auth_lock:
            if (
                not force_refresh
                and self._openf1_access_token
                and time.time() < self._openf1_token_expires_at
            ):
                return self._openf1_access_token

            try:
                response = self._session.post(
                    "https://api.openf1.org/token",
                    data={
                        "username": self._openf1_username,
                        "password": self._openf1_password,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=15,
                )
                response.raise_for_status()
                payload = response.json()
                token = payload.get("access_token")
                if not token:
                    raise ValueError("OpenF1 token response did not contain an access token")

                try:
                    expires_in = max(int(payload.get("expires_in", 3600)), 60)
                except (TypeError, ValueError):
                    expires_in = 3600

                self._openf1_access_token = token
                # Refresh one minute early so an in-flight request does not use
                # an expired token.
                self._openf1_token_expires_at = time.time() + expires_in - 60
                self._openf1_last_error = None
                return token
            except (requests.RequestException, ValueError) as error:
                self._openf1_access_token = None
                self._openf1_token_expires_at = 0
                self._openf1_last_error = (
                    "OpenF1 authentication failed. Check OPENF1_USERNAME and "
                    "OPENF1_PASSWORD."
                )
                print(f"[OpenF1 Authentication Error] {error}")
                return None

    def _openf1_headers(self, force_token_refresh=False):
        """Build request headers for OpenF1, including OAuth when configured."""
        headers = {"Accept": "application/json"}
        token = self._get_openf1_access_token(force_refresh=force_token_refresh)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _record_openf1_unauthorized(self):
        """Record a concise, actionable message after an OpenF1 401 response."""
        self._openf1_last_error = (
            "OpenF1 requires authentication for this data. Configure "
            "OPENF1_ACCESS_TOKEN, or OPENF1_USERNAME and OPENF1_PASSWORD."
        )

    def _fetch_json(self, url, params=None, timeout=15):
        """Fetch JSON from a URL with rate limiting, error handling, and 429 backoff."""
        # Build a stable cache key for 404 suppression
        import urllib.parse
        _404_key = f"_404_{url}_{urllib.parse.urlencode(sorted((params or {}).items()))}"
        if self._mem_get(_404_key, 60) is not None:  # suppress for 60s after a 404
            return None

        max_retries = 3
        for attempt in range(max_retries):
            self._rate_limit()
            try:
                is_openf1 = url.startswith(self.OPENF1_BASE)
                headers = self._openf1_headers() if is_openf1 else None
                resp = self._session.get(url, params=params, timeout=timeout, headers=headers)

                # Tokens can expire between requests. When credentials are supplied,
                # refresh and retry once before treating the response as a failure.
                if (
                    resp.status_code == 401
                    and is_openf1
                    and self._openf1_username
                    and self._openf1_password
                ):
                    headers = self._openf1_headers(force_token_refresh=True)
                    resp = self._session.get(url, params=params, timeout=timeout, headers=headers)

                if resp.status_code == 429:
                    # Back off for 10 seconds on rate limit hit
                    with self._rate_lock:
                        self._backoff_until = time.time() + 10
                    print(f"[API Rate Limited] {url} — backing off 10s")
                    if attempt < max_retries - 1:
                        time.sleep(2)
                        continue
                    return None
                if resp.status_code == 404:
                    # Cache the 404 for 60s to avoid hammering the API on stale session keys
                    self._mem_set(_404_key, True)
                    print(f"[API 404 — suppressing 60s] {url}")
                    return None
                if resp.status_code == 401 and is_openf1:
                    self._record_openf1_unauthorized()
                resp.raise_for_status()
                if is_openf1:
                    self._openf1_last_error = None
                return resp.json()
            except (requests.RequestException, ConnectionResetError) as e:
                # Discard broken connection pool if connection reset occurred
                try:
                    self._session.close()
                except Exception:
                    pass
                self._session = requests.Session()
                self._session.headers.update({"User-Agent": "F1Dashboard/2.0"})
                if attempt < max_retries - 1:
                    print(f"[API Retry {attempt+1}/{max_retries}] {url}: {e}")
                    time.sleep(2 * (attempt + 1))
                    continue
                print(f"[API Error] {url}: {e}")
                return None

    @property
    def live_timing_last_error(self):
        """Latest Formula 1 Live Timing error, safe to display to a user."""
        return self._live_timing_last_error

    def _fetch_f1_live_json(self, url, timeout=20):
        """Fetch a Formula 1 Live Timing feed, accounting for its UTF-8 BOM."""
        self._rate_limit()
        try:
            response = self._session.get(
                url,
                headers={"Accept": "application/json"},
                timeout=timeout,
            )
            response.raise_for_status()
            data = json.loads(response.content.decode("utf-8-sig"))
            self._live_timing_last_error = None
            return data
        except (requests.RequestException, UnicodeDecodeError, json.JSONDecodeError) as error:
            self._live_timing_last_error = (
                "Formula 1 Live Timing is temporarily unavailable. Please try again shortly."
            )
            print(f"[F1 Live Timing Error] {url}: {error}")
            return None

    # ── In-Memory Cache ──────────────────────────────────────────

    def _mem_get(self, key, max_age_seconds=10):
        """Get from in-memory cache. Returns None if stale/missing."""
        with self._cache_lock:
            entry = self._mem_cache.get(key)
            if entry is not None:
                data, ts = entry
                if time.time() - ts < max_age_seconds:
                    return data
        return None

    def _mem_set(self, key, data):
        """Store in in-memory cache with current timestamp. Evicts oldest entries if cache exceeds max size."""
        with self._cache_lock:
            self._mem_cache[key] = (data, time.time())
            # LRU-style eviction: remove oldest entries if cache is too large
            if len(self._mem_cache) > self.MEM_CACHE_MAX_SIZE:
                # Sort by timestamp, remove oldest 20%
                entries = sorted(self._mem_cache.items(), key=lambda x: x[1][1])
                evict_count = len(entries) // 5
                for k, _ in entries[:evict_count]:
                    del self._mem_cache[k]

    # ══════════════════════════════════════════════════════════════
    # Jolpica API Methods (historical / Ergast-style data)
    # ══════════════════════════════════════════════════════════════

    # ── Schedule ──────────────────────────────────────────────────

    def get_schedule(self, season=2026):
        """Get the full race calendar for a season."""
        cached = get_cached("races", {"season": season}, self.CACHE_SCHEDULE)
        if cached:
            return sorted(cached, key=lambda x: x["round"])

        url = f"{self.JOLPICA_BASE}/{season}.json"
        data = self._fetch_json(url)
        if not data:
            return []

        races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        now = datetime.now(timezone.utc).isoformat()

        rows = []
        for race in races:
            circuit = race.get("Circuit", {})
            location = circuit.get("Location", {})
            has_sprint = 1 if "Sprint" in race else 0

            rows.append({
                "season": int(race.get("season", season)),
                "round": int(race.get("round", 0)),
                "race_name": race.get("raceName", ""),
                "circuit_name": circuit.get("circuitName", ""),
                "circuit_id": circuit.get("circuitId", ""),
                "locality": location.get("locality", ""),
                "country": location.get("country", ""),
                "date": race.get("date", ""),
                "time": race.get("time", ""),
                "url": race.get("url", ""),
                "lat": float(location.get("lat", 0)),
                "lng": float(location.get("long", 0)),
                "has_sprint": has_sprint,
                "fetched_at": now,
            })

        if rows:
            clear_table("races", {"season": season})
            upsert("races", rows)

        return sorted(rows, key=lambda x: x["round"])

    # ── Race Results ──────────────────────────────────────────────

    def get_race_results(self, season, round_num):
        """Get full race results for a specific round."""
        cached = get_cached("results", {"season": season, "round": round_num}, self.CACHE_HISTORICAL)
        if cached:
            return sorted(cached, key=lambda x: x["position"])

        url = f"{self.JOLPICA_BASE}/{season}/{round_num}/results.json"
        data = self._fetch_json(url)
        if not data:
            return []

        races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        if not races:
            return []

        results = races[0].get("Results", [])
        now = datetime.now(timezone.utc).isoformat()

        rows = []
        for r in results:
            driver = r.get("Driver", {})
            constructor = r.get("Constructor", {})
            fastest_lap = r.get("FastestLap", {})
            time_info = r.get("Time", {})

            rows.append({
                "season": season,
                "round": round_num,
                "position": int(r.get("position", 0)),
                "driver_id": driver.get("driverId", ""),
                "driver_code": driver.get("code", ""),
                "driver_name": f"{driver.get('givenName', '')} {driver.get('familyName', '')}",
                "constructor": constructor.get("name", ""),
                "grid": int(r.get("grid", 0)),
                "laps": int(r.get("laps", 0)),
                "status": r.get("status", ""),
                "time_text": time_info.get("time", r.get("status", "")),
                "points": float(r.get("points", 0)),
                "fastest_lap_time": fastest_lap.get("Time", {}).get("time", ""),
                "fastest_lap_rank": int(fastest_lap.get("rank", 0)) if fastest_lap.get("rank") else None,
                "fetched_at": now,
            })

        if rows:
            clear_table("results", {"season": season, "round": round_num})
            upsert("results", rows)

        return sorted(rows, key=lambda x: x["position"])

    def get_season_results(self, season):
        """Get all race results for a season from cache or API in a single query."""
        cached = get_cached("results", {"season": season}, self.CACHE_HISTORICAL)
        if cached:
            return cached

        url = f"{self.JOLPICA_BASE}/{season}/results.json?limit=1000"
        data = self._fetch_json(url)
        if not data:
            return []

        races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        now = datetime.now(timezone.utc).isoformat()

        all_rows = []
        for race in races:
            round_num = int(race.get("round", 0))
            results = race.get("Results", [])
            for r in results:
                driver = r.get("Driver", {})
                constructor = r.get("Constructor", {})
                fastest_lap = r.get("FastestLap", {})
                time_info = r.get("Time", {})

                all_rows.append({
                    "season": season,
                    "round": round_num,
                    "position": int(r.get("position", 0)),
                    "driver_id": driver.get("driverId", ""),
                    "driver_code": driver.get("code", ""),
                    "driver_name": f"{driver.get('givenName', '')} {driver.get('familyName', '')}",
                    "constructor": constructor.get("name", ""),
                    "grid": int(r.get("grid", 0)),
                    "laps": int(r.get("laps", 0)),
                    "status": r.get("status", ""),
                    "time_text": time_info.get("time", r.get("status", "")),
                    "points": float(r.get("points", 0)),
                    "fastest_lap_time": fastest_lap.get("Time", {}).get("time", ""),
                    "fastest_lap_rank": int(fastest_lap.get("rank", 0)) if fastest_lap.get("rank") else None,
                    "fetched_at": now,
                })

        if all_rows:
            clear_table("results", {"season": season})
            upsert("results", all_rows)

        return all_rows

    def get_season_qualifying(self, season):
        """Get all qualifying results for a season from cache or API in a single query."""
        cached = get_cached("qualifying", {"season": season}, self.CACHE_HISTORICAL)
        if cached:
            return cached

        url = f"{self.JOLPICA_BASE}/{season}/qualifying.json?limit=1000"
        data = self._fetch_json(url)
        if not data:
            return []

        races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        now = datetime.now(timezone.utc).isoformat()

        all_rows = []
        for race in races:
            round_num = int(race.get("round", 0))
            quali = race.get("QualifyingResults", [])
            for q in quali:
                driver = q.get("Driver", {})
                constructor = q.get("Constructor", {})

                all_rows.append({
                    "season": season,
                    "round": round_num,
                    "position": int(q.get("position", 0)),
                    "driver_code": driver.get("code", ""),
                    "driver_name": f"{driver.get('givenName', '')} {driver.get('familyName', '')}",
                    "constructor": constructor.get("name", ""),
                    "q1": q.get("Q1", ""),
                    "q2": q.get("Q2", ""),
                    "q3": q.get("Q3", ""),
                    "fetched_at": now,
                })

        if all_rows:
            clear_table("qualifying", {"season": season})
            upsert("qualifying", all_rows)

        return all_rows

    # ── Qualifying ────────────────────────────────────────────────

    def get_qualifying(self, season, round_num):
        """Get qualifying results for a specific round."""
        cached = get_cached("qualifying", {"season": season, "round": round_num}, self.CACHE_HISTORICAL)
        if cached:
            return sorted(cached, key=lambda x: x["position"])

        url = f"{self.JOLPICA_BASE}/{season}/{round_num}/qualifying.json"
        data = self._fetch_json(url)
        if not data:
            return []

        races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        if not races:
            return []

        qualifying = races[0].get("QualifyingResults", [])
        now = datetime.now(timezone.utc).isoformat()

        rows = []
        for q in qualifying:
            driver = q.get("Driver", {})
            constructor = q.get("Constructor", {})

            rows.append({
                "season": season,
                "round": round_num,
                "position": int(q.get("position", 0)),
                "driver_code": driver.get("code", ""),
                "driver_name": f"{driver.get('givenName', '')} {driver.get('familyName', '')}",
                "constructor": constructor.get("name", ""),
                "q1": q.get("Q1", ""),
                "q2": q.get("Q2", ""),
                "q3": q.get("Q3", ""),
                "fetched_at": now,
            })

        if rows:
            clear_table("qualifying", {"season": season, "round": round_num})
            upsert("qualifying", rows)

        return sorted(rows, key=lambda x: x["position"])

    # ── Driver Standings ──────────────────────────────────────────

    def get_driver_standings(self, season=2026):
        """Get current driver championship standings."""
        cached = get_cached("driver_standings", {"season": season}, self.CACHE_LIVE)
        if cached:
            return sorted(cached, key=lambda x: x["position"])

        url = f"{self.JOLPICA_BASE}/{season}/driverStandings.json"
        data = self._fetch_json(url)
        if not data:
            return []

        standings_lists = (
            data.get("MRData", {})
            .get("StandingsTable", {})
            .get("StandingsLists", [])
        )
        if not standings_lists:
            return []

        standings = standings_lists[0].get("DriverStandings", [])
        round_num = int(standings_lists[0].get("round", 0))
        now = datetime.now(timezone.utc).isoformat()

        rows = []
        for s in standings:
            driver = s.get("Driver", {})
            constructors = s.get("Constructors", [{}])
            constructor_name = constructors[0].get("name", "") if constructors else ""

            rows.append({
                "season": season,
                "round": round_num,
                "position": int(s.get("position", 0)),
                "driver_id": driver.get("driverId", ""),
                "driver_code": driver.get("code", ""),
                "driver_name": f"{driver.get('givenName', '')} {driver.get('familyName', '')}",
                "constructor": constructor_name,
                "points": float(s.get("points", 0)),
                "wins": int(s.get("wins", 0)),
                "nationality": driver.get("nationality", ""),
                "fetched_at": now,
            })

        if rows:
            clear_table("driver_standings", {"season": season})
            upsert("driver_standings", rows)

        return sorted(rows, key=lambda x: x["position"])

    # ── Constructor Standings ─────────────────────────────────────

    def get_constructor_standings(self, season=2026):
        """Get current constructor championship standings."""
        cached = get_cached("constructor_standings", {"season": season}, self.CACHE_LIVE)
        if cached:
            return sorted(cached, key=lambda x: x["position"])

        url = f"{self.JOLPICA_BASE}/{season}/constructorStandings.json"
        data = self._fetch_json(url)
        if not data:
            return []

        standings_lists = (
            data.get("MRData", {})
            .get("StandingsTable", {})
            .get("StandingsLists", [])
        )
        if not standings_lists:
            return []

        standings = standings_lists[0].get("ConstructorStandings", [])
        round_num = int(standings_lists[0].get("round", 0))
        now = datetime.now(timezone.utc).isoformat()

        rows = []
        for s in standings:
            constructor = s.get("Constructor", {})

            rows.append({
                "season": season,
                "round": round_num,
                "position": int(s.get("position", 0)),
                "constructor": constructor.get("name", ""),
                "constructor_id": constructor.get("constructorId", ""),
                "nationality": constructor.get("nationality", ""),
                "points": float(s.get("points", 0)),
                "wins": int(s.get("wins", 0)),
                "fetched_at": now,
            })

        if rows:
            clear_table("constructor_standings", {"season": season})
            upsert("constructor_standings", rows)

        return sorted(rows, key=lambda x: x["position"])

    # ── Lap Times ─────────────────────────────────────────────────

    def get_lap_times(self, season, round_num):
        """Get lap-by-lap timing data for a race."""
        cached = get_cached("lap_times", {"season": season, "round": round_num}, self.CACHE_HISTORICAL)
        if cached:
            return cached

        # Fetch all laps (use large page size to minimize roundtrips)
        all_laps = []
        offset = 0
        limit = 1000

        while True:
            url = f"{self.JOLPICA_BASE}/{season}/{round_num}/laps.json?limit={limit}&offset={offset}"
            data = self._fetch_json(url)
            if not data:
                break

            races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
            if not races:
                break

            laps = races[0].get("Laps", [])
            if not laps:
                break

            all_laps.extend(laps)
            total = int(data.get("MRData", {}).get("total", 0))
            offset += limit
            if offset >= total:
                break

        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for lap in all_laps:
            lap_num = int(lap.get("number", 0))
            for timing in lap.get("Timings", []):
                rows.append({
                    "season": season,
                    "round": round_num,
                    "driver_code": timing.get("driverId", ""),
                    "lap": lap_num,
                    "position": int(timing.get("position", 0)),
                    "time": timing.get("time", ""),
                    "fetched_at": now,
                })

        if rows:
            clear_table("lap_times", {"season": season, "round": round_num})
            upsert("lap_times", rows)

        return rows

    # ── Pit Stops ─────────────────────────────────────────────────

    def get_pit_stops(self, season, round_num):
        """Get pit stop data for a race."""
        cached = get_cached("pit_stops", {"season": season, "round": round_num}, self.CACHE_HISTORICAL)
        if cached:
            return cached

        url = f"{self.JOLPICA_BASE}/{season}/{round_num}/pitstops.json?limit=100"
        data = self._fetch_json(url)
        if not data:
            return []

        races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        if not races:
            return []

        pit_stops = races[0].get("PitStops", [])
        now = datetime.now(timezone.utc).isoformat()

        rows = []
        for ps in pit_stops:
            rows.append({
                "season": season,
                "round": round_num,
                "driver_code": ps.get("driverId", ""),
                "stop": int(ps.get("stop", 0)),
                "lap": int(ps.get("lap", 0)),
                "time": ps.get("time", ""),
                "duration": ps.get("duration", ""),
                "fetched_at": now,
            })

        if rows:
            clear_table("pit_stops", {"season": season, "round": round_num})
            upsert("pit_stops", rows)

        return rows

    # ── Helpers ───────────────────────────────────────────────────

    def get_next_race(self, season=2026):
        """Determine the next upcoming race from the schedule."""
        schedule = self.get_schedule(season)
        now = datetime.now(timezone.utc)

        for race in schedule:
            try:
                race_date_str = race["date"]
                race_time_str = race.get("time", "14:00:00Z")
                if not race_time_str:
                    race_time_str = "14:00:00Z"
                dt_str = f"{race_date_str}T{race_time_str}"
                if not dt_str.endswith("Z"):
                    dt_str += "Z"
                race_dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                if race_dt > now:
                    race["datetime"] = race_dt.isoformat()
                    return race
            except (ValueError, KeyError):
                continue

        return None

    def get_last_completed_races(self, season=2026, count=3):
        """Get the most recent completed races."""
        schedule = self.get_schedule(season)
        now = datetime.now(timezone.utc)

        completed = []
        for race in schedule:
            try:
                race_date_str = race["date"]
                race_time_str = race.get("time", "14:00:00Z")
                if not race_time_str:
                    race_time_str = "14:00:00Z"
                dt_str = f"{race_date_str}T{race_time_str}"
                if not dt_str.endswith("Z"):
                    dt_str += "Z"
                race_dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                if race_dt < now:
                    completed.append(race)
            except (ValueError, KeyError):
                continue

        return completed[-count:] if completed else []

    # ── OpenF1 Session Matcher ────────────────────────────────────

    def match_openf1_session(self, season, round_num):
        """Match a Jolpica (season, round) to an OpenF1 Race session_key.
        Returns the session_key (int) or None if no match is found.
        Uses circuit name and date proximity for matching."""
        cache_key = f"match_of1_{season}_{round_num}"
        mem = self._mem_get(cache_key, 3600)  # cache 1hr
        if mem is not None:
            return mem if mem != -1 else None

        # Get the Jolpica race info
        schedule = self.get_schedule(season)
        race_info = None
        for r in schedule:
            if r["round"] == round_num:
                race_info = r
                break

        if not race_info:
            self._mem_set(cache_key, -1)
            return None

        race_date = race_info.get("date", "")
        circuit_name = race_info.get("circuit_name", "").lower()
        country = race_info.get("country", "").lower()

        # Try to find matching OpenF1 sessions for this year
        # OpenF1 may use different year (e.g. 2025 data for 2025 season)
        for try_year in [season, season - 1]:
            sessions = self.get_openf1_sessions(try_year)
            if not sessions:
                continue

            # Filter to Race sessions only
            race_sessions = [s for s in sessions if s.get("session_type") == "Race"]

            best_match = None
            best_score = 0

            for s in race_sessions:
                score = 0
                s_circuit = (s.get("circuit_short_name") or "").lower()
                s_country = (s.get("country_name") or "").lower()
                s_date = (s.get("date_start") or "")[:10]  # YYYY-MM-DD

                # Date proximity match (strongest signal)
                if race_date and s_date:
                    try:
                        d1 = datetime.strptime(race_date, "%Y-%m-%d")
                        d2 = datetime.strptime(s_date, "%Y-%m-%d")
                        diff_days = abs((d1 - d2).days)
                        if diff_days == 0:
                            score += 10
                        elif diff_days == 1:
                            score += 8
                        elif diff_days == 2:
                            score += 6
                        elif diff_days <= 3:
                            score += 4
                    except Exception:
                        if s_date == race_date:
                            score += 10

                # Country match
                if country and s_country and (country in s_country or s_country in country):
                    score += 5

                # Circuit name fuzzy match
                if circuit_name and s_circuit:
                    # Check for substring overlap
                    circuit_words = set(circuit_name.replace("-", " ").split())
                    s_words = set(s_circuit.replace("-", " ").split())
                    if circuit_words & s_words:
                        score += 3

                if score > best_score:
                    best_score = score
                    best_match = s

            if best_match and best_score >= 5:
                session_key = best_match.get("session_key")
                self._mem_set(cache_key, session_key)
                return session_key

        self._mem_set(cache_key, -1)
        return None

    # ══════════════════════════════════════════════════════════════
    # OpenF1 API Methods — All use in-memory cache
    # ══════════════════════════════════════════════════════════════

    # ── Sessions ──────────────────────────────────────────────────

    def get_openf1_sessions(self, year=2024):
        """Get all sessions for a year from OpenF1."""
        # SQLite cache for longer-term storage
        cached = get_cached("openf1_sessions", {"year": year}, 1)
        if cached:
            return sorted(cached, key=lambda x: x.get("date_start", ""))

        url = f"{self.OPENF1_BASE}/sessions"
        data = self._fetch_json(url, params={"year": year})
        if not data or not isinstance(data, list):
            return []

        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for s in data:
            rows.append({
                "session_key": s.get("session_key"),
                "session_name": s.get("session_name", ""),
                "session_type": s.get("session_type", ""),
                "meeting_key": s.get("meeting_key"),
                "circuit_key": s.get("circuit_key"),
                "circuit_short_name": s.get("circuit_short_name", ""),
                "country_name": s.get("country_name", ""),
                "year": year,
                "date_start": s.get("date_start", ""),
                "date_end": s.get("date_end", ""),
                "fetched_at": now,
            })

        if rows:
            clear_table("openf1_sessions", {"year": year})
            upsert("openf1_sessions", rows)

        return sorted(rows, key=lambda x: x.get("date_start", ""))

    # ── Drivers (per session) ─────────────────────────────────────

    def get_openf1_drivers(self, session_key):
        """Get driver metadata for a specific session. Memory-cached 5 min."""
        cache_key = f"drivers_{session_key}"
        mem = self._mem_get(cache_key, self.MEM_TTL_DRIVERS)
        if mem is not None:
            return mem

        # SQLite fallback
        cached = get_cached("openf1_drivers", {"session_key": session_key}, 1)
        if cached:
            self._mem_set(cache_key, cached)
            return cached

        url = f"{self.OPENF1_BASE}/drivers"
        data = self._fetch_json(url, params={"session_key": session_key})
        if not data or not isinstance(data, list):
            return []

        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for d in data:
            rows.append({
                "session_key": session_key,
                "driver_number": d.get("driver_number"),
                "full_name": d.get("full_name", ""),
                "name_acronym": d.get("name_acronym", ""),
                "team_name": d.get("team_name", ""),
                "team_colour": d.get("team_colour", ""),
                "headshot_url": d.get("headshot_url", ""),
                "country_code": d.get("country_code", ""),
                "fetched_at": now,
            })

        if rows:
            clear_table("openf1_drivers", {"session_key": session_key})
            upsert("openf1_drivers", rows)

        self._mem_set(cache_key, rows)
        return rows
    def is_session_live(self, session_key):
        """Determine if a session is currently active/live. Historic sessions are cached long-term."""
        # Query local DB first for cached session info
        cached = get_cached("openf1_sessions", {"session_key": session_key}, 8760)

        session_info = None
        if cached:
            session_info = cached[0]
        else:
            # If session is not cached, we fallback to querying the API
            url = f"{self.OPENF1_BASE}/sessions"
            data = self._fetch_json(url, params={"session_key": session_key})
            if data and isinstance(data, list):
                session_info = data[0]

        if not session_info:
            return False

        date_end_str = session_info.get("date_end")
        if not date_end_str:
            return True

        try:
            clean_str = date_end_str.split("+")[0].split("Z")[0]
            date_end = datetime.fromisoformat(clean_str).replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return (now - date_end) < timedelta(hours=2)
        except Exception:
            return True

    def get_positions(self, session_key):
        """Get the latest position for each driver. Memory-cached 15s (live) / 12h (historic)."""
        cache_key = f"positions_{session_key}"
        ttl = self.MEM_TTL_POSITIONS if self.is_session_live(session_key) else 43200
        mem = self._mem_get(cache_key, ttl)
        if mem is not None:
            return mem

        url = f"{self.OPENF1_BASE}/position"
        data = self._fetch_json(url, params={"session_key": session_key})
        if not data or not isinstance(data, list):
            return []

        # Keep latest position per driver
        latest = {}
        for item in data:
            dn = item.get("driver_number")
            if dn is not None:
                latest[dn] = item

        result = sorted(latest.values(), key=lambda x: x.get("position", 99))
        self._mem_set(cache_key, result)
        return result

    # ── Car Locations (latest per driver) ─────────────────────────
    def get_car_locations(self, session_key):
        """Get the latest car position for each driver. Memory-cached 10s (live) / 12h (historic)."""
        cache_key = f"locations_{session_key}"
        ttl = self.MEM_TTL_LOCATIONS if self.is_session_live(session_key) else 43200
        mem = self._mem_get(cache_key, ttl)
        if mem is not None:
            return mem

        # Fetch locations with date filter for recent data
        # For historical sessions, the API may return empty — that's fine
        recent_cutoff = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        url = f"{self.OPENF1_BASE}/location"
        data = self._fetch_json(url, params={
            "session_key": session_key,
            "date>": recent_cutoff,
        })

        if not data or not isinstance(data, list):
            # Cache empty result briefly to avoid hammering the API on error
            self._mem_set(cache_key, [])
            return []

        # Group by driver, keep latest entry per driver
        latest = {}
        for loc in data:
            dn = loc.get("driver_number")
            if dn is not None:
                latest[dn] = loc

        result = []
        for dn, loc in latest.items():
            result.append({
                "driver_number": dn,
                "x": loc.get("x", 0),
                "y": loc.get("y", 0),
                "z": loc.get("z", 0),
                "date": loc.get("date", ""),
            })

        self._mem_set(cache_key, result)
        return result

    # ── Track Outline ─────────────────────────────────────────────

    CACHE_TRACK_OUTLINE = 24  # Hours — circuits don't change

    def get_track_outline(self, session_key, driver_number=None):
        """
        Get track outline from one driver's single-lap location data.
        Filters pit lane traversals and subsamples for performance.
        Cached 24h in SQLite + in-memory.
        """
        # In-memory cache (very long TTL)
        cache_key = f"track_{session_key}"
        mem = self._mem_get(cache_key, 3600)  # 1 hour in-memory
        if mem is not None:
            return mem

        # SQLite cache
        cached = get_cached("track_outlines", {"session_key": session_key}, self.CACHE_TRACK_OUTLINE)
        if cached:
            result = [{"x": p["x"], "y": p["y"]} for p in cached]
            self._mem_set(cache_key, result)
            return result

        # Pick a driver if not specified
        if driver_number is None:
            drivers = self.get_openf1_drivers(session_key)
            if drivers:
                driver_number = drivers[0]["driver_number"]
            else:
                return []

        # Step 1: Find a clean (non-pit) lap for timestamp filtering
        laps_data = self.get_openf1_laps(session_key)
        driver_laps = [
            l for l in laps_data
            if l.get("driver_number") == driver_number
            and not l.get("is_pit_out_lap")
            and l.get("lap_duration") is not None
            and l.get("date_start")
        ]

        target_lap = None
        if driver_laps:
            mid_idx = len(driver_laps) // 2
            target_lap = driver_laps[mid_idx]

        # Step 2: Fetch location data, filtered to one lap if possible
        params = {
            "session_key": session_key,
            "driver_number": driver_number,
        }

        if target_lap and target_lap.get("date_start"):
            lap_start = target_lap["date_start"]
            lap_duration = target_lap.get("lap_duration", 120)
            try:
                start_dt = datetime.fromisoformat(lap_start.replace("Z", "+00:00"))
                end_dt = start_dt + timedelta(seconds=lap_duration + 5)
                params["date>"] = lap_start
                params["date<"] = end_dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
            except (ValueError, TypeError):
                pass

        data = self._fetch_json(f"{self.OPENF1_BASE}/location", params=params)
        if not data or not isinstance(data, list):
            return []

        # Step 3: Extract x,y points
        raw_points = [{"x": p.get("x", 0), "y": p.get("y", 0)} for p in data]
        if not raw_points:
            return []

        # Step 4: Remove pit lane traversals (large jumps)
        if len(raw_points) > 2:
            distances = []
            for i in range(1, min(50, len(raw_points))):
                dx = raw_points[i]["x"] - raw_points[i-1]["x"]
                dy = raw_points[i]["y"] - raw_points[i-1]["y"]
                distances.append(math.sqrt(dx*dx + dy*dy))

            if distances:
                median_dist = sorted(distances)[len(distances)//2]
                threshold = median_dist * 8

                filtered = [raw_points[0]]
                for i in range(1, len(raw_points)):
                    dx = raw_points[i]["x"] - raw_points[i-1]["x"]
                    dy = raw_points[i]["y"] - raw_points[i-1]["y"]
                    dist = math.sqrt(dx*dx + dy*dy)
                    if dist < threshold:
                        filtered.append(raw_points[i])
                raw_points = filtered

        # Step 5: Subsample to ~400 points max
        if len(raw_points) > 400:
            step = len(raw_points) / 400
            raw_points = [raw_points[int(i * step)] for i in range(400)]

        # Step 6: Cache in SQLite
        now = datetime.now(timezone.utc).isoformat()
        cache_rows = [{"session_key": session_key, "x": p["x"], "y": p["y"], "fetched_at": now} for p in raw_points]
        if cache_rows:
            clear_table("track_outlines", {"session_key": session_key})
            upsert("track_outlines", cache_rows)

        self._mem_set(cache_key, raw_points)
        return raw_points

    # ── Laps with Sector Times ────────────────────────────────────
    def get_openf1_laps(self, session_key):
        """Get all lap data with sector times. Memory-cached 15s (live) / 12h (historic)."""
        cache_key = f"of1_laps_{session_key}"
        is_live = self.is_session_live(session_key)
        mem_ttl = self.MEM_TTL_LAPS if is_live else 43200
        db_ttl = 0.003 if is_live else 8760.0

        mem = self._mem_get(cache_key, mem_ttl)
        if mem is not None:
            return mem

        # SQLite cache
        cached = get_cached("openf1_laps", {"session_key": session_key}, db_ttl)
        if cached:
            self._mem_set(cache_key, cached)
            return cached

        url = f"{self.OPENF1_BASE}/laps"
        data = self._fetch_json(url, params={"session_key": session_key})
        if not data or not isinstance(data, list):
            return []

        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for lap in data:
            rows.append({
                "session_key": session_key,
                "driver_number": lap.get("driver_number"),
                "lap_number": lap.get("lap_number"),
                "duration_sector_1": lap.get("duration_sector_1"),
                "duration_sector_2": lap.get("duration_sector_2"),
                "duration_sector_3": lap.get("duration_sector_3"),
                "lap_duration": lap.get("lap_duration"),
                "is_pit_out_lap": 1 if lap.get("is_pit_out_lap") else 0,
                "st_speed": lap.get("st_speed"),
                "date_start": lap.get("date_start", ""),
                "fetched_at": now,
            })

        if rows:
            clear_table("openf1_laps", {"session_key": session_key})
            upsert("openf1_laps", rows)

        self._mem_set(cache_key, rows)
        return rows

    # ── Intervals ─────────────────────────────────────────────────
    def get_intervals(self, session_key):
        """Get gap to leader + gap ahead per driver. Memory-cached 15s (live) / 12h (historic)."""
        cache_key = f"intervals_{session_key}"
        ttl = self.MEM_TTL_INTERVALS if self.is_session_live(session_key) else 43200
        mem = self._mem_get(cache_key, ttl)
        if mem is not None:
            return mem

        url = f"{self.OPENF1_BASE}/intervals"
        data = self._fetch_json(url, params={"session_key": session_key})
        if not data or not isinstance(data, list):
            return []

        latest = {}
        for item in data:
            dn = item.get("driver_number")
            if dn is not None:
                latest[dn] = item

        result = list(latest.values())
        self._mem_set(cache_key, result)
        return result

    # ── Stints ────────────────────────────────────────────────────
    def get_stints(self, session_key):
        """Get stint / tyre strategy data. Memory-cached 30s (live) / 12h (historic)."""
        cache_key = f"stints_{session_key}"
        is_live = self.is_session_live(session_key)
        mem_ttl = self.MEM_TTL_STINTS if is_live else 43200
        db_ttl = 0.008 if is_live else 8760.0

        mem = self._mem_get(cache_key, mem_ttl)
        if mem is not None:
            return mem

        # SQLite cache
        cached = get_cached("stints", {"session_key": session_key}, db_ttl)
        if cached:
            self._mem_set(cache_key, cached)
            return cached

        url = f"{self.OPENF1_BASE}/stints"
        data = self._fetch_json(url, params={"session_key": session_key})
        if not data or not isinstance(data, list):
            return []

        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for s in data:
            rows.append({
                "session_key": session_key,
                "driver_number": s.get("driver_number"),
                "stint_number": s.get("stint_number"),
                "compound": s.get("compound", ""),
                "tyre_age_at_start": s.get("tyre_age_at_start", 0),
                "lap_start": s.get("lap_start"),
                "lap_end": s.get("lap_end"),
                "fetched_at": now,
            })

        if rows:
            clear_table("stints", {"session_key": session_key})
            upsert("stints", rows)

        self._mem_set(cache_key, rows)
        return rows

    # ── Weather ───────────────────────────────────────────────────
    def get_weather(self, session_key):
        """Get the latest weather data. Memory-cached 60s (live) / 12h (historic)."""
        cache_key = f"weather_{session_key}"
        ttl = self.MEM_TTL_WEATHER if self.is_session_live(session_key) else 43200
        mem = self._mem_get(cache_key, ttl)
        if mem is not None:
            return mem

        url = f"{self.OPENF1_BASE}/weather"
        data = self._fetch_json(url, params={"session_key": session_key})
        if not data or not isinstance(data, list):
            return {}

        result = data[-1] if data else {}
        self._mem_set(cache_key, result)
        return result

    # ── Race Control ──────────────────────────────────────────────

    def get_race_control(self, session_key):
        """Get race control messages. Memory-cached 20s (live) / 12h (historic)."""
        cache_key = f"rc_{session_key}"
        ttl = self.MEM_TTL_RACE_CONTROL if self.is_session_live(session_key) else 43200
        mem = self._mem_get(cache_key, ttl)
        if mem is not None:
            return mem

        url = f"{self.OPENF1_BASE}/race_control"
        data = self._fetch_json(url, params={"session_key": session_key})
        if not data or not isinstance(data, list):
            return []

        self._mem_set(cache_key, data)
        return data
    # ── Car Telemetry ─────────────────────────────────────────────

    def get_car_telemetry(self, session_key, driver_number):
        """Get latest telemetry data for a specific driver. Memory-cached 5s."""
        cache_key = f"telemetry_{session_key}_{driver_number}"
        mem = self._mem_get(cache_key, self.MEM_TTL_TELEMETRY)
        if mem is not None:
            return mem

        url = f"{self.OPENF1_BASE}/car_data"
        data = self._fetch_json(url, params={
            "session_key": session_key,
            "driver_number": driver_number,
        })
        if not data or not isinstance(data, list):
            return {}

        result = data[-1] if data else {}
        self._mem_set(cache_key, result)
        return result

    def get_lap_telemetry(self, session_key, driver_number, start_time_iso, duration_seconds):
        """Get high-frequency telemetry samples for a specific lap duration."""
        if not start_time_iso:
            return []

        try:
            start_dt = datetime.fromisoformat(start_time_iso.replace("Z", "+00:00"))
            end_dt = start_dt + timedelta(seconds=float(duration_seconds or 100.0))
            
            start_str = start_dt.isoformat()
            end_str = end_dt.isoformat()
        except Exception as e:
            print(f"[Telemetry Date Parse Error] {e} for {start_time_iso}")
            return []

        cache_key = f"lap_telemetry_{session_key}_{driver_number}_{start_time_iso}"
        mem = self._mem_get(cache_key, 3600)  # static lap telemetry, cache 1 hour
        if mem is not None:
            return mem

        url = f"{self.OPENF1_BASE}/car_data"
        params = {
            "session_key": session_key,
            "driver_number": driver_number,
            "date>=": start_str,
            "date<=": end_str,
        }
        data = self._fetch_json(url, params=params)
        if not data or not isinstance(data, list):
            return []

        # Downsample to 150 points for browser rendering performance
        if len(data) > 150:
            step = len(data) / 150
            data = [data[int(i * step)] for i in range(150)]

        self._mem_set(cache_key, data)
        return data

    # ══════════════════════════════════════════════════════════════
    # Formula 1 Live Timing Methods
    #
    # The official static Live Timing feeds provide the current keyframe while
    # a session is in progress and the final keyframe after it ends. They do
    # not require an OpenF1 account or token.
    # ══════════════════════════════════════════════════════════════

    @staticmethod
    def _as_int(value, default=None):
        """Convert a feed value to an integer without leaking parse errors."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _duration_seconds(value):
        """Convert F1 timing strings such as '1:32.123' to seconds."""
        if value in (None, ""):
            return None
        try:
            parts = str(value).split(":")
            seconds = float(parts[-1])
            for index, part in enumerate(reversed(parts[:-1]), start=1):
                seconds += int(part) * (60 ** index)
            return seconds
        except (TypeError, ValueError):
            return None

    def get_live_sessions(self, year=2024):
        """Get Formula 1 sessions for a season from the official timing feed."""
        cache_key = f"f1_live_sessions_{year}"
        cached = self._mem_get(cache_key, 300)
        if cached is not None:
            return cached

        data = self._fetch_f1_live_json(f"{self.F1_LIVE_BASE}/{year}/Index.json")
        if not isinstance(data, dict):
            return []

        sessions = []
        for meeting in data.get("Meetings", []):
            country = meeting.get("Country", {})
            circuit = meeting.get("Circuit", {})
            for session in meeting.get("Sessions", []):
                session_key = self._as_int(session.get("Key"))
                path = session.get("Path")
                if session_key is None or not path:
                    continue

                row = {
                    "session_key": session_key,
                    "session_name": session.get("Name", ""),
                    "session_type": session.get("Type", ""),
                    "meeting_key": self._as_int(meeting.get("Key")),
                    "circuit_key": self._as_int(circuit.get("Key")),
                    "circuit_short_name": circuit.get("ShortName", ""),
                    "country_name": country.get("Name", ""),
                    "year": year,
                    "date_start": session.get("StartDate", ""),
                    "date_end": session.get("EndDate", ""),
                    "path": path,
                }
                sessions.append(row)
                self._live_session_paths[session_key] = row

        result = sorted(sessions, key=lambda item: item["date_start"])
        self._mem_set(cache_key, result)
        return result

    def _get_live_session(self, session_key):
        """Return a session's feed metadata, fetching the current season if needed."""
        session = self._live_session_paths.get(session_key)
        if session:
            return session

        current_year = datetime.now(timezone.utc).year
        for year in range(current_year, max(current_year - 4, 2018), -1):
            for candidate in self.get_live_sessions(year):
                if candidate["session_key"] == session_key:
                    return candidate
        return None

    def _get_live_feed(self, session_key, topic, max_age_seconds):
        """Get a session feed with a topic-specific in-memory cache."""
        cache_key = f"f1_live_{session_key}_{topic}"
        cached = self._mem_get(cache_key, max_age_seconds)
        if cached is not None:
            return cached

        session = self._get_live_session(session_key)
        if not session:
            self._live_timing_last_error = "The selected Formula 1 session could not be found."
            return None

        data = self._fetch_f1_live_json(
            f"{self.F1_LIVE_BASE}/{session['path']}{topic}.json"
        )
        if data is not None:
            self._mem_set(cache_key, data)
        return data

    def _get_live_timing_data(self, session_key):
        """Get the current timing keyframe for a session."""
        return self._get_live_feed(session_key, "TimingData", self.MEM_TTL_POSITIONS)

    def _get_live_position_data(self, session_key):
        """Decode the compressed Formula 1 car-position keyframe."""
        cache_key = f"f1_live_positions_raw_{session_key}"
        cached = self._mem_get(cache_key, self.MEM_TTL_LOCATIONS)
        if cached is not None:
            return cached

        encoded = self._get_live_feed(session_key, "Position.z", self.MEM_TTL_LOCATIONS)
        if not isinstance(encoded, str):
            return None

        try:
            compressed = base64.b64decode(encoded)
            raw = zlib.decompress(compressed, -zlib.MAX_WBITS)
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, zlib.error, UnicodeDecodeError, json.JSONDecodeError) as error:
            self._live_timing_last_error = "Formula 1 Live Timing position data could not be decoded."
            print(f"[F1 Live Timing Error] Position.z: {error}")
            return None

        self._mem_set(cache_key, data)
        return data

    def get_live_drivers(self, session_key):
        """Get driver metadata for a session from DriverList."""
        data = self._get_live_feed(session_key, "DriverList", self.MEM_TTL_DRIVERS)
        if not isinstance(data, dict):
            return []

        drivers = []
        for driver in data.values():
            driver_number = self._as_int(driver.get("RacingNumber"))
            if driver_number is None:
                continue
            drivers.append({
                "session_key": session_key,
                "driver_number": driver_number,
                "full_name": driver.get("FullName", ""),
                "name_acronym": driver.get("Tla", ""),
                "team_name": driver.get("TeamName", ""),
                "team_colour": driver.get("TeamColour", ""),
                "headshot_url": driver.get("HeadshotUrl", ""),
                "country_code": driver.get("CountryCode", ""),
            })
        return sorted(drivers, key=lambda item: item["driver_number"])

    def get_live_positions(self, session_key):
        """Get the latest timing order for every driver."""
        data = self._get_live_timing_data(session_key)
        if not isinstance(data, dict):
            return []

        positions = []
        for line in data.get("Lines", {}).values():
            driver_number = self._as_int(line.get("RacingNumber"))
            position = self._as_int(line.get("Position"))
            if driver_number is None or position is None:
                continue
            positions.append({
                "driver_number": driver_number,
                "position": position,
                "gap_to_leader": line.get("GapToLeader", ""),
                "in_pit": bool(line.get("InPit")),
                "retired": bool(line.get("Retired")),
            })
        return sorted(positions, key=lambda item: item["position"])

    def get_live_car_locations(self, session_key):
        """Get the latest track coordinates for each driver."""
        data = self._get_live_position_data(session_key)
        if not isinstance(data, dict):
            return []

        snapshots = data.get("Position", [])
        if not snapshots:
            return []
        latest = snapshots[-1]
        locations = []
        for driver_number, position in latest.get("Entries", {}).items():
            number = self._as_int(driver_number)
            if number is None:
                continue
            locations.append({
                "driver_number": number,
                "x": position.get("X", 0),
                "y": position.get("Y", 0),
                "z": position.get("Z", 0),
                "date": latest.get("Timestamp", ""),
            })
        return locations

    def get_live_laps(self, session_key):
        """Get each driver's latest lap and sector times from TimingData."""
        data = self._get_live_timing_data(session_key)
        if not isinstance(data, dict):
            return []

        laps = []
        for line in data.get("Lines", {}).values():
            driver_number = self._as_int(line.get("RacingNumber"))
            if driver_number is None:
                continue
            sectors = line.get("Sectors", [])
            values = []
            for sector in sectors[:3]:
                value = sector.get("Value") or sector.get("PreviousValue")
                values.append(self._duration_seconds(value))
            while len(values) < 3:
                values.append(None)

            last_lap = line.get("LastLapTime", {})
            laps.append({
                "session_key": session_key,
                "driver_number": driver_number,
                "lap_number": self._as_int(line.get("NumberOfLaps"), 0),
                "duration_sector_1": values[0],
                "duration_sector_2": values[1],
                "duration_sector_3": values[2],
                "lap_duration": self._duration_seconds(
                    last_lap.get("Value") or last_lap.get("PreviousValue")
                ),
                "is_pit_out_lap": 1 if line.get("PitOut") else 0,
                "st_speed": None,
                "date_start": "",
            })
        return laps

    def get_live_intervals(self, session_key):
        """Get current gaps to the leader and to the car ahead."""
        data = self._get_live_timing_data(session_key)
        if not isinstance(data, dict):
            return []

        intervals = []
        for line in data.get("Lines", {}).values():
            driver_number = self._as_int(line.get("RacingNumber"))
            if driver_number is None:
                continue
            ahead = line.get("IntervalToPositionAhead", {})
            intervals.append({
                "driver_number": driver_number,
                "gap_to_leader": line.get("GapToLeader", ""),
                "interval": ahead.get("Value", "") if isinstance(ahead, dict) else "",
            })
        return intervals

    def get_live_stints(self, session_key):
        """Get tyre stint data from TimingAppData."""
        data = self._get_live_feed(session_key, "TimingAppData", self.MEM_TTL_STINTS)
        if not isinstance(data, dict):
            return []

        stints = []
        for line in data.get("Lines", {}).values():
            driver_number = self._as_int(line.get("RacingNumber"))
            if driver_number is None:
                continue
            for stint_number, stint in enumerate(line.get("Stints", []), start=1):
                lap_end = self._as_int(stint.get("LapNumber"))
                total_laps = self._as_int(stint.get("TotalLaps"), 0)
                stints.append({
                    "session_key": session_key,
                    "driver_number": driver_number,
                    "stint_number": stint_number,
                    "compound": stint.get("Compound", ""),
                    "tyre_age_at_start": self._as_int(stint.get("StartLaps"), 0),
                    "lap_start": max((lap_end or 0) - total_laps + 1, 1),
                    "lap_end": lap_end,
                })
        return stints

    def get_live_weather(self, session_key):
        """Get the current weather keyframe."""
        data = self._get_live_feed(session_key, "WeatherData", self.MEM_TTL_WEATHER)
        if not isinstance(data, dict):
            return {}

        rainfall = data.get("Rainfall", "0")
        return {
            "air_temperature": data.get("AirTemp"),
            "track_temperature": data.get("TrackTemp"),
            "humidity": data.get("Humidity"),
            "pressure": data.get("Pressure"),
            "rainfall": str(rainfall).lower() in {"1", "true"},
            "wind_direction": data.get("WindDirection"),
            "wind_speed": data.get("WindSpeed"),
        }

    def get_live_race_control(self, session_key):
        """Get race-control messages from the official session feed."""
        data = self._get_live_feed(session_key, "RaceControlMessages", self.MEM_TTL_RACE_CONTROL)
        if not isinstance(data, dict):
            return []
        return [
            {
                "date": message.get("Utc", ""),
                "category": message.get("Category", ""),
                "flag": message.get("Flag", ""),
                "scope": message.get("Scope", ""),
                "lap_number": self._as_int(message.get("Lap")),
                "message": message.get("Message", ""),
            }
            for message in data.get("Messages", [])
        ]

    def get_live_track_outline(self, session_key, driver_number=None):
        """Build a circuit outline from a driver's official position stream."""
        cache_key = f"f1_live_track_{session_key}"
        cached = self._mem_get(cache_key, 3600)
        if cached is not None:
            return cached

        data = self._get_live_position_data(session_key)
        if not isinstance(data, dict):
            return []

        snapshots = data.get("Position", [])
        if driver_number is None:
            for snapshot in snapshots:
                if snapshot.get("Entries"):
                    driver_number = next(iter(snapshot["Entries"]))
                    break
        if driver_number is None:
            return []

        driver_key = str(driver_number)
        points = []
        for snapshot in snapshots:
            position = snapshot.get("Entries", {}).get(driver_key)
            if position and position.get("Status") == "OnTrack":
                points.append({"x": position.get("X", 0), "y": position.get("Y", 0)})

        if len(points) > 400:
            step = len(points) / 400
            points = [points[int(index * step)] for index in range(400)]

        self._mem_set(cache_key, points)
        return points

    # ══════════════════════════════════════════════════════════════
    # Unified Methods — OpenF1 → F1 Live Timing fallback
    #
    # These combine both data sources so the dashboard works without
    # any API credentials.  OpenF1's free tier covers historical and
    # recently-completed sessions; the official F1 Live Timing static
    # feeds cover in-progress and recent sessions with zero auth.
    # ══════════════════════════════════════════════════════════════

    def _init_data_source(self):
        """Lazily initialised per-request data source tracker."""
        if not hasattr(self, "_last_data_source"):
            self._last_data_source = "unknown"

    @property
    def last_data_source(self):
        """Which backend provided the most recent live-page data."""
        return getattr(self, "_last_data_source", "unknown")

    def _unified(self, primary_fn, fallback_fn, *args, empty_ok=False):
        """Shared OpenF1-first / F1-Live-Timing-fallback pattern.

        `empty_ok` should be True for endpoints (like weather) where an
        empty dict/list is still a meaningful "no data yet" result from
        the primary source and shouldn't trigger a fallback attempt —
        currently unused since both APIs legitimately return empty
        collections when there's nothing to report, but kept as a hook
        for future sources that distinguish "empty" from "error".
        """
        try:
            primary = primary_fn(*args)
        except Exception as e:
            print(f"[Unified] Primary source error: {e}")
            primary = None

        # Use truthiness: an empty list/dict from OpenF1 (e.g. 404 → no data)
        # correctly falls through to the F1 Live Timing backup.
        if primary:
            self._last_data_source = "openf1"
            return primary

        try:
            fallback = fallback_fn(*args)
        except Exception as e:
            print(f"[Unified] Fallback source error: {e}")
            fallback = None

        if fallback:
            self._last_data_source = "f1_live"
            return fallback

        # Neither source had data — return a safe empty collection.
        return []

    def get_unified_sessions(self, year):
        """Get sessions for a year — OpenF1 first, then F1 Live Timing."""
        return self._unified(self.get_openf1_sessions, self.get_live_sessions, year)

    def get_unified_drivers(self, session_key):
        """Get driver metadata — OpenF1 first, then F1 Live Timing."""
        return self._unified(self.get_openf1_drivers, self.get_live_drivers, session_key)

    def get_unified_positions(self, session_key):
        """Get position data — OpenF1 first, then F1 Live Timing."""
        return self._unified(self.get_positions, self.get_live_positions, session_key)

    def get_unified_locations(self, session_key):
        """Get car locations — OpenF1 first, then F1 Live Timing."""
        return self._unified(self.get_car_locations, self.get_live_car_locations, session_key)

    def get_unified_laps(self, session_key):
        """Get lap/sector data — OpenF1 first, then F1 Live Timing."""
        return self._unified(self.get_openf1_laps, self.get_live_laps, session_key)

    def get_unified_intervals(self, session_key):
        """Get gap/interval data — OpenF1 first, then F1 Live Timing."""
        return self._unified(self.get_intervals, self.get_live_intervals, session_key)

    def get_unified_stints(self, session_key):
        """Get tyre stint data — OpenF1 first, then F1 Live Timing."""
        return self._unified(self.get_stints, self.get_live_stints, session_key)

    def get_unified_weather(self, session_key):
        """Get weather data — OpenF1 first, then F1 Live Timing."""
        return self._unified(self.get_weather, self.get_live_weather, session_key)

    def get_unified_race_control(self, session_key):
        """Get race control messages — OpenF1 first, then F1 Live Timing."""
        return self._unified(self.get_race_control, self.get_live_race_control, session_key)

    def get_unified_track_outline(self, session_key):
        """Get track outline — OpenF1 first, then F1 Live Timing."""
        return self._unified(self.get_track_outline, self.get_live_track_outline, session_key)