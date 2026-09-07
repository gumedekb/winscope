"use client";
import React from 'react';
import { Database, Radio, RefreshCw, TriangleAlert } from 'lucide-react';
import type { Freshness } from '../types';

interface Props {
  freshness: Freshness | null;
  onRefresh: () => void;
  refreshing: boolean;
  error?: string | null;
  /**
   * Live matches as the cards actually count them — in play AND recently
   * confirmed. `freshness.inPlay` is the raw database tally and includes rows
   * frozen by a missed ETL run, so showing it here would contradict the grid.
   */
  liveCount?: number;
}

const ago = (iso: string | null) => {
  if (!iso) return 'never';
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  return hrs < 48 ? `${hrs}h ago` : `${Math.round(hrs / 24)}d ago`;
};

/**
 * Where the data came from and how old it is.
 *
 * Worth showing prominently: this app never fetches fixtures itself — the ETL
 * does, on a schedule — so "why is this match missing?" is nearly always
 * "the ETL has not run recently", and the answer should be on screen.
 */
export const StatusBar: React.FC<Props> = ({
  freshness, onRefresh, refreshing, error, liveCount,
}) => {
  const stale = freshness?.lastUpdated
    ? Date.now() - new Date(freshness.lastUpdated).getTime() > 12 * 3600_000
    : true;

  if (error) {
    return (
      <div className="rounded-xl border border-[#E30613] bg-[#E30613]/10 px-4 py-3 mb-6 flex items-start gap-3">
        <TriangleAlert className="w-4 h-4 text-[#E30613] mt-0.5 shrink-0" />
        <div className="text-sm">
          <p className="font-bold text-[#E30613]">Cannot read the fixtures database</p>
          <p className="text-gray-300 text-xs mt-1">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-[#404040] bg-[#2d2d2d]/60 px-4 py-3 mb-6
                    flex flex-wrap items-center gap-x-5 gap-y-2 text-xs">
      <span className="flex items-center gap-1.5 text-gray-400">
        <Database className="w-3.5 h-3.5 text-[#FFD700]" />
        Turso
      </span>

      {liveCount ? (
        <span className="flex items-center gap-1.5 font-bold text-[#00A651]">
          <Radio className="w-3.5 h-3.5" />
          {liveCount} live
        </span>
      ) : null}

      {freshness?.stranded ? (
        <span
          className="flex items-center gap-1.5 font-bold text-[#FFD700]"
          title={'Matches whose kickoff has passed but which the ETL never advanced — '
               + 'either it is behind, or no live source covers that league.'}
        >
          <TriangleAlert className="w-3.5 h-3.5" />
          {freshness.stranded} awaiting update
        </span>
      ) : null}

      <span className="text-gray-300">
        <strong className="text-white">{freshness?.scheduled ?? 0}</strong> upcoming
      </span>
      <span className="text-gray-300">
        <strong className="text-white">{freshness?.finished ?? 0}</strong> finished
      </span>
      <span className="text-gray-300">
        <strong className="text-white">{freshness?.leagues.length ?? 0}</strong> leagues
      </span>

      <span className={stale ? 'text-[#E30613] font-bold' : 'text-gray-400'}>
        updated {ago(freshness?.lastUpdated ?? null)}
      </span>

      <button
        onClick={onRefresh}
        disabled={refreshing}
        className="ml-auto flex items-center gap-1.5 text-gray-300 hover:text-[#FFD700] transition-colors font-bold"
      >
        <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
        Reload
      </button>

      {stale && (
        <p className="w-full text-[11px] text-gray-500 border-t border-[#404040] pt-2 mt-1">
          Fixtures are collected by the ETL, not by this app (it caps every provider at 90%
          of its free tier). To pull new matches run{' '}
          <code className="text-[#FFD700]">cd data &amp;&amp; python pipeline.py --live</code>.
        </p>
      )}
    </div>
  );
};
