"use client";
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Check, ChevronDown, ChevronUp, ClipboardList, Layers, Loader2, Trash2, X,
} from 'lucide-react';
import type { BetslipResponse, BetslipRow } from '../types';
import { api } from '../api-client';
import { coveredBy, labelFor, permAdvice, permPair, pickCode, slipMaths, type Code } from '../../lib/perm';

const STAKE_KEY = 'winscope.perm.stake';
const rowProbs = (row: BetslipRow) => ({ home: row.prob_home, draw: row.prob_draw, away: row.prob_away });

interface Props {
  isOpen: boolean;
  onClose: () => void;
  /** Bumped by the dashboard whenever a card is added/removed elsewhere. */
  version: number;
  onChanged: (rows: BetslipRow[]) => void;
}

const PICK_LABEL = (row: BetslipRow): string =>
  row.pick === 1 ? row.home_team : row.pick === 3 ? row.away_team : 'Draw';

const PICK_CODE: Record<number, string> = { 1: '1', 2: 'X', 3: '2' };

const pickProb = (row: BetslipRow): number =>
  row.pick === 1 ? row.prob_home : row.pick === 2 ? row.prob_draw : row.prob_away;

/**
 * The day's slip: what you are actually betting, in order, with the model's
 * pick beside each one so the drawer doubles as the sheet you copy onto Betway.
 */
export const BetslipDrawer: React.FC<Props> = ({ isOpen, onClose, version, onChanged }) => {
  const [data, setData] = useState<BetslipResponse | null>(null);
  const [date, setDate] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Perm builder: which matches are covered two ways. Defaults to the advice;
  // a tap overrides it for this session. Stake per line is remembered locally.
  const [permOverride, setPermOverride] = useState<Record<string, boolean>>({});
  const [stake, setStake] = useState<number>(() => {
    try { return Number(localStorage.getItem(STAKE_KEY)) || 1; } catch { return 1; }
  });
  useEffect(() => { try { localStorage.setItem(STAKE_KEY, String(stake)); } catch { /* per-viewer nicety only */ } }, [stake]);

  const load = useCallback(async (forDate?: string) => {
    try {
      setLoading(true);
      setError(null);
      const res = await api.getBetslip(forDate);
      setData(res);
      setDate(res.date);
      onChanged(res.rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the betslip.');
    } finally {
      setLoading(false);
    }
  }, [onChanged]);

  useEffect(() => {
    if (isOpen) void load(date);
    // `version` re-loads when a card elsewhere changed the slip.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, version]);

  const mutate = async (fn: () => Promise<BetslipResponse>, key: string) => {
    try {
      setBusy(key);
      const res = await fn();
      setData((prev) => (prev ? { ...prev, rows: res.rows } : prev));
      onChanged(res.rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That did not work.');
    } finally {
      setBusy(null);
    }
  };

  const rows = data?.rows ?? [];
  const settled = rows.filter((r) => r.hit !== null);
  const correct = settled.filter((r) => r.hit).length;

  // One advice per row; the override flips perm on/off but keeps the pick.
  const plan = useMemo(() => rows.map((row) => {
    const probs = rowProbs(row);
    const advice = permAdvice(probs, row.pick);
    const perm = permOverride[row.match_key] ?? advice.perm;
    const selections: Code[] = perm ? permPair(probs, row.pick) : [pickCode(row.pick)];
    return { row, advice, perm, selections, covered: coveredBy(probs, selections) };
  }), [rows, permOverride]);
  const open = plan.filter((p) => p.row.hit === null);
  const maths = slipMaths(open.map((p) => ({ selections: p.selections, covered: p.covered })), stake);

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
        aria-label="Betslip"
      >
        <div className="p-6">
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-xl font-black text-white flex items-center gap-2">
              <ClipboardList className="w-5 h-5 text-[#FFD700]" />
              Betslip
            </h2>
            <button onClick={onClose} className="p-2 hover:bg-white/5 rounded-full text-gray-400" aria-label="Close">
              <X className="w-6 h-6" />
            </button>
          </div>

          {(data?.dates.length ?? 0) > 0 && (
            <label className="block mb-5">
              <span className="text-[10px] font-black text-gray-500 uppercase tracking-widest">Matchday</span>
              <select
                value={date ?? ''}
                onChange={(e) => { setDate(e.target.value); void load(e.target.value); }}
                className="mt-1 w-full bg-[#2d2d2d] border border-[#404040] rounded-lg px-3 py-2
                           text-sm text-white focus:border-[#FFD700] outline-none"
              >
                {!data?.dates.includes(date ?? '') && date && <option value={date}>{date} (today)</option>}
                {data?.dates.map((d) => (
                  <option key={d} value={d}>{d}</option>
                ))}
              </select>
            </label>
          )}

          {settled.length > 0 && (
            <div className="mb-5 bg-[#2d2d2d] border border-[#404040] rounded-2xl p-4 text-center">
              <p className="text-2xl font-black text-[#FFD700]">
                {correct}/{settled.length}
              </p>
              <p className="text-[10px] font-bold text-gray-500 uppercase tracking-wider mt-1">
                Correct so far{rows.length > settled.length ? ` · ${rows.length - settled.length} still to play` : ''}
              </p>
            </div>
          )}

          {open.length > 0 && (
            <div className="mb-5 bg-[#2d2d2d] border border-[#404040] rounded-2xl p-4">
              <div className="flex items-center justify-between mb-3">
                <p className="text-[10px] font-black text-gray-500 uppercase tracking-widest flex items-center gap-1.5">
                  <Layers className="w-3.5 h-3.5 text-[#FFD700]" /> Perm builder
                </p>
                <label className="flex items-center gap-1.5 text-[10px] text-gray-500">
                  R/line
                  <input
                    type="number" min={0} step={0.5} value={stake}
                    onChange={(e) => setStake(Number(e.target.value))}
                    className="w-14 bg-[#1a1a1a] border border-[#404040] rounded px-1.5 py-0.5 text-right
                               text-xs text-white focus:border-[#FFD700] outline-none tabular-nums"
                  />
                </label>
              </div>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div>
                  <p className="text-lg font-black text-white tabular-nums">{maths.lines}</p>
                  <p className="text-[9px] font-bold text-gray-500 uppercase">{maths.lines === 1 ? 'line' : 'lines'}</p>
                </div>
                <div>
                  <p className="text-lg font-black text-[#FFD700] tabular-nums">R{maths.cost.toFixed(maths.cost % 1 ? 2 : 0)}</p>
                  <p className="text-[9px] font-bold text-gray-500 uppercase">stake</p>
                </div>
                <div>
                  <p className="text-lg font-black text-white tabular-nums">{(maths.chance * 100).toFixed(maths.chance < 0.1 ? 1 : 0)}%</p>
                  <p className="text-[9px] font-bold text-gray-500 uppercase">all {open.length} land</p>
                </div>
              </div>
              <p className="text-[10px] text-gray-500 mt-3 leading-relaxed">
                {maths.permed > 0
                  ? `${maths.permed} match${maths.permed === 1 ? '' : 'es'} covered two ways — the model rarely calls a draw outright, so tight games and live draws get a second selection. Tap a pick to change it.`
                  : 'Every match is a single. Tap a pick to cover it two ways.'}
              </p>
            </div>
          )}

          {error && <p className="text-[11px] text-[#E30613] mb-4">{error}</p>}

          {loading && !data ? (
            <div className="flex flex-col items-center justify-center py-24 text-gray-500">
              <Loader2 className="w-10 h-10 animate-spin mb-4" />
              <p className="font-bold">Loading slip…</p>
            </div>
          ) : rows.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center text-gray-500">
              <ClipboardList className="w-12 h-12 mb-4 opacity-50" />
              <p className="font-bold text-gray-300">Nothing on this slip yet</p>
              <p className="text-xs mt-2 max-w-xs leading-relaxed">
                Tap <span className="text-[#FFD700] font-bold">+</span> on any match card to add it.
                The pick and probabilities are saved as they are at that moment, so the slip stays
                intact after kick-off.
              </p>
            </div>
          ) : (
            <ol className="space-y-2 pb-10">
              {plan.map(({ row, advice, perm, selections, covered }, i) => {
                const key = row.match_key;
                const isBusy = busy === key;
                const upcoming = row.hit === null;
                return (
                  <li key={key} className="bg-[#2d2d2d] border border-[#404040] rounded-xl p-3">
                    <div className="flex items-start gap-2">
                      <span className="text-[10px] font-black text-gray-600 tabular-nums mt-0.5 w-4 shrink-0">
                        {i + 1}
                      </span>

                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[9px] font-bold text-gray-500 uppercase truncate">
                            {row.league}
                          </span>
                          {row.hit === null ? (
                            <span className="text-[9px] font-bold text-gray-600 shrink-0">
                              {new Date(row.kickoff_utc).toLocaleString('en-ZA', {
                                day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
                                timeZone: 'Africa/Johannesburg',
                              })}
                            </span>
                          ) : row.hit ? (
                            <span className="flex items-center gap-1 text-[9px] font-bold text-[#00A651] shrink-0">
                              <Check className="w-3 h-3" /> HIT
                            </span>
                          ) : (
                            <span className="flex items-center gap-1 text-[9px] font-bold text-[#E30613] shrink-0">
                              <X className="w-3 h-3" /> MISS
                            </span>
                          )}
                        </div>

                        <p className="text-xs font-bold text-white truncate mt-0.5">
                          {row.home_team} <span className="text-gray-600">v</span> {row.away_team}
                          {row.home_score !== null && (
                            <span className="ml-2 tabular-nums text-gray-300">
                              {row.home_score}–{row.away_score}
                            </span>
                          )}
                        </p>

                        {upcoming ? (
                          <button
                            onClick={() => setPermOverride((o) => ({ ...o, [key]: !perm }))}
                            title={perm ? 'Back to a single pick' : advice.reason ? `Perm this: ${advice.reason}` : 'Cover two outcomes'}
                            className={`mt-1 text-[11px] text-left rounded-md px-1.5 py-0.5 -ml-1.5 transition-colors
                              ${perm ? 'bg-[#FFD700]/10 hover:bg-[#FFD700]/20' : 'hover:bg-white/5'}`}
                          >
                            <span className="text-gray-500">{perm ? 'Perm: ' : 'Pick: '}</span>
                            <span className="font-black text-[#FFD700]">{selections.join('')}</span>
                            <span className="text-gray-300">
                              {' '}{selections.map((c) => labelFor(c, row.home_team, row.away_team)).join(' / ')}
                            </span>
                            <span className="text-gray-600"> ({Math.round(covered * 100)}%)</span>
                            {!perm && advice.perm && (
                              <span className="ml-1.5 text-[9px] font-bold text-orange-400 uppercase">perm? {advice.reason}</span>
                            )}
                          </button>
                        ) : (
                          <p className="text-[11px] mt-1">
                            <span className="text-gray-500">Pick: </span>
                            <span className="font-black text-[#FFD700]">{PICK_CODE[row.pick]}</span>
                            <span className="text-gray-300"> {PICK_LABEL(row)}</span>
                            <span className="text-gray-600"> ({Math.round(pickProb(row) * 100)}%)</span>
                          </p>
                        )}
                      </div>

                      <div className="flex flex-col gap-0.5 shrink-0">
                        <button
                          onClick={() => void mutate(() => api.moveInBetslip(key, 'up', date), key)}
                          disabled={i === 0 || isBusy}
                          className="p-1 rounded text-gray-500 hover:text-[#FFD700] disabled:opacity-25"
                          aria-label="Move up"
                        >
                          <ChevronUp className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={() => void mutate(() => api.moveInBetslip(key, 'down', date), key)}
                          disabled={i === rows.length - 1 || isBusy}
                          className="p-1 rounded text-gray-500 hover:text-[#FFD700] disabled:opacity-25"
                          aria-label="Move down"
                        >
                          <ChevronDown className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={() => void mutate(() => api.removeFromBetslip(key, date), key)}
                          disabled={isBusy}
                          className="p-1 rounded text-gray-500 hover:text-[#E30613] disabled:opacity-25"
                          aria-label="Remove"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      </div>
    </>
  );
};
