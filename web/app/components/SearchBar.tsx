"use client";
import React from 'react';
import { Search, X } from 'lucide-react';

interface Props {
  value: string;
  onChange: (v: string) => void;
  resultCount: number;
  totalCount: number;
}

/**
 * Live filter over the fixture cards.
 *
 * Forgiving on purpose: Betway's team names will not match our sources exactly,
 * so matching folds accents and punctuation and matches on any word. Typing
 * "utd", "koln" or "man" should find the card.
 */
export const SearchBar: React.FC<Props> = ({ value, onChange, resultCount, totalCount }) => (
  <div className="relative">
    <Search className="w-4 h-4 text-gray-500 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
    <input
      type="search"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder="Search team or league…"
      aria-label="Search fixtures by team or league"
      className="w-full bg-[#2d2d2d] border border-[#404040] rounded-full pl-10 pr-24 py-2.5
                 text-sm text-white placeholder:text-gray-500
                 focus:border-[#FFD700] focus:outline-none transition-colors"
    />
    {value && (
      <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-2">
        <span className="text-[10px] font-bold text-gray-500 tabular-nums">
          {resultCount}/{totalCount}
        </span>
        <button
          onClick={() => onChange('')}
          className="p-1 rounded-full text-gray-500 hover:text-white hover:bg-[#404040]"
          aria-label="Clear search"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
    )}
  </div>
);

/** Fold accents/punctuation so "koln" matches "1. FC Köln". */
export const normalizeForSearch = (s: string): string =>
  s
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

/** Every query word must appear somewhere in the haystack. */
export const matchesQuery = (haystack: string, query: string): boolean => {
  const words = normalizeForSearch(query).split(' ').filter(Boolean);
  if (words.length === 0) return true;
  const hay = normalizeForSearch(haystack);
  return words.every((w) => hay.includes(w));
};
