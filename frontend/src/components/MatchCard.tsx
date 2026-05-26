import React from 'react';
import { Match, Prediction } from '../types';
import { PredictionBar } from './PredictionBar';
import { api } from '../services/api';
import { TrendingUp, User } from 'lucide-react';

interface Props {
  match: Match;
  prediction?: Prediction;
}

export const MatchCard: React.FC<Props> = ({ match, prediction }) => {
  const date = new Date(match.utcDate);
  const timeStr = date.toLocaleTimeString('en-ZA', { 
    hour: '2-digit', 
    minute: '2-digit',
    timeZone: 'Africa/Johannesburg'
  });

  const renderForm = (form?: string[]) => {
    if (!form || form.length === 0) return null;
    return (
      <div className="flex gap-1 mt-1">
        {form.map((result, i) => (
          <span 
            key={i} 
            className={`w-4 h-4 rounded-sm flex items-center justify-center text-[9px] font-black text-white ${
              result === 'W' ? 'bg-green-500' : result === 'D' ? 'bg-gray-500' : 'bg-red-500'
            }`}
          >
            {result}
          </span>
        ))}
      </div>
    );
  };

  return (
    <div 
      className="glass-morphism rounded-xl p-4 cursor-pointer hover:border-accent/50 transition-all duration-300 group"
    >
      <div className="flex justify-between items-center mb-3">
        <span className="text-[10px] font-bold text-accent uppercase tracking-wider">{match.status}</span>
        <span className="text-xs font-semibold text-gray-400">{timeStr}</span>
      </div>

      <div className="flex items-center justify-between gap-4 mb-4">
        {/* Home Team */}
        <div className="flex flex-col items-center flex-1 text-center">
          <img 
            src={api.getTeamCrestUrl(match.homeTeamId)} 
            alt="home crest" 
            className="w-12 h-12 object-contain mb-2 group-hover:scale-110 transition-transform" 
            onError={(e) => (e.currentTarget.src = 'https://via.placeholder.com/48?text=Team')}
          />
          <span className="text-sm font-bold text-white leading-tight h-10 flex items-center">{match.homeTeam?.shortName || match.homeTeamId}</span>
          {renderForm(prediction?.home_form)}
        </div>

        <div className="flex flex-col items-center">
            <span className="text-xl font-black text-gray-600 italic">VS</span>
        </div>

        {/* Away Team */}
        <div className="flex flex-col items-center flex-1 text-center">
          <img 
            src={api.getTeamCrestUrl(match.awayTeamId)} 
            alt="away crest" 
            className="w-12 h-12 object-contain mb-2 group-hover:scale-110 transition-transform" 
            onError={(e) => (e.currentTarget.src = 'https://via.placeholder.com/48?text=Team')}
          />
          <span className="text-sm font-bold text-white leading-tight h-10 flex items-center">{match.awayTeam?.shortName || match.awayTeamId}</span>
          {renderForm(prediction?.away_form)}
        </div>
      </div>

      {prediction && (
        <div className="pt-3 border-t border-white/5">
          <div className="flex justify-between items-center mb-1">
            <span className="text-[10px] font-bold text-gray-500 uppercase flex items-center gap-1">
              <TrendingUp className="w-3 h-3 text-accent" />
              AI Prediction
            </span>
            <span className="text-[10px] font-black text-white bg-accent px-2 py-0.5 rounded uppercase">
              {prediction.outcome === 'H' ? 'Home' : prediction.outcome === 'D' ? 'Draw' : 'Away'}
            </span>
          </div>
          <PredictionBar 
            h={prediction.home_win} 
            d={prediction.draw} 
            a={prediction.away_win} 
          />
        </div>
      )}
    </div>
  );
};
