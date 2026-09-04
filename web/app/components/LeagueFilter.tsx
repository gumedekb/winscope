"use client";
import React from 'react';

interface Props {
  leagues: string[];
  active: string;
  counts: Record<string, number>;
  onChange: (league: string) => void;
}

/**
 * League chips. Eleven leagues and a couple of hundred fixtures is far too much
 * to scroll, and the Betway Tote is usually built from one or two competitions.
 */
export const LeagueFilter: React.FC<Props> = ({ leagues, active, counts, onChange }) => {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const chip = (key: string, label: string, n: number) => {
    const on = active === key;
    return (
      <button
        key={key}
        onClick={() => onChange(key)}
        aria-pressed={on}
        className={`px-3 py-1.5 rounded-full text-xs font-bold whitespace-nowrap transition-all border
          ${on
            ? 'bg-[#FFD700] text-black border-[#FFD700]'
            : 'bg-[#2d2d2d] text-gray-300 border-[#404040] hover:border-[#FFD700] hover:text-[#FFD700]'}`}
      >
        {label}
        <span className={`ml-1.5 text-[10px] ${on ? 'text-black/60' : 'text-gray-500'}`}>{n}</span>
      </button>
    );
  };

  return (
    <div className="flex gap-2 overflow-x-auto pb-2 -mx-1 px-1 scrollbar-thin" role="group" aria-label="Filter by league">
      {chip('all', 'All leagues', total)}
      {leagues.map((l) => chip(l, l, counts[l] ?? 0))}
    </div>
  );
};
