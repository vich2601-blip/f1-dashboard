# 🏎️ Apex — Formula 1 Grand Prix Control Room

A full-featured Formula 1 dashboard with real-time timing, championship standings, race analytics, head-to-head driver comparisons, and an ML-powered race winner predictor — all wrapped in a dark, pit-wall-inspired UI.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-SocketIO-000000?logo=flask&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

---

## ✨ Features

### 📊 Dashboard
- **Title fight**: live championship maths for drivers *and* constructors. It shows who can still win, each contender's maximum reachable points, and exactly what the leader needs to clinch at the next round (sprints included)
- Championship standings (drivers & constructors) at a glance
- Next race countdown with circuit info and sprint weekend badges
- Recent race results with winner highlights

### 📅 Calendar
- Full season schedule with past/upcoming race indicators
- Sprint weekend markers and country flag emojis

### 🏁 Race Detail
- Complete race results with grid positions, time gaps, and fastest laps
- Qualifying results (Q1 / Q2 / Q3)
- Lap time charts (top 5 finishers)
- Position change chart across the race
- Pit stop timeline and tyre stint strategy visualization
- Sector timing analysis with purple / green / yellow classification

### 👤 Driver Profiles
- Season stats: wins, podiums, top 5/10, DNFs, average finish
- Race-by-race results table
- Cumulative points progression chart

### ⚔️ Head-to-Head Comparison
- Side-by-side driver stat comparison
- Head-to-head race & qualifying counts
- Points progression overlay chart

### 📡 Live Timing
- Real-time positions, intervals, and gap data via WebSocket
- Track map with live car positions
- Sector times with purple/green/yellow color coding
- Weather data and race control messages (flags, penalties)
- Tyre stint tracking
- Session picker across all race weekends
- Dual data source: **OpenF1** → **F1 Live Timing** automatic fallback

### 🤖 Race Winner Predictor
- **Stacking ensemble**: XGBoost + LightGBM + CatBoost
- **Optuna HPO**: 20 trials per model by default (`PREDICTOR_OPTUNA_TRIALS` to change)
- **40+ engineered features**: grid, qualifying gap, driver/constructor form, Elo ratings, circuit history, momentum, DNF rate, home race bonus, pit strategy, and more
- **Per-race softmax calibration** (temperature fitted to actual winners) for win probabilities that sum to 100%
- **SHAP explainability**: per-driver waterfall explanations
- **Live predictions**: adjusts probabilities based on current race positions
- Trains on 8 seasons of historical data with GroupKFold cross-validation

### ⚡ Performance
- SQLite caching layer with configurable TTLs (avoids redundant API calls)
- In-memory LRU cache for hot data (positions, weather, intervals)
- Per-host rate limiting (Jolpica / OpenF1) with 429 backoff, so a slow host never stalls another
- Full Jolpica pagination (the API caps pages at 100 rows), so every season is complete
- Stale-cache fallback: if an upstream API is down, the last good data is served instead of an empty page
- Live-page feeds fetched concurrently; dead upstreams are circuit-broken instead of retried on every load
- Predictor never blocks a request: the saved model serves while a fresh one trains in the background
- Bulk season data fetching (single API call per data type per season)
- Background model training (non-blocking server startup)
- HTTP keep-alive via `requests.Session`

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python · Flask · Flask-SocketIO (threading + simple-websocket) |
| **Data Sources** | [Jolpica (Ergast)](https://api.jolpi.ca) · [OpenF1](https://openf1.org) · [F1 Live Timing](https://livetiming.formula1.com) · [FastF1](https://docs.fastf1.dev) |
| **ML / Prediction** | XGBoost · LightGBM · CatBoost · scikit-learn · Optuna · SHAP |
| **Database** | SQLite (WAL mode) |
| **Frontend** | Jinja2 templates · Vanilla JS · Chart.js · WebSocket |
| **Real-time** | Socket.IO over native WebSockets (no eventlet) |

---

## 📁 Project Structure

```
f1-dashboard/
├── app.py                 # Flask application — routes, WebSocket handlers, background emitter
├── api_client.py          # Unified F1 data client (Jolpica + OpenF1 + F1 Live Timing)
├── fastf1_client.py       # FastF1 wrapper for telemetry and detailed lap data
├── predictor.py           # ML race predictor (stacking ensemble + Elo + SHAP)
├── championship.py        # Title-fight maths: contention, ceilings, clinch scenarios
├── database.py            # SQLite caching layer
├── requirements.txt       # Python dependencies
├── .env.example           # Environment variable template
├── win_predictor.joblib   # Serialized trained model (auto-generated)
├── f1_cache.db            # SQLite cache database (auto-generated)
├── static/
│   ├── css/
│   │   └── style.css      # Component styles
│   ├── js/
│   │   ├── main.js        # Client-side logic, charts, WebSocket, command palette
│   │   └── sw.js          # Service worker (PWA offline support)
│   └── manifest.json      # PWA manifest
├── templates/
│   ├── base.html          # Base template with navbar and meta tags
│   ├── dashboard.html     # Home page — standings overview + countdown
│   ├── calendar.html      # Season calendar
│   ├── race.html          # Race detail — results, qualifying, charts
│   ├── driver.html        # Driver profile page
│   ├── compare.html       # Head-to-head comparison
│   ├── standings.html     # Full championship standings
│   ├── live.html          # Live timing page
│   ├── predict.html       # Race predictor UI
│   ├── error.html         # Error page (404 / 500)
│   └── components/
│       ├── navbar.html    # Navigation bar
│       └── footer.html    # Footer
├── fastf1_cache/          # FastF1 disk cache (auto-generated)
└── catboost_info/         # CatBoost training logs (auto-generated)
```

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.10+** (tested with 3.14)

### Installation

1. **Clone the repository**

   ```bash
   git clone https://github.com/vich2601-blip/f1-dashboard.git
   cd f1-dashboard
   ```

2. **Create a virtual environment**

   ```bash
   python -m venv venv
   source venv/bin/activate   # macOS / Linux
   venv\Scripts\activate      # Windows
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables** _(optional — for real-time data)_

   ```bash
   cp .env.example .env
   ```

   Edit `.env` and fill in your OpenF1 credentials if you want authenticated real-time data:

   ```env
   # Option 1: short-lived OAuth token
   OPENF1_ACCESS_TOKEN=your_token_here

   # Option 2: username/password for automatic token refresh
   OPENF1_USERNAME=your_username
   OPENF1_PASSWORD=your_password

   # Flask session security (required for production)
   SECRET_KEY=your_random_secret_key
   ```

   > **Note:** The app works without OpenF1 credentials — historical data and most live features use free, unauthenticated APIs. Credentials unlock additional real-time endpoints.

5. **Run the application**

   ```bash
   python app.py
   ```

   The server starts at **http://localhost:8050** with native WebSocket support.

---

## 📡 API Endpoints

### Pages

| Route | Description |
|---|---|
| `/` | Dashboard — standings overview, next race, recent results |
| `/calendar` | Full season calendar |
| `/race/<season>/<round>` | Race detail page |
| `/driver/<driver_id>` | Driver profile |
| `/compare?d1=VER&d2=NOR` | Head-to-head comparison |
| `/standings?season=2026` | Championship standings |
| `/live?session=<key>` | Live timing |
| `/predict` | Race predictor |

### REST API

| Endpoint | Description |
|---|---|
| `GET /api/live/<session_key>` | Live positions, intervals, locations |
| `GET /api/live/<session_key>?full=1` | Full live data (drivers, laps, weather, stints) |
| `GET /api/track/<session_key>` | Track outline coordinates |
| `GET /api/sectors/<season>/<round>` | Sector timing data |
| `GET /api/predict/<season>/<round>` | Race predictions with SHAP |
| `GET /api/predict/explain/<season>/<round>/<driver>` | Per-driver SHAP explanation |
| `POST /api/predict/live/<season>/<round>` | Live predictions with current positions |
| `POST /api/predict/retrain` | Force model retrain |
| `GET /api/refresh/<season>` | Clear cached data for a season |
| `GET /api/fastf1/telemetry/<year>/<round>/<driver>/<lap>` | Full lap telemetry |
| `GET /api/fastf1/comparison/<year>/<round>?d1=VER&d2=NOR` | Two-driver telemetry comparison |

### WebSocket Events

| Event | Direction | Description |
|---|---|---|
| `join_session` | Client → Server | Subscribe to live session updates |
| `leave_session` | Client → Server | Unsubscribe from a session |
| `request_telemetry` | Client → Server | Request driver lap telemetry |
| `request_comparison` | Client → Server | Request two-driver comparison |
| `positions_update` | Server → Client | Live positions + intervals (every 2s) |
| `full_update` | Server → Client | Laps, weather, stints, race control (~30s) |

---

## 🧠 Prediction Model

The race predictor uses a **3-layer stacking architecture**:

```
                    ┌──────────────┐
                    │  Ridge Meta  │ ← Stacking meta-learner
                    │   Learner    │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────┴─────┐ ┌─────┴─────┐ ┌─────┴─────┐
        │  XGBoost  │ │  LightGBM │ │  CatBoost │ ← Base models
        │ (Optuna)  │ │  (Optuna) │ │  (Optuna) │
        └────────────┘ └───────────┘ └───────────┘
              │            │            │
        ┌─────┴────────────┴────────────┴─────┐
        │        40+ Engineered Features       │
        │  Grid · Quali Gap · Form · Elo ·     │
        │  Circuit History · Momentum · DNF    │
        │  Rate · Pit Strategy · Home Bonus    │
        └─────────────────────────────────────┘
```

**Key design decisions:**
- **GroupKFold** by season prevents data leakage across time
- **Elo ratings** for drivers and constructors (all-time head-to-head performance)
- **Per-race softmax calibration**: predicted positions → win probabilities normalised within each race, with the temperature fitted by maximum likelihood on out-of-fold predictions
- **SHAP values** provide transparent, per-driver feature importance

---

## ⚙️ Configuration

| Environment Variable | Description | Default |
|---|---|---|
| `PORT` | Web server listening port | `8050` |
| `SECRET_KEY` | Flask session secret key | Ephemeral random key |
| `PREDICTOR_OPTUNA_TRIALS` | Number of Optuna tuning trials per base model | `20` |
| `OPENF1_ACCESS_TOKEN` | OpenF1 OAuth token for live timing | _(none)_ |
| `OPENF1_USERNAME` | OpenF1 username for auto token refresh | _(none)_ |
| `OPENF1_PASSWORD` | OpenF1 password for auto token refresh | _(none)_ |

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgements

- [Jolpica API](https://api.jolpi.ca) — Open Formula 1 data (Ergast successor)
- [OpenF1](https://openf1.org) — Real-time F1 data API
- [FastF1](https://docs.fastf1.dev) — Python library for F1 telemetry data
- [Formula 1 Live Timing](https://livetiming.formula1.com) — Official timing feeds
