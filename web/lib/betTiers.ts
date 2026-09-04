/**
 * The three suggested bets — computed here, in code, from the model's own
 * probabilities. The AI only writes the *rationale* for them.
 *
 * That split is deliberate: an LLM asked to invent probabilities will happily
 * produce confident nonsense, and a betting suggestion is exactly where that
 * does damage. So every number a user sees (probability, implied odds, edge)
 * comes from the model; the language model gets those numbers as input and is
 * asked only to explain them.
 */

export type TierName = 'low' | 'medium' | 'high';
/** 1 = home, X = draw, 2 = away; 1X / X2 / 12 are the double chances. */
export type MarketCode = '1' | 'X' | '2' | '1X' | 'X2' | '12';

export interface Probs {
  home: number;
  draw: number;
  away: number;
}

export interface BetTier {
  tier: TierName;
  label: string;
  market: MarketCode;
  /** Human selection, e.g. "Arsenal or Draw". */
  selection: string;
  probability: number;
  /** Fair odds implied by our probability — compare against the bookmaker's. */
  impliedOdds: number;
  /** model − market on this selection, when a market snapshot exists. */
  edge: number | null;
  rationale?: string;
}

const pct = (p: number) => Math.round(p * 100);

export function marketProbability(market: MarketCode, p: Probs): number {
  switch (market) {
    case '1':  return p.home;
    case 'X':  return p.draw;
    case '2':  return p.away;
    case '1X': return p.home + p.draw;
    case 'X2': return p.draw + p.away;
    case '12': return p.home + p.away;
  }
}

export function selectionText(market: MarketCode, home: string, away: string): string {
  switch (market) {
    case '1':  return `${home} to win`;
    case 'X':  return 'Draw';
    case '2':  return `${away} to win`;
    case '1X': return `${home} or Draw`;
    case 'X2': return `Draw or ${away}`;
    case '12': return `${home} or ${away} (no draw)`;
  }
}

/**
 * Pick one selection per risk tier.
 *
 *   low    — the safest double chance: two of the three outcomes, so it wins
 *            most of the time and pays little.
 *   medium — the model's outright call.
 *   high   — the value play. With market odds, that is the selection where the
 *            model most disagrees with the bookmakers (biggest positive edge);
 *            without them, the second-favourite outright.
 */
export function computeTiers(
  probs: Probs,
  homeTeam: string,
  awayTeam: string,
  market?: Probs | null
): BetTier[] {
  const edgeOf = (m: MarketCode): number | null =>
    market ? marketProbability(m, probs) - marketProbability(m, market) : null;

  const make = (tier: TierName, label: string, m: MarketCode): BetTier => {
    const p = marketProbability(m, probs);
    return {
      tier,
      label,
      market: m,
      selection: selectionText(m, homeTeam, awayTeam),
      probability: p,
      impliedOdds: p > 0 ? 1 / p : 0,
      edge: edgeOf(m),
    };
  };

  const doubles: MarketCode[] = ['1X', 'X2', '12'];
  const singles: MarketCode[] = ['1', 'X', '2'];

  const bestDouble = doubles.reduce((best, m) =>
    marketProbability(m, probs) > marketProbability(best, probs) ? m : best
  );
  const favourite = singles.reduce((best, m) =>
    marketProbability(m, probs) > marketProbability(best, probs) ? m : best
  );

  const others = singles.filter((m) => m !== favourite);
  let longshot: MarketCode;
  if (market) {
    // Biggest positive disagreement with the bookmakers = where the value is.
    longshot = others.reduce((best, m) => ((edgeOf(m) ?? -1) > (edgeOf(best) ?? -1) ? m : best));
  } else {
    longshot = others.reduce((best, m) =>
      marketProbability(m, probs) > marketProbability(best, probs) ? m : best
    );
  }

  return [
    make('low', 'Lower risk', bestDouble),
    make('medium', 'Balanced', favourite),
    make('high', 'Higher risk', longshot),
  ];
}

/** Compact, token-cheap description of the tiers for the AI prompt. */
export function describeTiers(tiers: BetTier[]): string {
  return tiers
    .map(
      (t) =>
        `${t.tier}: ${t.market} (${t.selection}) — model ${pct(t.probability)}%, ` +
        `fair odds ${t.impliedOdds.toFixed(2)}` +
        (t.edge !== null ? `, edge vs market ${t.edge >= 0 ? '+' : ''}${pct(t.edge)}pp` : '')
    )
    .join('\n');
}
