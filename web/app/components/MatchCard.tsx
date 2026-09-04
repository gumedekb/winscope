"use client";
import React from 'react';
import type { Match, Prediction } from '../types';
import { PredictionBar } from './PredictionBar';
import { TeamCrest } from './TeamCrest';
import { Check, Plus, RefreshCw, TrendingUp } from 'lucide-react';

interface Props {
  match: Match;
  prediction?: Prediction | null;
  onClick?: () => void;
  onRefresh?: (e: React.MouseEvent) => void;
  refreshing?: boolean;
  /** On today's betslip already? Drives the +/✓ toggle. */
  onSlip?: boolean;
  onToggleSlip?: () => void;
  slipBusy?: boolean;
}

const FORM_STYLE: Record<string, string> = {
  W: 'bg-[#00A651] text-white',
  D: 'bg-[#6b7280] text-white',
  L: 'bg-[#E30613] text-white',
};

/** W/D/L strip, oldest on the left so it reads as a run. */
const FormStrip: React.FC<{ form?: ('W' | 'D' | 'L')[] }> = ({ form }) => {
  if (!form?.length) return <div className="h-4 mt-1" />;
  return (
    <div className="flex gap-1 mt-1" title="Last 5, most recent on the right">
      {[...form].reverse().map((r, i) => (
        <span
          key={i}
          className={`w-4 h-4 rounded-sm flex items-center justify-center text-[9px] font-black ${FORM_STYLE[r]}`}
        >
          {r}
        </span>
      ))}
    </div>
  );
};

export const MatchCard: React.FC<Props> = ({
  match, prediction, onClick, onRefresh, refreshing, onSlip, onToggleSlip, slipBusy,
}) => {
  const kickoff = new Date(match.utcDate);
  const timeStr = kickoff.toLocaleTimeString('en-ZA', {
    hour: '2-digit', minute: '2-digit', timeZone: 'Africa/Johannesburg',
  });

  const isLive = match.statusGroup === 'in_play';
  const isOff = match.statusGroup === 'off';
  const hasScore = match.homeScore !== null && match.awayScore !== null;

  return (
    <div
      onClick={onClick}
      className={`card-glow rounded-xl p-4 cursor-pointer transition-all duration-300 group relative
                  bg-[#2d2d2d] border ${isLive ? 'border-[#00A651]' : 'border-[#404040]'}`}
    >
      {isLive && <span className="live-ring" aria-hidden />}

      <div className="absolute top-3 right-3 flex items-center gap-1.5 z-10">
        {onToggleSlip && (
          <button
            onClick={(e) => { e.stopPropagation(); onToggleSlip(); }}
            disabled={slipBusy || !prediction}
            className={`p-1.5 rounded-full transition-colors disabled:opacity-40
              ${onSlip
                ? 'bg-[#00A651] text-white'
                : 'bg-[#404040] text-gray-300 hover:bg-[#505050] hover:text-[#FFD700]'}`}
            title={
              !prediction
                ? 'Needs a model prediction before it can go on the slip'
                : onSlip ? 'Remove from today\u2019s betslip' : 'Add to today\u2019s betslip'
            }
            aria-label={onSlip ? 'Remove from betslip' : 'Add to betslip'}
            aria-pressed={onSlip}
          >
            {onSlip ? <Check className="w-3.5 h-3.5" /> : <Plus className="w-3.5 h-3.5" />}
          </button>
        )}
        <button
          onClick={(e) => { e.stopPropagation(); onRefresh?.(e); }}
          disabled={refreshing}
          className="p-1.5 rounded-full bg-[#404040] hover:bg-[#505050]
                     text-gray-300 hover:text-[#FFD700] transition-colors"
          title="Refresh the model prediction"
          aria-label="Refresh prediction"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin text-[#FFD700]' : ''}`} />
        </button>
      </div>

      <div className="flex justify-between items-start mb-3 pr-16">
        <div className="flex flex-col min-w-0">
          {isLive ? (
            <span className="text-[10px] font-black text-[#00A651] uppercase tracking-wider flex items-center gap-1.5">
              <span className="live-dot" />
              LIVE{match.minute ? ` · ${match.minute}'` : ''}
            </span>
          ) : isOff ? (
            <span className="text-[10px] font-bold text-[#E30613] uppercase tracking-wider">
              {match.status}
            </span>
          ) : (
            <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">
              {kickoff.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })}
            </span>
          )}
          <span className="text-[9px] font-medium text-gray-400 uppercase truncate max-w-[150px]">
            {match.competition}
          </span>
        </div>
        <span className={`text-xs font-semibold ${isLive ? 'text-[#00A651]' : 'text-[#FFD700]'}`}>
          {timeStr}
        </span>
      </div>

      <div className="flex items-center justify-between gap-3 mb-4">
        <div className="flex flex-col items-center flex-1 text-center min-w-0">
          <TeamCrest
            name={match.homeTeam?.name}
            crest={match.homeCrest}
            size={48}
            className="mb-2 group-hover:scale-110 transition-transform"
          />
          <span className="text-sm font-bold text-white leading-tight h-10 flex items-center px-1">
            {match.homeTeam?.name}
          </span>
          <FormStrip form={match.homeForm ?? prediction?.home_form} />
        </div>

        {/* Live and finished matches show the score; upcoming ones show VS. */}
        <div className="flex flex-col items-center shrink-0 px-1">
          {hasScore ? (
            <span className={`text-2xl font-black tabular-nums ${isLive ? 'text-[#00A651]' : 'text-white'}`}>
              {match.homeScore}<span className="text-gray-500 mx-1">-</span>{match.awayScore}
            </span>
          ) : (
            <span className="text-xl font-black text-gray-500 italic">VS</span>
          )}
        </div>

        <div className="flex flex-col items-center flex-1 text-center min-w-0">
          <TeamCrest
            name={match.awayTeam?.name}
            crest={match.awayCrest}
            size={48}
            className="mb-2 group-hover:scale-110 transition-transform"
          />
          <span className="text-sm font-bold text-white leading-tight h-10 flex items-center px-1">
            {match.awayTeam?.name}
          </span>
          <FormStrip form={match.awayForm ?? prediction?.away_form} />
        </div>
      </div>

      {prediction ? (
        <div className="pt-3 border-t border-[#404040]">
          <div className="flex justify-between items-center mb-1">
            <span className="text-[10px] font-bold text-gray-400 uppercase flex items-center gap-1">
              <TrendingUp className="w-3 h-3 text-[#00A651]" />
              Prediction
              {prediction.market_probs ? (
                <span className="text-[8px] text-gray-500 normal-case">
                  · blended with {prediction.market_probs.books} books
                </span>
              ) : null}
            </span>
            <span className="text-[10px] font-black text-white bg-[#00A651] px-2 py-0.5 rounded uppercase">
              {prediction.outcome === 'H' ? 'Home' : prediction.outcome === 'D' ? 'Draw' : 'Away'}
            </span>
          </div>
          <PredictionBar h={prediction.home_win} d={prediction.draw} a={prediction.away_win} />
        </div>
      ) : (
        <div className="pt-3 border-t border-[#404040]">
          <p className="text-[10px] text-gray-500 uppercase tracking-wider text-center py-1">
            No prediction — model server offline
          </p>
        </div>
      )}
    </div>
  );
};
