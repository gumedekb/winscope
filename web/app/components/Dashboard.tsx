"use client";
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import type { BetslipRow, Freshness, Match, Prediction } from '../types';
import { api } from '../api-client';
import { MatchCard } from './MatchCard';
import { PredictionDrawer } from './PredictionDrawer';
import { HistoryDrawer } from './HistoryDrawer';
import { SplashScreen } from './SplashScreen';
import { LeagueFilter } from './LeagueFilter';
import { StatusBar } from './StatusBar';
import { BetslipDrawer } from './BetslipDrawer';
import { SearchBar, matchesQuery } from './SearchBar';
import { Calendar, ClipboardList, Loader2, PanelRightOpen, Radio, Trophy } from 'lucide-react';
import { isLiveNow } from '../../lib/liveness';

/** Live scores go stale fast; upcoming fixtures do not. */
const LIVE_POLL_MS = 60_000;

export const Dashboard: React.FC = () => {
  const [matches, setMatches] = useState<Match[]>([]);
  const [freshness, setFreshness] = useState<Freshness | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [showSplash, setShowSplash] = useState(true);

  const [league, setLeague] = useState('all');
  const [liveOnly, setLiveOnly] = useState(false);
  const [slipOnly, setSlipOnly] = useState(false);
  const [query, setQuery] = useState('');

  const [isBetslipOpen, setIsBetslipOpen] = useState(false);
  const [slipKeys, setSlipKeys] = useState<Set<string>>(new Set());
  const [slipVersion, setSlipVersion] = useState(0);
  const [slipBusy, setSlipBusy] = useState<string | null>(null);

  const [cardRefreshing, setCardRefreshing] = useState<Record<string, boolean>>({});
  const [selectedMatch, setSelectedMatch] = useState<Match | null>(null);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  // Detail (form + AI read) is loaded at most once per match per session.
  const [loadedDetails, setLoadedDetails] = useState<Set<string>>(new Set());

  const fetchData = useCallback(async (quiet = false) => {
    try {
      if (!quiet) setLoading(true);
      const data = await api.getFixtures();
      setMatches(data.matches ?? []);
      setFreshness(data.freshness ?? null);
      setError(data.error ?? null);
    } catch (err) {
      console.error('Failed to load fixtures:', err);
      setError(err instanceof Error ? err.message : 'Failed to load fixtures');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void fetchData(); }, [fetchData]);

  // The slip is loaded once up front so the +/✓ state is right on first paint.
  const syncSlipKeys = useCallback((rows: BetslipRow[]) => {
    setSlipKeys(new Set(rows.map((r) => r.match_key)));
  }, []);
  useEffect(() => {
    (async () => {
      try {
        const slip = await api.getBetslip();
        syncSlipKeys(slip.rows);
      } catch {
        /* an unreachable slip must not block the fixture list */
      }
    })();
  }, [syncSlipKeys]);

  const toggleSlip = async (match: Match) => {
    const key = match.matchKey ?? match.id;
    const pick = match.prediction?.outcome === 'D' ? 2 : match.prediction?.outcome === 'A' ? 3 : 1;
    try {
      setSlipBusy(key);
      const res = slipKeys.has(key)
        ? await api.removeFromBetslip(key)
        : await api.addToBetslip(match, pick);
      syncSlipKeys(res.rows);
      setSlipVersion((v) => v + 1);
    } catch (err) {
      console.error('Betslip update failed:', err);
    } finally {
      setSlipBusy(null);
    }
  };

  // Poll only while something is actually in play — no point hammering
  // the database to re-read a fixture list that changes twice a day.
  // Staleness matters here: a row stuck at `in_play` because the ETL missed a
  // run would otherwise keep the 60s poll going forever, re-reading a value
  // that cannot change until the next cron tick.
  const liveCount = useMemo(() => matches.filter((m) => isLiveNow(m)).length, [matches]);
  useEffect(() => {
    if (liveCount === 0) return;
    const id = setInterval(() => void fetchData(true), LIVE_POLL_MS);
    return () => clearInterval(id);
  }, [liveCount, fetchData]);

  const handleRefresh = async () => {
    setRefreshing(true);
    await fetchData(true);
  };

  const applyPrediction = (matchKey: string, pred: Prediction) => {
    setMatches((prev) =>
      prev.map((m) => (m.id === matchKey ? { ...m, prediction: { ...m.prediction, ...pred } } : m))
    );
    setSelectedMatch((prev) =>
      prev?.id === matchKey ? { ...prev, prediction: { ...prev.prediction, ...pred } } : prev
    );
    setLoadedDetails((prev) => new Set(prev).add(matchKey));
  };

  const loadDetail = async (match: Match, force: boolean) => {
    try {
      setCardRefreshing((p) => ({ ...p, [match.id]: true }));
      const pred = await api.getPrediction(match, force);
      applyPrediction(match.id, pred);
    } catch (err) {
      console.error('Detail load failed:', err);
    } finally {
      setCardRefreshing((p) => ({ ...p, [match.id]: false }));
    }
  };

  const handleCardClick = async (match: Match) => {
    setSelectedMatch(match);
    setIsDrawerOpen(true);
    if (loadedDetails.has(match.id) || cardRefreshing[match.id]) return;
    await loadDetail(match, false);
  };

  // An empty slip disables the toggle, so clear it first — otherwise the filter
  // stays on with nothing to show and no enabled control to switch it off.
  useEffect(() => {
    if (slipKeys.size === 0) setSlipOnly(false);
  }, [slipKeys]);

  const leagues = useMemo(
    () => Array.from(new Set(matches.map((m) => m.competition))).sort(),
    [matches]
  );
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const m of matches) c[m.competition] = (c[m.competition] ?? 0) + 1;
    return c;
  }, [matches]);

  const visible = useMemo(() => {
    let list = matches;
    if (league !== 'all') list = list.filter((m) => m.competition === league);
    if (liveOnly) list = list.filter((m) => isLiveNow(m));
    if (slipOnly) list = list.filter((m) => slipKeys.has(m.matchKey ?? m.id));
    if (query.trim()) {
      list = list.filter((m) =>
        matchesQuery(
          `${m.homeTeam?.name ?? ''} ${m.awayTeam?.name ?? ''} ${m.competition} ${m.country ?? ''}`,
          query
        )
      );
    }
    return list;
  }, [matches, league, liveOnly, slipOnly, slipKeys, query]);

  // Live matches sit in their own group at the top; the rest group by date.
  const { live, byDate } = useMemo(() => {
    // Stalled rows drop out of the live group and back into their date group,
    // where their kickoff time makes it obvious they are not happening now.
    const liveList = visible.filter((m) => isLiveNow(m));
    const rest = visible.filter((m) => !isLiveNow(m));
    const groups: Record<string, Match[]> = {};
    for (const m of rest) {
      const label = new Date(m.utcDate).toLocaleDateString('en-GB', {
        weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
      });
      (groups[label] ??= []).push(m);
    }
    return { live: liveList, byDate: groups };
  }, [visible]);

  if (showSplash) {
    return <SplashScreen isLoading={loading} onFinished={() => setShowSplash(false)} />;
  }

  const renderGrid = (list: Match[]) => (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
      {list.map((match) => (
        <MatchCard
          key={match.id}
          match={match}
          prediction={match.prediction}
          onClick={() => void handleCardClick(match)}
          onRefresh={() => void loadDetail(match, true)}
          refreshing={cardRefreshing[match.id]}
          onSlip={slipKeys.has(match.matchKey ?? match.id)}
          onToggleSlip={() => void toggleSlip(match)}
          slipBusy={slipBusy === (match.matchKey ?? match.id)}
        />
      ))}
    </div>
  );

  return (
    <div className="min-h-screen text-white w-full">
      <div className="max-w-6xl w-full mx-auto px-4 py-8">
        <header className="mb-8 text-center">
          <p className="text-[10px] md:text-xs font-black text-[#FFD700] uppercase tracking-[0.4em] mb-4 opacity-80">
            Bet with Confidence
          </p>
          <div className="text-5xl md:text-6xl font-black text-[#FFD700] tracking-tight mb-3">
            Win<span className="text-white">Scope</span>
          </div>
          <h1 className="text-base md:text-lg font-black text-[#FFD700] uppercase tracking-[0.1em]">
            Better predictions with the help of AI
          </h1>

          <div className="flex flex-wrap items-center justify-center gap-3 mt-6">
            <button
              onClick={() => setLiveOnly((v) => !v)}
              aria-pressed={liveOnly}
              disabled={liveCount === 0}
              className={`font-bold py-2 px-5 rounded-full flex items-center gap-2 transition-all text-sm border
                ${liveOnly
                  ? 'bg-[#00A651] text-white border-[#00A651]'
                  : 'bg-[#2d2d2d] text-gray-300 border-[#404040] hover:border-[#00A651]'}
                ${liveCount === 0 ? 'opacity-40 cursor-not-allowed' : ''}`}
            >
              <Radio className="w-4 h-4" />
              Live{liveCount > 0 ? ` (${liveCount})` : ''}
            </button>
            <div className="flex items-center">
              <button
                onClick={() => setSlipOnly((v) => !v)}
                aria-pressed={slipOnly}
                disabled={slipKeys.size === 0}
                title={slipKeys.size === 0 ? 'Add matches to the slip first' : 'Show only matches on the slip'}
                className={`font-bold py-2 pl-5 pr-4 rounded-l-full flex items-center gap-2 transition-all text-sm border
                  ${slipOnly
                    ? 'bg-[#FFD700] text-black border-[#FFD700]'
                    : 'bg-[#2d2d2d] text-gray-300 border-[#404040] hover:border-[#FFD700]'}
                  ${slipKeys.size === 0 ? 'opacity-40 cursor-not-allowed' : ''}`}
              >
                <ClipboardList className="w-4 h-4" />
                Betslip
                {slipKeys.size > 0 && (
                  <span className="bg-black/20 text-[10px] font-black px-1.5 py-0.5 rounded-full tabular-nums">
                    {slipKeys.size}
                  </span>
                )}
              </button>
              <button
                onClick={() => setIsBetslipOpen(true)}
                title="Open the slip sheet"
                aria-label="Open the slip sheet"
                className={`py-2 px-3 rounded-r-full border border-l-0 transition-all
                  ${slipOnly
                    ? 'bg-[#FFD700] text-black border-[#FFD700] hover:bg-[#e6c200]'
                    : 'bg-[#2d2d2d] text-gray-300 border-[#404040] hover:border-[#FFD700]'}`}
              >
                <PanelRightOpen className="w-4 h-4" />
              </button>
            </div>
            <button
              onClick={() => setIsHistoryOpen(true)}
              className="btn-glow font-bold py-2 px-5 rounded-full flex items-center gap-2 transition-all text-sm"
            >
              <Trophy className="w-4 h-4" />
              Track Record
            </button>
          </div>
        </header>

        <StatusBar
          freshness={freshness}
          liveCount={liveCount}
          onRefresh={() => void handleRefresh()}
          refreshing={refreshing}
          error={error}
        />

        {matches.length > 0 && (
          <div className="mb-4">
            <SearchBar
              value={query}
              onChange={setQuery}
              resultCount={visible.length}
              totalCount={matches.length}
            />
          </div>
        )}

        {leagues.length > 1 && (
          <div className="mb-8">
            <LeagueFilter leagues={leagues} active={league} counts={counts} onChange={setLeague} />
          </div>
        )}

        {loading ? (
          <div className="flex flex-col items-center justify-center py-40 space-y-4">
            <Loader2 className="w-12 h-12 text-[#FFD700] animate-spin" />
            <p className="text-gray-500 font-bold animate-pulse uppercase tracking-[0.2em]">
              Loading matches…
            </p>
          </div>
        ) : visible.length === 0 ? (
          <div className="text-center py-20 bg-[#2d2d2d] rounded-3xl border border-[#404040] px-6">
            <Calendar className="w-16 h-16 text-gray-500 mx-auto mb-4" />
            <h2 className="text-2xl font-bold text-gray-200">
              {matches.length === 0 ? 'No matches in the database' : 'Nothing matches this filter'}
            </h2>
            {matches.length === 0 ? (
              <p className="text-gray-400 mt-3 text-sm max-w-md mx-auto">
                Fixtures are published by the ETL, not fetched here. Run{' '}
                <code className="text-[#FFD700]">cd data &amp;&amp; python pipeline.py --live</code>{' '}
                to populate Turso, then reload.
              </p>
            ) : (
              <button
                onClick={() => { setLeague('all'); setLiveOnly(false); setSlipOnly(false); setQuery(''); }}
                className="text-[#FFD700] mt-3 text-sm font-bold hover:underline"
              >
                Clear filters
              </button>
            )}
          </div>
        ) : (
          <>
            {live.length > 0 && (
              <section className="mb-10">
                <h2 className="text-lg font-black text-white mb-4 border-l-4 border-[#00A651] pl-4 flex items-center gap-2">
                  <span className="live-dot" />
                  Live now
                  <span className="text-xs font-bold text-gray-500">({live.length})</span>
                </h2>
                {renderGrid(live)}
              </section>
            )}

            {Object.entries(byDate).map(([date, dayMatches]) => (
              <section key={date} className="mb-10">
                <h2 className="text-lg font-black text-white mb-4 border-l-4 border-[#FFD700] pl-4 flex items-center gap-2">
                  <Calendar className="w-5 h-5 text-[#FFD700]" />
                  {date}
                  <span className="text-xs font-bold text-gray-500">({dayMatches.length})</span>
                </h2>
                {renderGrid(dayMatches)}
              </section>
            ))}
          </>
        )}

        <PredictionDrawer
          match={selectedMatch}
          isOpen={isDrawerOpen}
          onClose={() => setIsDrawerOpen(false)}
        />
        <BetslipDrawer
          isOpen={isBetslipOpen}
          onClose={() => setIsBetslipOpen(false)}
          version={slipVersion}
          onChanged={syncSlipKeys}
        />
        <HistoryDrawer isOpen={isHistoryOpen} onClose={() => setIsHistoryOpen(false)} />
      </div>
    </div>
  );
};
