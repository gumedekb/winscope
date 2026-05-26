# Project Context

## Goal
Football betting prediction platform with AI-driven match analysis.

## Tech Stack
- **Frontend**: React (TypeScript) + Vite + Tailwind CSS + Lucide Icons.
- **Backend**: Node.js (TypeScript) + Express + better-sqlite3.
- **ML Engine**: Scikit-learn (Python 3.13) integrated via `child_process` bridge.
- **Databases**: 
    - `football_master_v2.sqlite`: Main database for historical match data, team statistics, and Elo ratings.
    - `upcoming_matches.sqlite`: Dedicated database for the next 3 days of upcoming fixtures.
- **Data Providers**: 
    - `football-data.org`
    - `thesportsdb.com`
    - `api-sports.io` (API-Football)

## Current Phase
Phase 5: Multi-API Integration & League Expansion [IN PROGRESS]

## Status
- [x] **Phase 1: Data Ingestion**: Imported historical data for 10 leagues (PL, BL1, SA, PD, FL1, DED, PPL, RSA, etc.) across seasons 2022-2025.
- [x] **Phase 2: ML Pipeline**: Trained `v5` model with expanded league support and venue-aware features.
- [x] **Phase 3: Backend Core**: 
    - Migrated Elo rating logic from Python to TypeScript.
    - Implemented `predictionService` to communicate with the Python scikit-learn model.
- [x] **Phase 4: Frontend & Features**:
    - Built interactive dashboard showing upcoming matches.
    - Integrated AI predictions directly into match cards.
- [x] **Phase 5: Multi-API Strategy**:
    - **Historical Fallback**: `Football-Data` -> `TheSportsDB` -> `API-Sports`.
    - **Upcoming Strategy**: `API-Sports` -> `Football-Data`.
    - **Separate DB**: Upcoming matches stored in `upcoming_matches.sqlite` to ensure isolation.
- [x] **Phase 6: Maintenance & Sync**:
    - Added UI-driven historical result synchronization.
    - Optimized dashboard loading with prediction batching.
    - Cleaned up redundant legacy scripts and databases.

## API Endpoints (Express)
- `GET /api/competitions`: List all monitored leagues.
- `GET /api/matches`: Query matches by competition, season, or status.
- `GET /api/matches/upcoming`: Fetch and store upcoming 3-day fixtures using the priority strategy.
- `GET /api/matches/sync-results`: Update the main database with latest finished match results.
- `POST /api/predict`: Returns AI outcome probabilities for any match.

## Major Changes (April 2026)
1.  Migrated to `football_master_v2.sqlite`.
2.  Added **Liga Portugal**, **Bundesliga**, and **Betway Premiership** (South Africa).
3.  Implemented multi-provider API fallback logic for higher reliability.
4.  Created dedicated `upcoming_matches.sqlite` for temporary fixture storage.
5.  Refined UI: Replaced text branding with WinScope logo and fixed match form ordering (newest first).
6.  Upgraded to **AI Engine v5.0** (updated scikit-learn model and scalers).
7.  Automated League Sync: Upcoming matches now fetch fixtures for all leagues defined in the database.
8.  Performance Optimization: Implemented prediction batching (3 at a time) to mitigate CPU spikes during dashboard refresh.
9.  **Historical Sync**: Added "Sync Latest Results" feature to keep the main database up-to-date with finished games.
10. **Codebase Cleanup**: Removed 15+ unused legacy scripts and 4 redundant databases to achieve production-ready state.
