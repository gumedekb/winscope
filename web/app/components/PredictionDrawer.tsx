"use client";
import React, { useEffect, useState } from 'react';
import { Sparkles, TrendingUp, X } from 'lucide-react';
import type { Match, RecentMatch } from '../types';
import { PredictionBar } from './PredictionBar';
import { AiInsightPanel } from './AiInsightPanel';
import { TeamCrest } from './TeamCrest';

interface Props {
  match: Match | null;
  isOpen: boolean;
  onClose: () => void;
}

type Tab = 'prediction' | 'ai';

const RecentList: React.FC<{ title: string; rows?: RecentMatch[] }> = ({ title, rows }) => (
  <div>
    <p className="text-[10px] font-bold text-gray-500 uppercase mb-2">{title}</p>
    <div className="space-y-1">
      {rows && rows.length > 0 ? (
        rows.slice(0, 5).map((m, i) => (
          <div key={i} className="flex justify-between items-center gap-2 text-[10px] bg-[#2d2d2d] border border-[#404040] p-2 rounded">
            <span className="text-gray-400 truncate flex-1">
              {m.home_team} v {m.away_team}
            </span>
            <span className="font-bold text-[#FFD700] px-1 tabular-nums shrink-0">
              {m.home_score}-{m.away_score}
            </span>
            <span className="text-[8px] text-gray-600 shrink-0">
              {new Date(m.match_date).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}
            </span>
          </div>
        ))
      ) : (
        <p className="text-[10px] text-gray-600 italic">
          No results stored yet — they fill in as the ETL collects finished matches.
        </p>
      )}
    </div>
  </div>
);

export const PredictionDrawer: React.FC<Props> = ({ match, isOpen, onClose }) => {
  const [tab, setTab] = useState<Tab>('prediction');

  // Reset to the cheap tab whenever a different match is opened, so the AI panel
  // is never showing for a match the user did not choose it for.
  useEffect(() => { setTab('prediction'); }, [match?.id]);

  if (!match) return null;
  const { prediction } = match;
  const isLive = match.statusGroup === 'in_play';
  const hasScore = match.homeScore !== null && match.awayScore !== null;

  const tabButton = (id: Tab, label: string, Icon: typeof TrendingUp) => (
    <button
      onClick={() => setTab(id)}
      aria-pressed={tab === id}
      className={`flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-bold transition-colors
        ${tab === id ? 'bg-[#FFD700] text-black' : 'text-gray-400 hover:text-white'}`}
    >
      <Icon className="w-3.5 h-3.5" />
      {label}
    </button>
  );

  return (
    <>
      <div
        className={`fixed inset-0 bg-black/60 backdrop-blur-sm z-40 transition-opacity duration-300
                    ${isOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}
        onClick={onClose}
      />
      <div
        className={`fixed right-0 top-0 h-full w-full max-w-md bg-[#1a1a1a] z-50 shadow-2xl
                    transform transition-transform duration-300 ease-in-out border-l border-[#404040]
                    overflow-y-auto ${isOpen ? 'translate-x-0' : 'translate-x-full'}`}
        role="dialog"
        aria-label="Match detail"
      >
        <div className="p-6">
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-lg font-black text-white">Match Detail</h2>
            <button onClick={onClose} className="p-2 hover:bg-white/5 rounded-full text-gray-400" aria-label="Close">
              <X className="w-6 h-6" />
            </button>
          </div>

          {/* Header: competition, kickoff, and the live score if there is one */}
          <div className="mb-5 bg-[#2d2d2d] border border-[#404040] p-4 rounded-2xl">
            <div className="flex items-center justify-between mb-3 text-[10px] uppercase font-bold tracking-wider">
              <span className="text-gray-400 truncate">{match.competition}</span>
              {isLive ? (
                <span className="flex items-center gap-1.5 text-[#00A651]">
                  <span className="live-dot" />
                  Live{match.minute ? ` · ${match.minute}'` : ''}
                </span>
              ) : (
                <span className="text-[#FFD700]">
                  {new Date(match.utcDate).toLocaleString('en-ZA', {
                    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
                    timeZone: 'Africa/Johannesburg',
                  })}
                </span>
              )}
            </div>
            <div className="flex justify-between items-center">
              <div className="flex flex-col items-center flex-1 min-w-0">
                <TeamCrest name={match.homeTeam?.name} crest={match.homeCrest}
                           size={48} className="mb-2" />
                <span className="text-xs font-bold text-white text-center px-1">{match.homeTeam?.name}</span>
              </div>
              {hasScore ? (
                <span className={`text-2xl font-black px-3 tabular-nums shrink-0 ${isLive ? 'text-[#00A651]' : 'text-white'}`}>
                  {match.homeScore}<span className="text-gray-600 mx-1">-</span>{match.awayScore}
                </span>
              ) : (
                <span className="text-lg font-black text-gray-600 italic px-4 shrink-0">VS</span>
              )}
              <div className="flex flex-col items-center flex-1 min-w-0">
                <TeamCrest name={match.awayTeam?.name} crest={match.awayCrest}
                           size={48} className="mb-2" />
                <span className="text-xs font-bold text-white text-center px-1">{match.awayTeam?.name}</span>
              </div>
            </div>
            {match.venue && (
              <p className="text-[10px] text-gray-500 text-center mt-3 truncate">{match.venue}</p>
            )}
          </div>

          <div className="flex gap-1 p-1 bg-[#2d2d2d] border border-[#404040] rounded-xl mb-6">
            {tabButton('prediction', 'Prediction', TrendingUp)}
            {tabButton('ai', 'AI Insight', Sparkles)}
          </div>

          {tab === 'prediction' ? (
            <div className="space-y-8 pb-10">
              <section>
                <h3 className="text-xs font-black text-gray-500 uppercase tracking-widest mb-3">
                  {prediction?.market_probs ? 'Model + market blend' : 'Model probabilities'}
                </h3>
                {prediction ? (
                  <>
                    <PredictionBar h={prediction.home_win} d={prediction.draw} a={prediction.away_win} />
                    {prediction.market_probs && (
                      <div className="mt-3 text-[10px] text-gray-500 bg-[#2d2d2d] border border-[#404040] rounded-lg p-2">
                        <span className="font-bold text-gray-400">Market consensus</span>
                        <span className="text-gray-600"> ({prediction.market_probs.books} bookmakers)</span>
                        <span className="float-right font-mono">
                          {Math.round(prediction.market_probs.home * 100)}% /{' '}
                          {Math.round(prediction.market_probs.draw * 100)}% /{' '}
                          {Math.round(prediction.market_probs.away * 100)}%
                        </span>
                      </div>
                    )}
                  </>
                ) : (
                  <p className="text-xs text-gray-500 italic">
                    No prediction yet — the model server is not reachable.
                  </p>
                )}
              </section>

              <section className="space-y-4">
                <h3 className="text-xs font-black text-gray-500 uppercase tracking-widest">
                  Recent performance (last 5)
                </h3>
                <RecentList title={`${match.homeTeam?.name} recent results`} rows={prediction?.home_team_history} />
                <RecentList title={`${match.awayTeam?.name} recent results`} rows={prediction?.away_team_history} />
              </section>
            </div>
          ) : (
            <div className="pb-10">
              <AiInsightPanel matchKey={match.matchKey ?? match.id} active={isOpen && tab === 'ai'} />
            </div>
          )}
        </div>
      </div>
    </>
  );
};
