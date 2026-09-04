"use client";
import React, { useEffect, useMemo, useState } from 'react';
import { Check, History as HistoryIcon, Loader2, Minus, Target, X } from 'lucide-react';
import { api } from '../api-client';
import type { History, ScoredMatch } from '../types';

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

const outcomeLabel = (m: ScoredMatch, code: string) => {
  if (code === 'H') return m.home_team;
  if (code === 'A') return m.away_team;
  if (code === 'D') return 'Draw';
  return '—';
};

const probFor = (m: ScoredMatch, code: string): number | null => {
  if (!m.probs) return null;
  if (code === 'H') return m.probs.h;
  if (code === 'D') return m.probs.d;
  if (code === 'A') return m.probs.a;
  return null;
};

/**
 * Track record — finished matches joined to what was predicted before kickoff.
 * The join is possible because the ETL never deletes a finished fixture.
 */
export const HistoryDrawer: React.FC<Props> = ({ isOpen, onClose }) => {
  const [data, setData] = useState<History | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [onlyScored, setOnlyScored] = useState(false);
  const [range, setRange] = useState<{ from: string; to: string }>({ from: '', to: '' });

  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const res = await api.getHistory({
          from: range.from || undefined,
          to: range.to || undefined,
        });
        if (!cancelled) setData(res);
      } catch {
        if (!cancelled) setError('Could not load the track record.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [isOpen, range.from, range.to]);

  const summary = data?.summary;
  const accPct = summary?.accuracy != null ? Math.round(summary.accuracy * 100) : null;
  const confPct =
    summary?.avg_confidence_on_actual != null
      ? Math.round(summary.avg_confidence_on_actual * 100)
      : null;

  const rows = useMemo(() => {
    const list = data?.matches ?? [];
    return onlyScored ? list.filter((m) => m.hit !== null) : list;
  }, [data, onlyScored]);

  const stat = (value: string, label: string, tone = 'text-white') => (
    <div className="bg-[#2d2d2d] border border-[#404040] p-4 rounded-2xl text-center">
      <p className={`text-2xl font-black ${tone}`}>{value}</p>
      <p className="text-[10px] font-bold text-gray-500 uppercase tracking-wider mt-1">{label}</p>
    </div>
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
        aria-label="Track record"
      >
        <div className="p-6">
          <div className="flex justify-between items-center mb-8">
            <h2 className="text-xl font-black text-white flex items-center gap-2">
              <HistoryIcon className="w-5 h-5 text-[#FFD700]" />
              Track Record
            </h2>
            <button
              onClick={onClose}
              className="p-2 hover:bg-white/5 rounded-full text-gray-400"
              aria-label="Close"
            >
              <X className="w-6 h-6" />
            </button>
          </div>

          <div className="grid grid-cols-3 gap-3 mb-6">
            {stat(accPct != null ? `${accPct}%` : '—', 'Hit Rate', 'text-[#FFD700]')}
            {stat(summary ? `${summary.correct}/${summary.evaluated}` : '—', 'Correct')}
            {stat(confPct != null ? `${confPct}%` : '—', 'Avg Conf.')}
          </div>

          <div className="grid grid-cols-2 gap-3 mb-6">
            <label className="block">
              <span className="text-[10px] font-black text-gray-500 uppercase tracking-widest">From</span>
              <input
                type="date"
                value={range.from}
                onChange={(e) => setRange((r) => ({ ...r, from: e.target.value }))}
                className="mt-1 w-full bg-[#2d2d2d] border border-[#404040] rounded-lg px-2 py-1.5
                           text-xs text-white focus:border-[#FFD700] outline-none"
              />
            </label>
            <label className="block">
              <span className="text-[10px] font-black text-gray-500 uppercase tracking-widest">To</span>
              <input
                type="date"
                value={range.to}
                onChange={(e) => setRange((r) => ({ ...r, to: e.target.value }))}
                className="mt-1 w-full bg-[#2d2d2d] border border-[#404040] rounded-lg px-2 py-1.5
                           text-xs text-white focus:border-[#FFD700] outline-none"
              />
            </label>
          </div>

          {summary && summary.market_accuracy !== null && (
            <div className="mb-6 bg-[#2d2d2d] border border-[#404040] rounded-2xl p-4">
              <p className="text-[10px] font-black text-gray-500 uppercase tracking-widest mb-2">
                Model vs the bookmakers&apos; favourite ({summary.market_evaluated} matches)
              </p>
              <div className="flex justify-between items-baseline text-sm">
                <span className="text-gray-300">
                  Model{' '}
                  <span className="font-mono font-bold text-[#FFD700]">
                    {accPct != null ? `${accPct}%` : '—'}
                  </span>
                </span>
                <span className="text-gray-300">
                  Market{' '}
                  <span className="font-mono font-bold text-white">
                    {Math.round(summary.market_accuracy * 100)}%
                  </span>
                </span>
              </div>
              {accPct != null && (
                <p className="text-[10px] text-gray-500 mt-2">
                  {accPct > Math.round(summary.market_accuracy * 100)
                    ? 'Ahead of the market on this sample — the only bar that matters.'
                    : accPct === Math.round(summary.market_accuracy * 100)
                      ? 'Level with the market on this sample.'
                      : 'Behind the market on this sample.'}
                </p>
              )}
            </div>
          )}

          {(data?.per_league.length ?? 0) > 0 && (
            <div className="mb-6">
              <p className="text-[10px] font-black text-gray-500 uppercase tracking-widest mb-2">
                By league
              </p>
              <div className="space-y-1">
                {data?.per_league.map((l) => (
                  <div
                    key={l.league}
                    className="flex items-center justify-between gap-2 bg-[#2d2d2d] border border-[#404040]
                               rounded-lg px-3 py-2 text-[11px]"
                  >
                    <span className="text-gray-300 truncate flex-1">{l.league}</span>
                    <span className="text-gray-500 tabular-nums shrink-0">
                      {l.correct}/{l.evaluated}
                    </span>
                    <span className="font-bold text-[#FFD700] tabular-nums w-10 text-right shrink-0">
                      {l.accuracy != null ? `${Math.round(l.accuracy * 100)}%` : '—'}
                    </span>
                    {l.market_accuracy != null && (
                      <span
                        className="text-gray-600 tabular-nums w-10 text-right shrink-0"
                        title="Bookmakers' favourite on the same matches"
                      >
                        {Math.round(l.market_accuracy * 100)}%
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {summary?.benchmark && (
            <div className="mb-6 bg-[#2d2d2d] border border-[#404040] rounded-2xl p-4">
              <p className="text-[10px] font-black text-gray-500 uppercase tracking-widest mb-2">
                Model vs Market — log-loss, lower is better ({summary.benchmark.matches} matches)
              </p>
              <div className="flex justify-between text-sm">
                <span className="text-gray-300">
                  Model{' '}
                  <span className="font-mono font-bold text-[#FFD700]">
                    {summary.benchmark.model_log_loss.toFixed(3)}
                  </span>
                </span>
                <span className="text-gray-300">
                  Market{' '}
                  <span className="font-mono font-bold text-white">
                    {summary.benchmark.market_log_loss.toFixed(3)}
                  </span>
                </span>
              </div>
            </div>
          )}

          {(data?.matches.length ?? 0) > 0 && (
            <label className="flex items-center gap-2 mb-4 text-xs text-gray-400 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={onlyScored}
                onChange={(e) => setOnlyScored(e.target.checked)}
                className="accent-[#FFD700]"
              />
              Only matches we predicted
            </label>
          )}

          {loading ? (
            <div className="flex flex-col items-center justify-center py-24 text-gray-500">
              <Loader2 className="w-10 h-10 animate-spin mb-4" />
              <p className="font-bold">Loading results…</p>
            </div>
          ) : error ? (
            <div className="text-center py-20 text-gray-500">{error}</div>
          ) : rows.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center text-gray-500">
              <Target className="w-12 h-12 mb-4 opacity-50" />
              <p className="font-bold text-gray-300">
                {data?.matches.length ? 'No predicted matches yet' : 'No finished matches yet'}
              </p>
              <p className="text-xs mt-2 max-w-xs leading-relaxed">
                Finished matches are kept forever in Turso. Once a fixture you had a prediction for
                ends, it appears here with the call scored against the real result.
              </p>
            </div>
          ) : (
            <div className="space-y-2 pb-10">
              <h3 className="text-xs font-black text-gray-500 uppercase tracking-widest mb-3">
                Predicted vs Actual ({rows.length})
              </h3>
              {rows.map((m) => {
                const predProb = probFor(m, m.predicted_outcome);
                const scored = m.home_score !== null && m.away_score !== null;
                return (
                  <div key={m.match_key} className="bg-[#2d2d2d] border border-[#404040] p-3 rounded-xl">
                    <div className="flex items-center justify-between gap-2 mb-1">
                      <span className="text-[9px] font-bold text-gray-500 uppercase truncate">
                        {m.league} ·{' '}
                        {new Date(m.kickoff_utc).toLocaleDateString('en-GB', {
                          day: 'numeric', month: 'short',
                        })}
                      </span>
                      {m.hit === null ? (
                        <span className="flex items-center gap-1 text-[10px] font-bold text-gray-500 shrink-0">
                          <Minus className="w-3 h-3" /> NOT PREDICTED
                        </span>
                      ) : m.hit ? (
                        <span className="flex items-center gap-1 text-[10px] font-bold text-[#00A651] shrink-0">
                          <Check className="w-3 h-3" /> HIT
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 text-[10px] font-bold text-[#E30613] shrink-0">
                          <X className="w-3 h-3" /> MISS
                        </span>
                      )}
                    </div>

                    <div className="flex items-center justify-between gap-2 mb-2">
                      <span className="text-xs font-bold text-white truncate flex-1">
                        {m.home_team} <span className="text-gray-600">v</span> {m.away_team}
                      </span>
                      {scored && (
                        <span className="text-sm font-black text-white tabular-nums shrink-0">
                          {m.home_score}–{m.away_score}
                        </span>
                      )}
                    </div>

                    <div className="flex items-center justify-between text-[11px] gap-2">
                      <div className="flex-1 min-w-0 truncate">
                        <span className="text-gray-500">Predicted: </span>
                        <span className="font-bold text-gray-300">
                          {outcomeLabel(m, m.predicted_outcome)}
                        </span>
                        {predProb != null && (
                          <span className="text-gray-600"> ({Math.round(predProb * 100)}%)</span>
                        )}
                      </div>
                      <div className="flex-1 min-w-0 text-right truncate">
                        <span className="text-gray-500">Actual: </span>
                        <span className={`font-bold ${m.hit ? 'text-[#00A651]' : 'text-orange-400'}`}>
                          {outcomeLabel(m, m.actual_outcome)}
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </>
  );
};
