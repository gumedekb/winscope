import sys
import json
import joblib
import numpy as np
import os

# Set working directory to the directory of this script
os.chdir(os.path.dirname(os.path.abspath(__file__)))

try:
    model      = joblib.load('soccer_model_v5.pkl')
    scaler     = joblib.load('soccer_scaler_v5.pkl')
    # FEATURES   = joblib.load('soccer_features_v5.pkl') # Not used in predict_match logic directly but available
    elo_scaler = joblib.load('soccer_elo_scaler_v5.pkl')
except Exception as e:
    print(json.dumps({"error": f"Model load error: {str(e)}"}))
    sys.exit(1)

def predict():
    try:
        input_data = json.loads(sys.stdin.read())
        
        competition = input_data.get('competition', 'PL')
        home_elo = input_data.get('home_elo', 1000)
        away_elo = input_data.get('away_elo', 1000)
        
        league_draw_rates = {
            'PL':0.224,'BL1':0.243,'SA':0.261,'PD':0.254,
            'FL1':0.258,'DED':0.272,'PPL':0.248
        }

        # Normalize Elo
        elos_norm = elo_scaler.transform([[home_elo, away_elo]])[0]
        home_elo_n, away_elo_n = elos_norm[0], elos_norm[1]
        elo_diff_n = home_elo_n - away_elo_n

        # Features from input
        season_stage = input_data.get('season_stage', 0.5)
        home_prev_pos = input_data.get('home_prev_pos', 0.75)
        away_prev_pos = input_data.get('away_prev_pos', 0.75)
        
        home_overall_form = input_data.get('home_overall_form', 0.5)
        home_venue_form = input_data.get('home_venue_form', 0.5)
        away_overall_form = input_data.get('away_overall_form', 0.5)
        away_venue_form = input_data.get('away_venue_form', 0.5)
        
        home_overall_goals_avg = input_data.get('home_overall_goals_avg', 1.5)
        home_overall_conc_avg = input_data.get('home_overall_conc_avg', 1.5)
        away_overall_goals_avg = input_data.get('away_overall_goals_avg', 1.5)
        away_overall_conc_avg = input_data.get('away_overall_conc_avg', 1.5)
        
        home_venue_goals_avg = input_data.get('home_venue_goals_avg', 1.5)
        home_venue_conc_avg = input_data.get('home_venue_conc_avg', 1.5)
        away_venue_goals_avg = input_data.get('away_venue_goals_avg', 1.5)
        away_venue_conc_avg = input_data.get('away_venue_conc_avg', 1.5)
        
        home_overall_goals_std = input_data.get('home_overall_goals_std', 1.0)
        away_overall_goals_std = input_data.get('away_overall_goals_std', 1.0)
        home_overall_draw_rate = input_data.get('home_overall_draw_rate', 0.25)
        away_overall_draw_rate = input_data.get('away_overall_draw_rate', 0.25)

        # Derived features
        form_venue_diff   = home_venue_form    - away_venue_form
        form_overall_diff = home_overall_form  - away_overall_form
        goals_diff        = home_overall_goals_avg - away_overall_goals_avg
        conceded_diff     = home_overall_conc_avg  - away_overall_conc_avg
        net_diff          = (home_overall_goals_avg - home_overall_conc_avg) - \
                            (away_overall_goals_avg - away_overall_conc_avg)
        draw_tendency     = (home_overall_draw_rate + away_overall_draw_rate) / 2
        strength_diff     = away_prev_pos - home_prev_pos

        row = np.array([[
            season_stage,
            home_prev_pos, away_prev_pos, strength_diff,
            home_overall_form, home_venue_form,
            away_overall_form, away_venue_form,
            home_overall_goals_avg, home_overall_conc_avg,
            away_overall_goals_avg, away_overall_conc_avg,
            home_venue_goals_avg,   home_venue_conc_avg,
            away_venue_goals_avg,   away_venue_conc_avg,
            home_overall_goals_std, away_overall_goals_std,
            home_overall_draw_rate, away_overall_draw_rate,
            form_venue_diff, form_overall_diff,
            goals_diff, conceded_diff, net_diff,
            draw_tendency, league_draw_rates.get(competition, 0.25),
            home_elo_n, away_elo_n, elo_diff_n,
        ]])

        row_sc = scaler.transform(row)
        proba  = model.predict_proba(row_sc)[0]

        print(json.dumps({
            'home_win': round(float(proba[0]), 4),
            'draw':     round(float(proba[1]), 4),
            'away_win': round(float(proba[2]), 4),
            'home_elo': round(float(home_elo), 1),
            'away_elo': round(float(away_elo), 1),
        }))
    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    predict()
