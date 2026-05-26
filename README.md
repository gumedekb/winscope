

```md
# ✦ WinScope: AI-Driven Football Analysis

WinScope is a football prediction platform that uses Machine Learning to predict match outcomes (Home Win, Draw, Away Win). It combines historical data, Elo ratings, and recent form to generate insights for analysts and bettors.

---

## 🚀 Overview

WinScope uses a full-stack architecture:

- **Frontend:** React (Vite + Tailwind CSS)
- **Backend:** Node.js (Express + TypeScript)
- **ML Engine:** Python (scikit-learn models)
- **Database:** SQLite (separated for historical + upcoming matches)

---

## 🧠 Architecture

```

React Frontend → Node.js API → Python ML Model → Prediction Response

```

---

## 🏗️ System Components

### Frontend
- React SPA built with Vite
- Tailwind CSS for styling
- Displays matches, predictions, and analytics

### Backend
Node.js Express server handling:
- API routing
- Data syncing
- ML model execution bridge

### ML Engine
Python-based prediction system:
- Uses trained `.pkl` models
- Accepts match features via stdin
- Returns probability outputs

---

## 📁 Backend Structure

```

backend/
├── data/
│   ├── football_master_v2.sqlite
│   └── upcoming_matches.sqlite
│
├── model/
│   ├── predict.py
│   ├── soccer_model_v5.pkl
│   └── soccer_scaler_v5.pkl
│
├── src/
│   ├── index.ts
│   ├── config.ts
│   ├── api/client.ts
│   ├── db/
│   │   ├── repository.ts
│   │   └── schema.ts
│   ├── services/
│   │   ├── eloService.ts
│   │   └── predictionService.ts
│   └── scripts/export_ml.ts
│
└── venv/

```

---

## 🎨 Frontend Structure

```

frontend/
├── public/
├── src/
│   ├── App.tsx
│   ├── main.tsx
│   ├── components/
│   │   ├── MatchCard.tsx
│   │   └── PredictionBar.tsx
│   ├── pages/
│   │   ├── Dashboard.tsx
│   │   └── MatchDetail.tsx
│   ├── services/api.ts
│   └── types/index.ts
└── tailwind.config.js

````

---

## 🔄 Key Workflows

### 1. Data Sync
1. User clicks “Sync Results”
2. Backend fetches latest completed matches
3. Database is updated with final scores

---

### 2. Prediction Flow
1. Frontend sends match data
2. Backend extracts features:
   - Elo rating
   - Form (W/D/L)
   - Goals stats
3. Python ML model runs prediction
4. Returns probabilities:
```json
{
  "home_win": 0.60,
  "draw": 0.25,
  "away_win": 0.15
}
````

5. Frontend displays results visually

---

## ⚡ Performance Notes

* Predictions are **batched (3 at a time)** to prevent CPU spikes
* Full TypeScript used across backend + frontend
* Separate databases for performance isolation
* Python model runs as a child process from Node.js

---

## 🧩 Tech Stack

* React + Vite
* Tailwind CSS
* Node.js + Express
* Python (scikit-learn)
* SQLite
* TypeScript

---

## 🧠 Key Features

* Football match predictions using ML
* Elo-based team strength system
* Head-to-head analysis
* Real-time match syncing
* Performance-optimized prediction batching

---

## 📌 Production Readiness

* Clean modular architecture
* Removed legacy ingestion scripts
* Fully typed backend (TypeScript)
* Optimized frontend batching system
* Stable ML pipeline integration

---

## 📈 Summary

WinScope is a full-stack AI football analytics system combining machine learning, real-time data processing, and a modern web dashboard.

```

