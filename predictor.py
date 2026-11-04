"""
Race Winner Predictor — v5 (Stacking Ensemble: XGBoost + LightGBM + CatBoost)
==============================================================================
Architecture:
  • Base models: XGBoost, LightGBM, CatBoost (each tuned with 50 Optuna trials)
  • Stacking meta-learner: Ridge Regression on OOF predictions
  • Calibration: per-race softmax with a likelihood-fitted temperature
  • Explainability: SHAP values per prediction
  • Training data: 2018–current (8 seasons)
  • Validation: GroupKFold (grouped by season, no data leakage)
"""

import os
import re
import threading
import warnings
from datetime import datetime, timezone
from collections import defaultdict

import numpy as np
import pandas as pd
from joblib import dump, load
import optuna
from optuna.samplers import TPESampler
from optuna.pruners import MedianPruner

# Suppress verbose warnings during HPO
optuna.logging.set_verbosity(optuna.logging.WARNING)
warnings.filterwarnings("ignore", category=UserWarning)

# ─── Lazy imports of heavy libraries (so server starts fast) ───────────────────
def _import_xgb():
    import xgboost as xgb
    return xgb

def _import_lgb():
    import lightgbm as lgb
    return lgb

def _import_cb():
    from catboost import CatBoostRegressor
    return CatBoostRegressor

def _import_shap():
    import shap
    return shap

# ─── Paths & constants ─────────────────────────────────────────────────────────
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "win_predictor.joblib")
MODEL_MAX_AGE_HOURS = 12
DEFAULT_TRAINING_SEASONS = 8   # How many past seasons to use
# Bump when saved models were trained on data/logic now known to be wrong.
# v2: full paginated seasons + per-race softmax calibration.
MODEL_DATA_VERSION = 2
# Optuna trials per base model. 20 keeps a cold-start train to a few minutes;
# raise it with PREDICTOR_OPTUNA_TRIALS for a longer, more thorough search.
DEFAULT_OPTUNA_TRIALS = int(os.environ.get("PREDICTOR_OPTUNA_TRIALS", 20))

# ─── Feature columns ──────────────────────────────────────────────────────────
FEATURE_COLUMNS = [
    # ── Qualifying & grid ──────────────────────────────────────────────────────
    "grid",
    "grid_squared",               # Non-linear pole effect
    "quali_position",
    "quali_gap_to_pole",
    "quali_gap_normalized",       # Gap / session median pace
    "quali_vs_teammate",

    # ── Driver form & performance ──────────────────────────────────────────────
    "driver_form",
    "driver_form_variance",       # Consistency: std-dev of last 5 finishes
    "driver_momentum",
    "driver_dnf_rate",
    "driver_incident_rate",
    "driver_experience",
    "driver_elo",                 # Elo rating (all-time race outcomes)

    # ── Constructor / car ──────────────────────────────────────────────────────
    "constructor_form",
    "constructor_dnf_rate",
    "constructor_win_rate",
    "constructor_elo",            # Team Elo rating

    # ── Circuit-specific history ───────────────────────────────────────────────
    "circuit_driver_avg_finish",
    "circuit_driver_win_rate",
    "circuit_driver_podium_rate", # Podiums at this circuit
    "circuit_constructor_win_rate",

    # ── Championship context ───────────────────────────────────────────────────
    "championship_position",
    "championship_gap",
    "season_progress",
    "home_race_bonus",            # 1 if driver's nationality matches circuit country

    # ── Pit strategy ──────────────────────────────────────────────────────────
    "cumulative_points",
    "avg_pit_stops",
    "avg_pit_duration",

    # ── Live data overrides ────────────────────────────────────────────────────
    "live_current_position",
    "live_laps_completed",
]

# ─── Neutral / prior values ───────────────────────────────────────────────────
NEUTRAL_FORM                  = 10.5
NEUTRAL_MOMENTUM              = 0.0
NEUTRAL_DNF_RATE              = 0.12
NEUTRAL_INCIDENT_RATE         = 0.04
NEUTRAL_WIN_RATE              = 0.05
NEUTRAL_PODIUM_RATE           = 0.15
NEUTRAL_PIT_STOPS             = 2.0
NEUTRAL_PIT_DURATION          = 23.0
NEUTRAL_QUALI_GAP             = 1.5
NEUTRAL_QUALI_VS_TEAMMATE     = 0.0
NEUTRAL_CIRCUIT_AVG_FINISH    = 10.0
NEUTRAL_CIRCUIT_WIN_RATE      = 0.05
NEUTRAL_CHAMPIONSHIP_POS      = 10.0
NEUTRAL_CHAMPIONSHIP_GAP      = 100.0
NEUTRAL_DRIVER_EXPERIENCE     = 0.0
NEUTRAL_ELO                   = 1500.0
ELO_K                         = 32.0   # Elo K-factor

# ─── DNF / DSQ / DNS status maps ──────────────────────────────────────────────
_DNF_STATUSES = frozenset({
    "Accident", "Collision", "Mechanical", "Engine", "Retired",
    "Gearbox", "Hydraulics", "Electrical", "Spun off",
    "Suspension", "Brakes", "Overheating", "Wheel", "Transmission",
    "Power Unit", "Vibrations", "Front Wing", "Rear Wing", "Fire",
    "Tyre", "Debris", "Damage", "Physical", "Technical",
    "Oil Leak", "Water Leak", "Driveshaft", "Clutch", "Turbo",
    "ERS", "Battery",
})
_INCIDENT_STATUSES = frozenset({
    "Accident", "Collision", "Spun off", "Damage", "Debris", "Fire",
})
_DSQ_STATUSES = frozenset({"Disqualified", "Excluded"})
_DNS_STATUSES = frozenset({"Did not start", "Did not qualify", "Withdrew", "107% Rule"})
_LAPS_RE = re.compile(r"^\+\d+\s+Laps?$", re.IGNORECASE)


def _is_dnf(status: str) -> bool:
    s = str(status).strip()
    if not s or s.lower() == "finished":
        return False
    if _LAPS_RE.match(s):
        return False
    return s in _DNF_STATUSES or s in _DSQ_STATUSES or s in _DNS_STATUSES


def _is_incident(status: str) -> bool:
    return str(status).strip() in _INCIDENT_STATUSES


# ──────────────────────────────────────────────────────────────────────────────

class EloRatingSystem:
    """
    Computes Elo ratings for drivers and constructors from race results.
    Higher Elo = stronger performer. Initial rating = 1500.
    """

    def __init__(self, k: float = ELO_K):
        self.k = k
        self._driver_ratings: dict[str, float] = {}
        self._constructor_ratings: dict[str, float] = {}

    def _expected(self, ra: float, rb: float) -> float:
        return 1.0 / (1.0 + 10 ** ((rb - ra) / 400))

    def _outcome(self, pos_a: int, pos_b: int) -> float:
        """1.0 if A beats B, 0.5 for tie, 0.0 if A loses."""
        if pos_a < pos_b:
            return 1.0
        if pos_a == pos_b:
            return 0.5
        return 0.0

    def process_race(self, results: list[dict]) -> None:
        """
        Update Elo ratings based on head-to-head outcomes in a race.
        results: list of {driver_code, constructor, position}
        """
        valid = [r for r in results if r.get("driver_code") and r.get("position")]
        valid.sort(key=lambda x: x["position"])

        # All vs all pairwise Elo update (accumulated, applied atomically)
        deltas_d: dict[str, float] = defaultdict(float)
        deltas_c: dict[str, float] = defaultdict(float)

        for i, a in enumerate(valid):
            da = a["driver_code"]
            ca = str(a.get("constructor", ""))
            ra_d = self._driver_ratings.get(da, NEUTRAL_ELO)
            ra_c = self._constructor_ratings.get(ca, NEUTRAL_ELO)

            for b in valid[i + 1:]:
                db = b["driver_code"]
                cb = str(b.get("constructor", ""))
                rb_d = self._driver_ratings.get(db, NEUTRAL_ELO)
                rb_c = self._constructor_ratings.get(cb, NEUTRAL_ELO)

                outcome = self._outcome(a["position"], b["position"])

                # Driver Elo
                ea = self._expected(ra_d, rb_d)
                deltas_d[da] += self.k * (outcome - ea)
                deltas_d[db] += self.k * ((1 - outcome) - (1 - ea))

                # Constructor Elo (averaged across both entries)
                ec = self._expected(ra_c, rb_c)
                deltas_c[ca] += self.k * (outcome - ec) * 0.5
                deltas_c[cb] += self.k * ((1 - outcome) - (1 - ec)) * 0.5

        for code, delta in deltas_d.items():
            self._driver_ratings[code] = self._driver_ratings.get(code, NEUTRAL_ELO) + delta
        for code, delta in deltas_c.items():
            self._constructor_ratings[code] = self._constructor_ratings.get(code, NEUTRAL_ELO) + delta

    def get_driver_elo(self, driver_code: str) -> float:
        return self._driver_ratings.get(driver_code, NEUTRAL_ELO)

    def get_constructor_elo(self, constructor: str) -> float:
        return self._constructor_ratings.get(constructor, NEUTRAL_ELO)

    def snapshot(self) -> tuple[dict, dict]:
        """Return deep copies of current ratings (for time-travel safe features)."""
        return dict(self._driver_ratings), dict(self._constructor_ratings)

    def restore(self, d_snap: dict, c_snap: dict) -> None:
        self._driver_ratings = dict(d_snap)
        self._constructor_ratings = dict(c_snap)


# ──────────────────────────────────────────────────────────────────────────────

class RacePredictor:
    """
    World-class F1 race predictor using a stacked ensemble of
    XGBoost + LightGBM + CatBoost with OOF cross-validation,
    per-race softmax calibration, and SHAP explainability.
    """

    def __init__(self, client):
        self.client = client
        self._model_bundle = None  # dict with all model artifacts
        self._trained_at = None
        self._lock = threading.Lock()
        self._train_lock = threading.Lock()

    # ══════════════════════════════════════════════════════════════════════════
    # Static Feature Helpers
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _parse_time(value) -> "float | None":
        if not value:
            return None
        try:
            parts = str(value).strip().split(":")
            secs = float(parts[-1])
            for i, p in enumerate(reversed(parts[:-1]), start=1):
                secs += int(p) * (60 ** i)
            return secs
        except (TypeError, ValueError):
            return None

    _parse_duration = _parse_time

    @staticmethod
    def _quali_gap_to_pole(quali_round_df, driver_code) -> float:
        if quali_round_df is None or quali_round_df.empty:
            return NEUTRAL_QUALI_GAP

        def _best(row) -> "float | None":
            for col in ("q3", "q2", "q1"):
                t = RacePredictor._parse_time(row.get(col))
                if t:
                    return t
            return None

        times = [_best(row) for _, row in quali_round_df.iterrows()]
        valid = [t for t in times if t is not None]
        if not valid:
            return NEUTRAL_QUALI_GAP
        pole_time = min(valid)
        drv = quali_round_df[quali_round_df["driver_code"] == driver_code]
        if drv.empty:
            return NEUTRAL_QUALI_GAP
        drv_time = _best(drv.iloc[0])
        return float(drv_time - pole_time) if drv_time else NEUTRAL_QUALI_GAP

    @staticmethod
    def _quali_gap_normalized(quali_round_df, driver_code) -> float:
        """Gap to pole normalized by the median Q1 time (removes circuit-length bias)."""
        if quali_round_df is None or quali_round_df.empty:
            return 0.0

        def _best(row) -> "float | None":
            for col in ("q3", "q2", "q1"):
                t = RacePredictor._parse_time(row.get(col))
                if t:
                    return t
            return None

        times = [_best(row) for _, row in quali_round_df.iterrows()]
        valid = sorted([t for t in times if t is not None])
        if not valid:
            return 0.0
        pole_time = valid[0]
        median_time = valid[len(valid) // 2]
        drv = quali_round_df[quali_round_df["driver_code"] == driver_code]
        if drv.empty:
            return 0.0
        drv_time = _best(drv.iloc[0])
        if not drv_time or median_time == 0:
            return 0.0
        return float((drv_time - pole_time) / median_time * 100)

    @staticmethod
    def _quali_vs_teammate(quali_round_df, driver_code, constructor) -> float:
        if quali_round_df is None or quali_round_df.empty:
            return NEUTRAL_QUALI_VS_TEAMMATE
        if "constructor" not in quali_round_df.columns:
            return NEUTRAL_QUALI_VS_TEAMMATE
        team = quali_round_df[quali_round_df["constructor"] == constructor]
        if len(team) < 2:
            return NEUTRAL_QUALI_VS_TEAMMATE
        drv_row = team[team["driver_code"] == driver_code]
        mate_row = team[team["driver_code"] != driver_code]
        if drv_row.empty or mate_row.empty:
            return NEUTRAL_QUALI_VS_TEAMMATE
        return float(drv_row.iloc[0]["position"]) - float(mate_row.iloc[0]["position"])

    @staticmethod
    def _driver_form(results_df, driver_code, before_round, window=5) -> float:
        if results_df.empty or "driver_code" not in results_df.columns:
            return NEUTRAL_FORM
        past = results_df[
            (results_df["driver_code"] == driver_code) & (results_df["round"] < before_round)
        ].sort_values("round").tail(window)
        return float(past["position"].mean()) if not past.empty else NEUTRAL_FORM

    @staticmethod
    def _driver_form_variance(results_df, driver_code, before_round, window=5) -> float:
        """Consistency metric: std dev of finishing positions (lower = more consistent)."""
        if results_df.empty or "driver_code" not in results_df.columns:
            return 5.0
        past = results_df[
            (results_df["driver_code"] == driver_code) & (results_df["round"] < before_round)
        ].sort_values("round").tail(window)
        if len(past) < 2:
            return 5.0
        return float(past["position"].std())

    @staticmethod
    def _driver_momentum(results_df, driver_code, before_round, recent=3, prior=3) -> float:
        if results_df.empty or "driver_code" not in results_df.columns:
            return NEUTRAL_MOMENTUM
        past = results_df[
            (results_df["driver_code"] == driver_code) & (results_df["round"] < before_round)
        ].sort_values("round").tail(recent + prior)
        if len(past) < 2:
            return NEUTRAL_MOMENTUM
        recent_rows = past.tail(recent)
        prior_rows = past.iloc[:max(0, len(past) - len(recent_rows))]
        if prior_rows.empty:
            return NEUTRAL_MOMENTUM
        return float(recent_rows["position"].mean()) - float(prior_rows["position"].mean())

    @staticmethod
    def _driver_dnf_rate(results_df, driver_code, before_round, window=10) -> float:
        if results_df.empty or "driver_code" not in results_df.columns:
            return NEUTRAL_DNF_RATE
        past = results_df[
            (results_df["driver_code"] == driver_code) & (results_df["round"] < before_round)
        ].sort_values("round").tail(window)
        if past.empty:
            return NEUTRAL_DNF_RATE
        return sum(_is_dnf(str(s)) for s in past["status"]) / len(past)

    @staticmethod
    def _driver_incident_rate(results_df, driver_code, before_round, window=10) -> float:
        if results_df.empty or "driver_code" not in results_df.columns:
            return NEUTRAL_INCIDENT_RATE
        past = results_df[
            (results_df["driver_code"] == driver_code) & (results_df["round"] < before_round)
        ].sort_values("round").tail(window)
        if past.empty:
            return NEUTRAL_INCIDENT_RATE
        return sum(_is_incident(str(s)) for s in past["status"]) / len(past)

    @staticmethod
    def _driver_experience(results_df, driver_code, before_round) -> float:
        if results_df.empty or "driver_code" not in results_df.columns:
            return NEUTRAL_DRIVER_EXPERIENCE
        past = results_df[
            (results_df["driver_code"] == driver_code) & (results_df["round"] < before_round)
        ]
        return float(len(past))

    @staticmethod
    def _constructor_form(results_df, constructor, before_round, window=10) -> float:
        if results_df.empty or "constructor" not in results_df.columns:
            return NEUTRAL_FORM
        past = results_df[
            (results_df["constructor"] == constructor) & (results_df["round"] < before_round)
        ].sort_values("round").tail(window)
        return float(past["position"].mean()) if not past.empty else NEUTRAL_FORM

    @staticmethod
    def _constructor_dnf_rate(results_df, constructor, before_round, window=20) -> float:
        if results_df.empty or "constructor" not in results_df.columns:
            return NEUTRAL_DNF_RATE
        past = results_df[
            (results_df["constructor"] == constructor) & (results_df["round"] < before_round)
        ].sort_values("round").tail(window)
        if past.empty:
            return NEUTRAL_DNF_RATE
        return sum(_is_dnf(str(s)) for s in past["status"]) / len(past)

    @staticmethod
    def _constructor_win_rate(results_df, constructor, before_round, window=20) -> float:
        if results_df.empty or "constructor" not in results_df.columns:
            return NEUTRAL_WIN_RATE
        past = results_df[
            (results_df["constructor"] == constructor) & (results_df["round"] < before_round)
        ].sort_values("round").tail(window)
        if past.empty:
            return NEUTRAL_WIN_RATE
        return sum(1 for p in past["position"] if p == 1) / len(past)

    @staticmethod
    def _circ_past(all_results, column, value, circuit_id, before_season, before_round, window):
        if all_results is None or all_results.empty or not circuit_id:
            return pd.DataFrame()
        mask = (
            (all_results[column] == value)
            & (all_results["circuit_id"] == circuit_id)
            & (
                (all_results["season"] < before_season)
                | ((all_results["season"] == before_season) & (all_results["round"] < before_round))
            )
        )
        return all_results[mask].sort_values(["season", "round"])

    @staticmethod
    def _circuit_driver_avg_finish(all_results, driver_code, circuit_id, before_season, before_round, window=8) -> float:
        past = RacePredictor._circ_past(
            all_results, "driver_code", driver_code, circuit_id, before_season, before_round, window
        ).tail(window)
        return float(past["position"].mean()) if not past.empty else NEUTRAL_CIRCUIT_AVG_FINISH

    @staticmethod
    def _circuit_driver_win_rate(all_results, driver_code, circuit_id, before_season, before_round, window=8) -> float:
        past = RacePredictor._circ_past(
            all_results, "driver_code", driver_code, circuit_id, before_season, before_round, window
        ).tail(window)
        if past.empty:
            return NEUTRAL_CIRCUIT_WIN_RATE
        return sum(1 for p in past["position"] if p == 1) / len(past)

    @staticmethod
    def _circuit_driver_podium_rate(all_results, driver_code, circuit_id, before_season, before_round, window=8) -> float:
        past = RacePredictor._circ_past(
            all_results, "driver_code", driver_code, circuit_id, before_season, before_round, window
        ).tail(window)
        if past.empty:
            return NEUTRAL_PODIUM_RATE
        return sum(1 for p in past["position"] if p <= 3) / len(past)

    @staticmethod
    def _circuit_constructor_win_rate(all_results, constructor, circuit_id, before_season, before_round, window=10) -> float:
        past = RacePredictor._circ_past(
            all_results, "constructor", constructor, circuit_id, before_season, before_round, window
        ).tail(window)
        if past.empty:
            return NEUTRAL_CIRCUIT_WIN_RATE
        return sum(1 for p in past["position"] if p == 1) / len(past)

    @staticmethod
    def _championship_position(results_df, driver_code, before_round) -> float:
        if results_df.empty or "driver_code" not in results_df.columns:
            return NEUTRAL_CHAMPIONSHIP_POS
        pts = results_df[results_df["round"] < before_round].groupby("driver_code")["points"].sum()
        if pts.empty:
            return NEUTRAL_CHAMPIONSHIP_POS
        ranked = pts.sort_values(ascending=False).index.tolist()
        return float(ranked.index(driver_code) + 1) if driver_code in ranked else float(len(ranked) + 1)

    @staticmethod
    def _championship_gap(results_df, driver_code, before_round) -> float:
        if results_df.empty or "driver_code" not in results_df.columns:
            return 0.0
        pts = results_df[results_df["round"] < before_round].groupby("driver_code")["points"].sum()
        if pts.empty:
            return 0.0
        return float(pts.max()) - float(pts.get(driver_code, 0))

    @staticmethod
    def _cumulative_points(results_df, driver_code, before_round) -> float:
        if results_df.empty or "driver_code" not in results_df.columns:
            return 0.0
        past = results_df[
            (results_df["driver_code"] == driver_code) & (results_df["round"] < before_round)
        ]
        return float(past["points"].sum()) if not past.empty else 0.0

    # ── Pit stop helpers ──────────────────────────────────────────────────────

    def _build_pitstop_df(self, season, schedule):
        rows = []
        for race in schedule:
            round_num = race["round"]
            stops = self.client.get_pit_stops(season, round_num)
            if not stops:
                continue
            by_driver: dict[str, list] = {}
            for s in stops:
                dc = s.get("driver_code")
                if dc:
                    by_driver.setdefault(dc, []).append(s)
            for dc, ds in by_driver.items():
                durations = [
                    d for d in (self._parse_duration(s.get("duration")) for s in ds) if d is not None
                ]
                rows.append({
                    "round": round_num,
                    "driver_code": dc,
                    "stop_count": len(ds),
                    "avg_duration": float(np.mean(durations)) if durations else None,
                })
        return pd.DataFrame(rows, columns=["round", "driver_code", "stop_count", "avg_duration"])

    def _driver_pit_strategy(self, pit_df, driver_id, before_round, window=5):
        if pit_df.empty or not driver_id:
            return NEUTRAL_PIT_STOPS, NEUTRAL_PIT_DURATION
        past = pit_df[
            (pit_df["driver_code"] == driver_id) & (pit_df["round"] < before_round)
        ].sort_values("round").tail(window)
        if past.empty:
            return NEUTRAL_PIT_STOPS, NEUTRAL_PIT_DURATION
        avg_stops = float(past["stop_count"].mean())
        durs = past["avg_duration"].dropna()
        return avg_stops, (float(durs.mean()) if not durs.empty else NEUTRAL_PIT_DURATION)

    # ── Elo helpers ───────────────────────────────────────────────────────────

    def _build_elo_snapshots(self, all_results: pd.DataFrame) -> dict:
        """
        Build a time-ordered dict of Elo snapshots keyed by (season, round).
        Returns: {(season, round): (driver_ratings_dict, constructor_ratings_dict)}
        """
        elo = EloRatingSystem()
        snapshots = {}

        if all_results.empty:
            return snapshots

        for (season, round_num), group in all_results.sort_values(["season", "round"]).groupby(["season", "round"]):
            # Snapshot BEFORE this race (for use as features)
            snapshots[(season, round_num)] = elo.snapshot()
            # Then update with race outcomes
            race_rows = group[["driver_code", "constructor", "position"]].to_dict("records")
            elo.process_race(race_rows)

        return snapshots

    # ── All-results builder ───────────────────────────────────────────────────

    def _build_all_results(self, seasons) -> pd.DataFrame:
        rows = []
        for season in seasons:
            schedule = self.client.get_schedule(season)
            circ_map = {r["round"]: r.get("circuit_id", "") for r in schedule}
            country_map = {r["round"]: r.get("country", "").lower() for r in schedule}
            results = self.client.get_season_results(season)
            for r in results:
                entry = dict(r)
                entry["circuit_id"] = circ_map.get(entry.get("round"), "")
                entry["circuit_country"] = country_map.get(entry.get("round"), "")
                entry["season"] = season
                rows.append(entry)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        if "status" not in df.columns:
            df["status"] = ""
        # Nationality mappings for home race detection
        if "nationality" not in df.columns:
            df["nationality"] = ""
        return df

    # ── Driver nationality lookup ──────────────────────────────────────────────

    def _build_nationality_map(self, driver_standings) -> dict[str, str]:
        """Returns {driver_code: nationality_lower} from standings data."""
        nm = {}
        for d in driver_standings:
            code = d.get("driver_code", "")
            nat = d.get("nationality", "").lower()
            if code:
                nm[code] = nat
        return nm

    # ══════════════════════════════════════════════════════════════════════════
    # Training Data Builder
    # ══════════════════════════════════════════════════════════════════════════

    def _build_training_rows(self, seasons) -> pd.DataFrame:
        all_results = self._build_all_results(seasons)
        elo_snapshots = self._build_elo_snapshots(all_results)

        rows = []
        season_labels = []  # for GroupKFold

        for season in seasons:
            schedule = self.client.get_schedule(season)
            total_rounds = max(len(schedule), 1)
            season_results = (
                all_results[all_results["season"] == season].copy()
                if not all_results.empty
                else pd.DataFrame()
            )
            if season_results.empty or "round" not in season_results.columns:
                continue

            season_quali = pd.DataFrame(self.client.get_season_qualifying(season))
            pit_df = self._build_pitstop_df(season, schedule)

            for race in schedule:
                round_num = race["round"]
                circuit_id = race.get("circuit_id", "")
                circuit_country = race.get("country", "").lower()
                round_results = season_results[season_results["round"] == round_num]
                if round_results.empty:
                    continue

                quali_round = (
                    season_quali[season_quali["round"] == round_num]
                    if not season_quali.empty
                    else pd.DataFrame()
                )

                # Elo snapshot BEFORE this race
                d_elo, c_elo = elo_snapshots.get((season, round_num), ({}, {}))

                for _, r in round_results.iterrows():
                    driver_code = r.get("driver_code")
                    if not driver_code:
                        continue
                    constructor = str(r.get("constructor", ""))
                    grid = int(r.get("grid") or 20)
                    nationality = str(r.get("nationality", "")).lower()

                    quali_pos = grid
                    if not quali_round.empty:
                        m = quali_round[quali_round["driver_code"] == driver_code]
                        if not m.empty:
                            quali_pos = int(m.iloc[0]["position"])

                    avg_stops, avg_dur = self._driver_pit_strategy(pit_df, r.get("driver_id"), round_num)
                    q_gap = self._quali_gap_to_pole(quali_round, driver_code)
                    q_norm = self._quali_gap_normalized(quali_round, driver_code)

                    # Home race: nationality matches circuit country
                    home_bonus = 1.0 if (nationality and circuit_country and nationality in circuit_country) else 0.0

                    rows.append({
                        "grid": grid,
                        "grid_squared": grid ** 2,
                        "quali_position": quali_pos,
                        "quali_gap_to_pole": q_gap,
                        "quali_gap_normalized": q_norm,
                        "quali_vs_teammate": self._quali_vs_teammate(quali_round, driver_code, constructor),
                        "driver_form": self._driver_form(season_results, driver_code, round_num),
                        "driver_form_variance": self._driver_form_variance(season_results, driver_code, round_num),
                        "driver_momentum": self._driver_momentum(season_results, driver_code, round_num),
                        "driver_dnf_rate": self._driver_dnf_rate(season_results, driver_code, round_num),
                        "driver_incident_rate": self._driver_incident_rate(season_results, driver_code, round_num),
                        "driver_experience": self._driver_experience(season_results, driver_code, round_num),
                        "driver_elo": d_elo.get(driver_code, NEUTRAL_ELO),
                        "constructor_form": self._constructor_form(season_results, constructor, round_num),
                        "constructor_dnf_rate": self._constructor_dnf_rate(season_results, constructor, round_num),
                        "constructor_win_rate": self._constructor_win_rate(season_results, constructor, round_num),
                        "constructor_elo": c_elo.get(constructor, NEUTRAL_ELO),
                        "circuit_driver_avg_finish": self._circuit_driver_avg_finish(all_results, driver_code, circuit_id, season, round_num),
                        "circuit_driver_win_rate": self._circuit_driver_win_rate(all_results, driver_code, circuit_id, season, round_num),
                        "circuit_driver_podium_rate": self._circuit_driver_podium_rate(all_results, driver_code, circuit_id, season, round_num),
                        "circuit_constructor_win_rate": self._circuit_constructor_win_rate(all_results, constructor, circuit_id, season, round_num),
                        "championship_position": self._championship_position(season_results, driver_code, round_num),
                        "championship_gap": self._championship_gap(season_results, driver_code, round_num),
                        "season_progress": round_num / total_rounds,
                        "home_race_bonus": home_bonus,
                        "cumulative_points": self._cumulative_points(season_results, driver_code, round_num),
                        "avg_pit_stops": avg_stops,
                        "avg_pit_duration": avg_dur,
                        "live_current_position": grid,
                        "live_laps_completed": 0,
                        "finishing_position": int(r.get("position", 20)),
                        "_season": season,  # for GroupKFold
                        "_round": round_num,  # for per-race calibration
                    })
                    season_labels.append(season)

        return pd.DataFrame(rows, columns=FEATURE_COLUMNS + ["finishing_position", "_season", "_round"])

    # ══════════════════════════════════════════════════════════════════════════
    # Hyperparameter Optimization (50 trials per model)
    # ══════════════════════════════════════════════════════════════════════════

    def _tune_xgboost(self, X_tr, y_tr, X_val, y_val, n_trials=50) -> dict:
        xgb = _import_xgb()
        from sklearn.metrics import mean_absolute_error

        def objective(trial):
            params = {
                "n_estimators": trial.suggest_int("n_estimators", 100, 800),
                "max_depth": trial.suggest_int("max_depth", 3, 9),
                "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.2, log=True),
                "min_child_weight": trial.suggest_int("min_child_weight", 1, 10),
                "subsample": trial.suggest_float("subsample", 0.5, 1.0),
                "colsample_bytree": trial.suggest_float("colsample_bytree", 0.4, 1.0),
                "reg_alpha": trial.suggest_float("reg_alpha", 1e-4, 10.0, log=True),
                "reg_lambda": trial.suggest_float("reg_lambda", 1e-4, 10.0, log=True),
                "random_state": 42,
                "tree_method": "hist",
                "objective": "reg:squarederror",
                "verbosity": 0,
            }
            model = xgb.XGBRegressor(**params)
            model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], verbose=False)
            return mean_absolute_error(y_val, model.predict(X_val))

        study = optuna.create_study(
            direction="minimize",
            sampler=TPESampler(seed=42),
            pruner=MedianPruner(n_startup_trials=10, n_warmup_steps=5),
        )
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
        return study.best_params

    def _tune_lightgbm(self, X_tr, y_tr, X_val, y_val, n_trials=50) -> dict:
        lgb = _import_lgb()
        from sklearn.metrics import mean_absolute_error

        def objective(trial):
            params = {
                "n_estimators": trial.suggest_int("n_estimators", 100, 800),
                "num_leaves": trial.suggest_int("num_leaves", 20, 300),
                "max_depth": trial.suggest_int("max_depth", 3, 12),
                "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.2, log=True),
                "min_child_samples": trial.suggest_int("min_child_samples", 5, 100),
                "subsample": trial.suggest_float("subsample", 0.5, 1.0),
                "colsample_bytree": trial.suggest_float("colsample_bytree", 0.4, 1.0),
                "reg_alpha": trial.suggest_float("reg_alpha", 1e-4, 10.0, log=True),
                "reg_lambda": trial.suggest_float("reg_lambda", 1e-4, 10.0, log=True),
                "random_state": 42,
                "verbose": -1,
                "objective": "regression",
            }
            model = lgb.LGBMRegressor(**params)
            model.fit(
                X_tr, y_tr,
                eval_set=[(X_val, y_val)],
                callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(-1)],
            )
            return mean_absolute_error(y_val, model.predict(X_val))

        study = optuna.create_study(
            direction="minimize",
            sampler=TPESampler(seed=42),
            pruner=MedianPruner(n_startup_trials=10, n_warmup_steps=5),
        )
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
        return study.best_params

    def _tune_catboost(self, X_tr, y_tr, X_val, y_val, n_trials=50) -> dict:
        CatBoostRegressor = _import_cb()
        from sklearn.metrics import mean_absolute_error

        def objective(trial):
            params = {
                "iterations": trial.suggest_int("iterations", 100, 800),
                "depth": trial.suggest_int("depth", 3, 10),
                "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.2, log=True),
                "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 1e-4, 10.0, log=True),
                "subsample": trial.suggest_float("subsample", 0.5, 1.0),
                "colsample_bylevel": trial.suggest_float("colsample_bylevel", 0.4, 1.0),
                "random_seed": 42,
                "verbose": 0,
                "loss_function": "RMSE",
            }
            model = CatBoostRegressor(**params)
            model.fit(
                X_tr, y_tr,
                eval_set=(X_val, y_val),
                early_stopping_rounds=50,
                verbose=False,
            )
            return mean_absolute_error(y_val, model.predict(X_val))

        study = optuna.create_study(
            direction="minimize",
            sampler=TPESampler(seed=42),
            pruner=MedianPruner(n_startup_trials=10, n_warmup_steps=5),
        )
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
        return study.best_params

    # ══════════════════════════════════════════════════════════════════════════
    # OOF Stacking
    # ══════════════════════════════════════════════════════════════════════════

    def _build_oof_predictions(self, X: np.ndarray, y: np.ndarray, groups: np.ndarray,
                                xgb_params: dict, lgb_params: dict, cb_params: dict) -> np.ndarray:
        """
        Build Out-Of-Fold (OOF) predictions for all 3 base models.
        Returns shape (N, 3) — one column per model.
        Uses GroupKFold to ensure no future-race leakage.
        """
        from sklearn.model_selection import GroupKFold
        xgb = _import_xgb()
        lgb = _import_lgb()
        CatBoostRegressor = _import_cb()

        n_splits = 5
        gkf = GroupKFold(n_splits=n_splits)
        oof = np.zeros((len(X), 3))

        for fold, (train_idx, val_idx) in enumerate(gkf.split(X, y, groups)):
            X_tr, X_val = X[train_idx], X[val_idx]
            y_tr, y_val_arr = y[train_idx], y[val_idx]

            # XGBoost
            _xp = {**xgb_params, "random_state": 42, "tree_method": "hist",
                   "objective": "reg:squarederror", "verbosity": 0}
            m_xgb = xgb.XGBRegressor(**_xp)
            m_xgb.fit(X_tr, y_tr, verbose=False)
            oof[val_idx, 0] = m_xgb.predict(X_val)

            # LightGBM
            _lp = {**lgb_params, "random_state": 42, "verbose": -1, "objective": "regression"}
            m_lgb = lgb.LGBMRegressor(**_lp)
            m_lgb.fit(X_tr, y_tr, callbacks=[lgb.log_evaluation(-1)])
            oof[val_idx, 1] = m_lgb.predict(X_val)

            # CatBoost
            _cp = {**cb_params, "random_seed": 42, "verbose": 0, "loss_function": "RMSE"}
            m_cb = CatBoostRegressor(**_cp)
            m_cb.fit(X_tr, y_tr, verbose=False)
            oof[val_idx, 2] = m_cb.predict(X_val)

            print(f"  [OOF] Fold {fold + 1}/{n_splits} done")

        return oof

    # ══════════════════════════════════════════════════════════════════════════
    # Train
    # ══════════════════════════════════════════════════════════════════════════

    def train(self, seasons=None, n_optuna_trials=DEFAULT_OPTUNA_TRIALS):
        if not self._train_lock.acquire(blocking=False):
            print("[Predictor] Training already in progress, skipping duplicate request.")
            return False
        try:
            return self._do_train(seasons, n_optuna_trials)
        finally:
            self._train_lock.release()

    def _do_train(self, seasons=None, n_optuna_trials=DEFAULT_OPTUNA_TRIALS):
        if seasons is None:
            yr = datetime.now(timezone.utc).year
            n_seasons = DEFAULT_TRAINING_SEASONS
            seasons = list(range(yr - n_seasons + 1, yr + 1))

        print(f"[Predictor] Building training data for seasons: {seasons}")
        df = self._build_training_rows(seasons)
        if df.empty or len(df) < 40:
            print("[Predictor] Not enough completed-race data to train yet.")
            return False

        X = df[FEATURE_COLUMNS].fillna(df[FEATURE_COLUMNS].median()).values
        y = df["finishing_position"].values
        groups = df["_season"].values

        from sklearn.model_selection import train_test_split
        from sklearn.metrics import mean_absolute_error
        from sklearn.linear_model import Ridge

        # One held-out season for HPO validation
        unique_seasons = sorted(df["_season"].unique())
        hpo_val_season = unique_seasons[-1]  # most recent season for validation
        hpo_mask = df["_season"] == hpo_val_season
        X_hpo_tr = X[~hpo_mask]
        y_hpo_tr = y[~hpo_mask]
        X_hpo_val = X[hpo_mask]
        y_hpo_val = y[hpo_mask]

        # ── HPO ───────────────────────────────────────────────────────────────
        print(f"[Predictor] Tuning XGBoost ({n_optuna_trials} trials)...")
        xgb_params = self._tune_xgboost(X_hpo_tr, y_hpo_tr, X_hpo_val, y_hpo_val, n_optuna_trials)
        print(f"[Predictor] XGBoost best: {xgb_params}")

        print(f"[Predictor] Tuning LightGBM ({n_optuna_trials} trials)...")
        lgb_params = self._tune_lightgbm(X_hpo_tr, y_hpo_tr, X_hpo_val, y_hpo_val, n_optuna_trials)
        print(f"[Predictor] LightGBM best: {lgb_params}")

        print(f"[Predictor] Tuning CatBoost ({n_optuna_trials} trials)...")
        cb_params = self._tune_catboost(X_hpo_tr, y_hpo_tr, X_hpo_val, y_hpo_val, n_optuna_trials)
        print(f"[Predictor] CatBoost best: {cb_params}")

        # ── OOF stacking ─────────────────────────────────────────────────────
        print("[Predictor] Building OOF predictions (GroupKFold by season)...")
        oof_preds = self._build_oof_predictions(X, y, groups, xgb_params, lgb_params, cb_params)

        # Meta-learner: Ridge on OOF
        meta = Ridge(alpha=1.0)
        meta.fit(oof_preds, y)
        oof_meta = meta.predict(oof_preds)
        mae_oof = mean_absolute_error(y, oof_meta)
        print(f"[Predictor] OOF MAE (stacked): {mae_oof:.3f} positions")

        # ── Retrain base models on ALL data ──────────────────────────────────
        print("[Predictor] Retraining base models on full dataset...")
        xgb_mod = _import_xgb().XGBRegressor(
            **{**xgb_params, "random_state": 42, "tree_method": "hist",
               "objective": "reg:squarederror", "verbosity": 0}
        )
        xgb_mod.fit(X, y, verbose=False)

        lgb_mod = _import_lgb().LGBMRegressor(
            **{**lgb_params, "random_state": 42, "verbose": -1, "objective": "regression"}
        )
        lgb_mod.fit(X, y, callbacks=[_import_lgb().log_evaluation(-1)])

        cb_mod = _import_cb()(
            **{**cb_params, "random_seed": 42, "verbose": 0, "loss_function": "RMSE"}
        )
        cb_mod.fit(X, y, verbose=False)

        # ── Win-probability calibration ───────────────────────────────────────
        # A race has exactly one winner, so probabilities must be normalised
        # within each race (not across the whole dataset). Fit the softmax
        # temperature that maximises the likelihood of the actual winners
        # on out-of-fold predictions.
        race_ids = df["_season"].astype(str).values + "-" + df["_round"].astype(str).values
        temp = self._fit_softmax_temperature(oof_meta, y, race_ids)
        print(f"[Predictor] Softmax temperature fitted: T = {temp:.3f}")

        trained_at = datetime.now(timezone.utc)
        bundle = {
            "xgb_model": xgb_mod,
            "lgb_model": lgb_mod,
            "cb_model": cb_mod,
            "meta_model": meta,
            "softmax_temp": temp,
            "data_version": MODEL_DATA_VERSION,
            "trained_at": trained_at,
            "seasons": seasons,
            "feature_columns": FEATURE_COLUMNS,
            "oof_mae": mae_oof,
            "n_training_rows": len(df),
            "xgb_params": xgb_params,
            "lgb_params": lgb_params,
            "cb_params": cb_params,
        }

        with self._lock:
            self._model_bundle = bundle
            self._trained_at = trained_at

        try:
            # Remove old format model if it exists
            if os.path.exists(MODEL_PATH):
                old = load(MODEL_PATH)
                if "xgb_model" not in old:
                    os.remove(MODEL_PATH)
            dump(bundle, MODEL_PATH, compress=3)
        except OSError as e:
            print(f"[Predictor] Could not persist model: {e}")

        print(
            f"[Predictor] ✅ Ensemble trained: {len(df)} rows, "
            f"{len(FEATURE_COLUMNS)} features, {len(seasons)} seasons, "
            f"OOF MAE = {mae_oof:.3f}"
        )
        return True

    # ══════════════════════════════════════════════════════════════════════════
    # Model Loading
    # ══════════════════════════════════════════════════════════════════════════

    def _ensure_model(self) -> bool:
        """Make a model available without ever blocking a request on training.

        A stale model is still a good model: serve it and refresh it in the
        background. Only when no model exists at all do we report False
        (while a background train is kicked off)."""
        with self._lock:
            have = self._model_bundle is not None
            trained_at = self._trained_at
        if not have and os.path.exists(MODEL_PATH):
            try:
                payload = load(MODEL_PATH)
                if "xgb_model" not in payload or payload.get("feature_columns") != FEATURE_COLUMNS:
                    print("[Predictor] Saved model is from an older feature set — discarding.")
                    os.remove(MODEL_PATH)
                else:
                    with self._lock:
                        self._model_bundle = payload
                        self._trained_at = trained_at = payload["trained_at"]
                    have = True
            except Exception as e:
                print(f"[Predictor] Saved model unreadable: {e}")

        if have:
            age_h = (datetime.now(timezone.utc) - trained_at).total_seconds() / 3600
            outdated = self._model_bundle.get("data_version", 1) < MODEL_DATA_VERSION
            if outdated or age_h > MODEL_MAX_AGE_HOURS:
                self.train_in_background()
            return True

        self.train_in_background()
        return False

    def train_in_background(self, seasons=None):
        """Start a training run on a daemon thread unless one is running."""
        if self.is_training:
            return False
        threading.Thread(target=self.train, kwargs={"seasons": seasons},
                         daemon=True, name="predictor-train").start()
        return True

    @property
    def is_training(self) -> bool:
        return self._train_lock.locked()

    # ══════════════════════════════════════════════════════════════════════════
    # Inference
    # ══════════════════════════════════════════════════════════════════════════

    def _ensemble_predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        Run all 3 base models + meta-learner. Returns:
          (stacked_positions, xgb_positions, lgb_positions, cb_positions)
        """
        bundle = self._model_bundle
        xgb_preds = bundle["xgb_model"].predict(X)
        lgb_preds = bundle["lgb_model"].predict(X)
        cb_preds = bundle["cb_model"].predict(X)
        oof_stack = np.column_stack([xgb_preds, lgb_preds, cb_preds])
        stacked = bundle["meta_model"].predict(oof_stack)
        return stacked, xgb_preds, lgb_preds, cb_preds

    @staticmethod
    def _race_softmax(positions: np.ndarray, temp: float) -> np.ndarray:
        """Win probabilities for one race: softmax over negative predicted
        finishing positions (lower predicted position → higher chance)."""
        z = -(positions - positions.min()) / max(temp, 1e-3)
        e = np.exp(z)
        return e / e.sum()

    @classmethod
    def _fit_softmax_temperature(cls, preds, finishing, race_ids) -> float:
        """Grid-search the temperature minimising winner negative log-likelihood."""
        races = [np.where(race_ids == r)[0] for r in np.unique(race_ids)]
        races = [idx for idx in races if (finishing[idx] == 1).any()]
        best_t, best_nll = 2.5, float("inf")
        for t in np.geomspace(0.2, 10, 60):
            nll = 0.0
            for idx in races:
                p = cls._race_softmax(preds[idx], t)
                nll -= np.log(p[np.argmax(finishing[idx] == 1)] + 1e-12)
            if nll < best_nll:
                best_t, best_nll = float(t), nll
        return best_t

    def _calibrated_win_probs(self, stacked_positions: np.ndarray) -> np.ndarray:
        """Convert one race's predicted positions → win probabilities."""
        temp = self._model_bundle.get("softmax_temp", 2.5)
        return self._race_softmax(np.asarray(stacked_positions, dtype=float), temp)

    def _compute_shap(self, X: np.ndarray) -> list[list[dict]]:
        """
        Compute SHAP values for the XGBoost model (fastest & most interpretable).
        Returns per-driver list of {feature, shap_value, feature_value} dicts.
        """
        try:
            shap_lib = _import_shap()
            explainer = shap_lib.TreeExplainer(self._model_bundle["xgb_model"])
            shap_values = explainer.shap_values(X)  # (N, n_features)
            result = []
            for i in range(len(X)):
                driver_shap = []
                for j, feat in enumerate(FEATURE_COLUMNS):
                    driver_shap.append({
                        "feature": feat,
                        "shap_value": float(shap_values[i, j]),
                        "feature_value": float(X[i, j]),
                    })
                # Sort by |shap_value| descending
                driver_shap.sort(key=lambda x: abs(x["shap_value"]), reverse=True)
                result.append(driver_shap[:10])  # top 10 features
            return result
        except Exception as e:
            print(f"[Predictor] SHAP computation failed: {e}")
            return [[] for _ in range(len(X))]

    def _build_inference_df(self, season, round_num, live_overrides=None):
        driver_standings = self.client.get_driver_standings(season)
        qualifying = self.client.get_qualifying(season, round_num)
        schedule = self.client.get_schedule(season)
        total_rounds = max(len(schedule), 1)
        pit_df = self._build_pitstop_df(season, schedule)
        yr = datetime.now(timezone.utc).year
        n_seasons = DEFAULT_TRAINING_SEASONS
        hist_seasons = list(range(yr - n_seasons + 1, yr + 1))
        all_results = self._build_all_results(hist_seasons)

        # Build live Elo from all history
        elo_snapshots = self._build_elo_snapshots(all_results)
        # Use the latest Elo snapshot available
        if elo_snapshots:
            last_key = sorted(elo_snapshots.keys())[-1]
            d_elo_cur, c_elo_cur = elo_snapshots[last_key]
        else:
            d_elo_cur, c_elo_cur = {}, {}

        season_res_list = self.client.get_season_results(season)
        season_results = pd.DataFrame(season_res_list) if season_res_list else pd.DataFrame()
        if not season_results.empty and "status" not in season_results.columns:
            season_results["status"] = ""

        circuit_id = next((r.get("circuit_id", "") for r in schedule if r["round"] == round_num), "")
        circuit_country = next((r.get("country", "").lower() for r in schedule if r["round"] == round_num), "")

        quali_df = pd.DataFrame(qualifying) if qualifying else pd.DataFrame()
        quali_by_code = {q["driver_code"]: q["position"] for q in qualifying} if qualifying else {}

        standings_pos = {d["driver_code"]: i + 1 for i, d in enumerate(driver_standings)}
        max_pts = max((d.get("points", 0) for d in driver_standings), default=0)
        standings_pts = {d["driver_code"]: d.get("points", 0) for d in driver_standings}

        # Nationality map
        nat_map = self._build_nationality_map(driver_standings)

        feature_rows, meta_rows = [], []

        for d in driver_standings:
            driver_code = d.get("driver_code")
            if not driver_code:
                continue
            constructor = str(d.get("constructor", ""))
            grid = int(quali_by_code.get(driver_code, 20))
            nationality = nat_map.get(driver_code, "")

            dr_form = self._driver_form(season_results, driver_code, round_num) if not season_results.empty else NEUTRAL_FORM
            dr_fvar = self._driver_form_variance(season_results, driver_code, round_num) if not season_results.empty else 5.0
            dr_mom = self._driver_momentum(season_results, driver_code, round_num) if not season_results.empty else NEUTRAL_MOMENTUM
            dr_dnf = self._driver_dnf_rate(season_results, driver_code, round_num) if not season_results.empty else NEUTRAL_DNF_RATE
            dr_inc = self._driver_incident_rate(season_results, driver_code, round_num) if not season_results.empty else NEUTRAL_INCIDENT_RATE
            dr_exp = self._driver_experience(season_results, driver_code, round_num) if not season_results.empty else NEUTRAL_DRIVER_EXPERIENCE
            dr_elo = d_elo_cur.get(driver_code, NEUTRAL_ELO)

            con_form = self._constructor_form(season_results, constructor, round_num) if not season_results.empty else NEUTRAL_FORM
            con_dnf = self._constructor_dnf_rate(season_results, constructor, round_num) if not season_results.empty else NEUTRAL_DNF_RATE
            con_win = self._constructor_win_rate(season_results, constructor, round_num) if not season_results.empty else NEUTRAL_WIN_RATE
            con_elo = c_elo_cur.get(constructor, NEUTRAL_ELO)

            circ_avg = self._circuit_driver_avg_finish(all_results, driver_code, circuit_id, season, round_num)
            circ_win = self._circuit_driver_win_rate(all_results, driver_code, circuit_id, season, round_num)
            circ_pod = self._circuit_driver_podium_rate(all_results, driver_code, circuit_id, season, round_num)
            circ_con = self._circuit_constructor_win_rate(all_results, constructor, circuit_id, season, round_num)

            q_gap = self._quali_gap_to_pole(quali_df, driver_code)
            q_norm = self._quali_gap_normalized(quali_df, driver_code)
            q_vs_tm = self._quali_vs_teammate(quali_df, driver_code, constructor)

            champ_pos = float(standings_pos.get(driver_code, NEUTRAL_CHAMPIONSHIP_POS))
            champ_gap = float(max_pts) - float(standings_pts.get(driver_code, 0))
            s_prog = round_num / total_rounds
            cum_pts = float(standings_pts.get(driver_code, 0))

            home_bonus = 1.0 if (nationality and circuit_country and nationality in circuit_country) else 0.0

            avg_stops, avg_dur = self._driver_pit_strategy(pit_df, d.get("driver_id"), round_num)

            live_pos = grid
            live_laps = 0
            if live_overrides and driver_code in live_overrides:
                live_pos = live_overrides[driver_code].get("position", grid)
                live_laps = live_overrides[driver_code].get("laps", 0)

            feature_rows.append([
                grid, grid ** 2, grid, q_gap, q_norm, q_vs_tm,
                dr_form, dr_fvar, dr_mom, dr_dnf, dr_inc, dr_exp, dr_elo,
                con_form, con_dnf, con_win, con_elo,
                circ_avg, circ_win, circ_pod, circ_con,
                champ_pos, champ_gap, s_prog, home_bonus,
                cum_pts, avg_stops, avg_dur,
                live_pos, live_laps,
            ])
            meta_rows.append({
                "driver_code": driver_code,
                "driver_name": d.get("driver_name"),
                "constructor": constructor,
                "driver_elo": round(dr_elo, 1),
                "quali_gap_to_pole": round(q_gap, 3),
                "quali_vs_teammate": round(q_vs_tm, 1),
                "circuit_driver_avg_finish": round(circ_avg, 1),
                "championship_position": int(champ_pos),
                "grid": grid,
                "live_pos": live_pos,
                "live_laps": live_laps,
                "home_race": bool(home_bonus),
            })

        return pd.DataFrame(feature_rows, columns=FEATURE_COLUMNS), meta_rows, circuit_id

    # ══════════════════════════════════════════════════════════════════════════
    # Public Prediction API
    # ══════════════════════════════════════════════════════════════════════════

    def predict_race(self, season, round_num, include_shap=True):
        if not self._ensure_model():
            return {"error": "The model is training on historical data. Check back in a few minutes.",
                    "training": True, "predictions": []}

        X_df, meta_rows, circuit_id = self._build_inference_df(season, round_num)
        if X_df.empty:
            return {"error": "No driver data available.", "predictions": []}

        X = X_df.fillna(X_df.median()).values
        stacked, xgb_p, lgb_p, cb_p = self._ensemble_predict(X)
        win_probs = self._calibrated_win_probs(stacked)

        predictions = []
        for i, meta in enumerate(meta_rows):
            meta["predicted_position"] = float(stacked[i])
            meta["win_probability"] = round(float(win_probs[i]), 4)
            meta["ensemble_breakdown"] = {
                "xgboost": round(float(xgb_p[i]), 2),
                "lightgbm": round(float(lgb_p[i]), 2),
                "catboost": round(float(cb_p[i]), 2),
            }
            predictions.append(meta)

        predictions.sort(key=lambda x: x["predicted_position"])

        # SHAP values (optional — can be skipped for speed)
        shap_values = self._compute_shap(X) if include_shap else [[] for _ in range(len(X))]
        # Re-index shap by driver_code for easy lookup
        shap_by_code = {}
        for i, meta in enumerate(predictions):
            original_idx = meta_rows.index(meta)
            shap_by_code[meta["driver_code"]] = shap_values[original_idx] if include_shap else []

        bundle = self._model_bundle
        return {
            "error": None,
            "predictions": predictions,
            "shap_by_driver": shap_by_code,
            "model_trained_at": self._trained_at.isoformat() if self._trained_at else None,
            "feature_count": len(FEATURE_COLUMNS),
            "circuit_id": circuit_id,
            "oof_mae": round(bundle.get("oof_mae", 0), 3),
            "n_training_rows": bundle.get("n_training_rows", 0),
            "training_seasons": bundle.get("seasons", []),
            "model_type": "XGBoost+LightGBM+CatBoost Stacking Ensemble",
        }

    def predict_live(self, season, round_num, live_standings):
        """
        Real-time prediction with live race position overrides.
        live_standings: list of {driver_code, position, laps}
        """
        if not self._ensure_model():
            return {"error": "Model not ready.", "predictions": []}

        live_overrides = {d["driver_code"]: d for d in live_standings}
        X_df, meta_rows, circuit_id = self._build_inference_df(season, round_num, live_overrides)
        if X_df.empty:
            return {"error": "No driver data available.", "predictions": []}

        X = X_df.fillna(X_df.median()).values
        stacked, xgb_p, lgb_p, cb_p = self._ensemble_predict(X)
        win_probs = self._calibrated_win_probs(stacked)

        predictions = []
        for i, meta in enumerate(meta_rows):
            meta["predicted_position"] = float(stacked[i])
            meta["win_probability"] = round(float(win_probs[i]), 4)
            meta["ensemble_breakdown"] = {
                "xgboost": round(float(xgb_p[i]), 2),
                "lightgbm": round(float(lgb_p[i]), 2),
                "catboost": round(float(cb_p[i]), 2),
            }
            predictions.append(meta)

        predictions.sort(key=lambda x: x["predicted_position"])

        return {
            "error": None,
            "predictions": predictions,
        }

    def get_model_info(self) -> dict:
        """Return metadata about the currently loaded model."""
        with self._lock:
            if not self._model_bundle:
                return {"loaded": False, "training": self.is_training}
            bundle = self._model_bundle
            return {
                "loaded": True,
                "model_type": "XGBoost + LightGBM + CatBoost Stacking Ensemble",
                "trained_at": self._trained_at.isoformat() if self._trained_at else None,
                "seasons": bundle.get("seasons", []),
                "n_training_rows": bundle.get("n_training_rows", 0),
                "feature_count": len(FEATURE_COLUMNS),
                "oof_mae": round(bundle.get("oof_mae", 0), 3),
                "training": self.is_training,
            }
