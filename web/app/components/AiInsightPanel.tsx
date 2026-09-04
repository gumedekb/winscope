"use client";
import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle, Gauge, Loader2, RefreshCw, ShieldCheck, Sparkles, TrendingUp, TriangleAlert,
} from 'lucide-react';
import type { AiInsight, BetTier, TierName } from '../types';
import { api } from '../api-client';

interface Props {
  matchKey: string;
  /** Only fetch the cache once the panel is actually visible. */
  active: boolean;
}

const TIER_STYLE: Record<TierName, { ring: string; text: string; chip: string; Icon: typeof ShieldCheck }> = {
  low:    { ring: 'border-[#00A651]', text: 'text-[#00A651]', chip: 'bg-[#00A651]', Icon: ShieldCheck },
  medium: { ring: 'border-[#FFD700]', text: 'text-[#FFD700]', chip: 'bg-[#FFD700] text-black', Icon: Gauge },
  high:   { ring: 'border-[#E30613]', text: 'text-[#E30613]', chip: 'bg-[#E30613]', Icon: AlertTriangle },
};

const pct = (n: number) => `${Math.round(n * 100)}%`;

const TierCard: React.FC<{ tier: BetTier }> = ({ tier }) => {
  const style = TIER_STYLE[tier.tier];
  const { Icon } = style;
  return (
    <div className={`rounded-xl border ${style.ring} bg-[#2d2d2d] p-3`}>
      <div className="flex items-center justify-between gap-2 mb-2">
        <span className={`flex items-center gap-1.5 text-[10px] font-black uppercase tracking-wider ${style.text}`}>
          <Icon className="w-3.5 h-3.5" />
          {tier.label}
        </span>
        <span className={`text-[10px] font-black px-2 py-0.5 rounded ${style.chip} ${tier.tier === 'medium' ? '' : 'text-white'}`}>
          {tier.market}
        </span>
      </div>

      <p className="text-sm font-bold text-white leading-tight mb-2">{tier.selection}</p>

      <div className="flex items-center gap-3 text-[10px] font-bold text-gray-400 mb-2 tabular-nums">
        <span>
          Model <span className="text-white">{pct(tier.probability)}</span>
        </span>
        <span>
          Fair odds <span className="text-white">{tier.impliedOdds.toFixed(2)}</span>
        </span>
        {tier.edge !== null && (
          <span title="Model probability minus bookmaker probability">
            Edge{' '}
            <span className={tier.edge >= 0 ? 'text-[#00A651]' : 'text-[#E30613]'}>
              {tier.edge >= 0 ? '+' : ''}
              {Math.round(tier.edge * 100)}pp
            </span>
          </span>
        )}
      </div>

      {tier.rationale && (
        <p className="text-[11px] text-gray-300 leading-relaxed">{tier.rationale}</p>
      )}
    </div>
  );
};

/**
 * AI insight, behind an explicit button.
 *
 * Nothing here runs on its own. On open it reads the CACHE only (a 204 means
 * "never generated"); a provider is called just once, when the user asks, and
 * again only if they press Refresh. Both providers are free tiers, so a panel
 * that generated for every match on the board would exhaust the day's calls in
 * one sitting.
 */
export const AiInsightPanel: React.FC<Props> = ({ matchKey, active }) => {
  const [insight, setInsight] = useState<AiInsight | null>(null);
  const [checking, setChecking] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Cache lookup only — never triggers a provider call.
  useEffect(() => {
    if (!active || !matchKey) return;
    let cancelled = false;
    (async () => {
      try {
        setChecking(true);
        setError(null);
        const cached = await api.getCachedInsight(matchKey);
        if (!cancelled) setInsight(cached);
      } catch {
        /* absence of a cached insight is not an error */
      } finally {
        if (!cancelled) setChecking(false);
      }
    })();
    return () => { cancelled = true; };
  }, [matchKey, active]);

  const generate = useCallback(
    async (refresh: boolean) => {
      try {
        setGenerating(true);
        setError(null);
        const res = await api.generateInsight(matchKey, refresh);
        setInsight(res);
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Could not generate the insight.');
      } finally {
        setGenerating(false);
      }
    },
    [matchKey]
  );

  if (checking) {
    return (
      <div className="flex items-center gap-2 text-xs text-gray-500 py-6 justify-center">
        <Loader2 className="w-4 h-4 animate-spin" />
        Checking for a saved insight…
      </div>
    );
  }

  // Nothing cached yet — one button, one call.
  if (!insight) {
    return (
      <div className="rounded-2xl border border-[#404040] bg-[#2d2d2d] p-5 text-center">
        <Sparkles className="w-7 h-7 text-[#FFD700] mx-auto mb-3" />
        <p className="text-sm font-bold text-white mb-1">AI insight for this match</p>
        <p className="text-[11px] text-gray-400 mb-4 leading-relaxed max-w-xs mx-auto">
          Explains the model&apos;s call and suggests three bets by risk level. Generated once and
          saved — opening this match again is free.
        </p>
        <button
          onClick={() => void generate(false)}
          disabled={generating}
          className="btn-glow font-bold py-2 px-6 rounded-full inline-flex items-center gap-2 text-sm disabled:opacity-60"
        >
          {generating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
          {generating ? 'Analysing…' : 'Get AI insight'}
        </button>
        {error && (
          <p className="mt-4 text-[11px] text-[#E30613] flex items-start gap-1.5 text-left">
            <TriangleAlert className="w-3.5 h-3.5 shrink-0 mt-px" />
            <span className="break-words">{error}</span>
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-xs font-black text-[#FFD700] uppercase tracking-widest flex items-center gap-2">
            <Sparkles className="w-4 h-4" />
            AI Insight
          </h3>
          <p className="text-[9px] text-gray-500 mt-1 truncate">
            {insight.provider} · {insight.model}
            {insight.refresh_count > 0 && ` · refreshed ${insight.refresh_count}×`}
            {insight.updated_at && ` · ${new Date(insight.updated_at).toLocaleDateString('en-GB')}`}
          </p>
        </div>
        <button
          onClick={() => void generate(true)}
          disabled={generating}
          className="shrink-0 flex items-center gap-1.5 text-[10px] font-bold text-gray-400
                     hover:text-[#FFD700] transition-colors disabled:opacity-50"
          title="Regenerate — this spends one AI call"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${generating ? 'animate-spin' : ''}`} />
          {generating ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {error && (
        <p className="text-[11px] text-[#E30613] flex items-start gap-1.5">
          <TriangleAlert className="w-3.5 h-3.5 shrink-0 mt-px" />
          <span className="break-words">{error}</span>
        </p>
      )}

      <p className="text-sm text-gray-200 leading-relaxed">{insight.summary}</p>

      {insight.tiers.length > 0 && (
        <div>
          <h4 className="text-[10px] font-black text-gray-500 uppercase tracking-widest mb-2 flex items-center gap-1.5">
            <TrendingUp className="w-3.5 h-3.5" />
            Suggested bets by risk
          </h4>
          <div className="space-y-2">
            {insight.tiers.map((t) => (
              <TierCard key={t.tier} tier={t} />
            ))}
          </div>
          <p className="text-[9px] text-gray-600 mt-2 leading-relaxed">
            Probabilities and odds come from the model, not the AI — it only writes the reasoning.
          </p>
        </div>
      )}

      {insight.key_factors.length > 0 && (
        <div>
          <h4 className="text-[10px] font-black text-gray-500 uppercase tracking-widest mb-2">
            Key factors
          </h4>
          <ul className="space-y-1.5">
            {insight.key_factors.map((f, i) => (
              <li key={i} className="text-[11px] text-gray-300 flex gap-2 leading-relaxed">
                <span className="text-[#FFD700] font-bold shrink-0">•</span>
                <span>{f}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {insight.model_commentary && (
        <div className="rounded-xl bg-[#2d2d2d] border border-[#404040] p-3">
          <h4 className="text-[10px] font-black text-gray-500 uppercase tracking-widest mb-1.5">
            On the model
          </h4>
          <p className="text-[11px] text-gray-300 leading-relaxed">{insight.model_commentary}</p>
        </div>
      )}

      {insight.confidence_notes && (
        <div className="rounded-xl bg-[#2d2d2d] border border-[#404040] p-3">
          <h4 className="text-[10px] font-black text-gray-500 uppercase tracking-widest mb-1.5">
            How much to trust it
          </h4>
          <p className="text-[11px] text-gray-300 leading-relaxed">{insight.confidence_notes}</p>
        </div>
      )}
    </div>
  );
};
