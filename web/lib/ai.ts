/**
 * AI provider layer — Gemini first, OpenRouter as fallback.
 *
 * Both are free tiers, so the rule everywhere in this app is: **never generate
 * an insight unless a human asked for this specific match.** Nothing here is
 * called while listing fixtures; only /api/insight calls it, and only on an
 * explicit click. Results are cached in Turso so clicking the same match twice
 * costs nothing.
 */

export interface TierRationale {
  tier: 'low' | 'medium' | 'high';
  rationale: string;
}

export interface InsightPayload {
  summary: string;
  key_factors: string[];
  tier_rationales: TierRationale[];
  model_commentary: string;
  confidence_notes: string;
}

export interface GeneratedInsight extends InsightPayload {
  provider: 'gemini' | 'openrouter';
  model: string;
}

const GEMINI_MODEL = process.env.GEMINI_MODEL || 'gemini-2.5-flash';
/**
 * OpenRouter free slugs come and go — `google/gemini-2.0-flash-exp:free` 404s
 * now, for instance. Try a list so one retirement does not break the fallback.
 */
const OPENROUTER_MODELS = (
  process.env.OPENROUTER_MODELS ||
  'minimax/minimax-m3:free,nvidia/nemotron-3.5-lightning:free,thinkingmachines/inkling:free'
)
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);

export const geminiConfigured = () => Boolean(process.env.GEMINI_API_KEY);
export const openRouterConfigured = () => Boolean(process.env.OPENROUTER_API_KEY);
export const aiConfigured = () => geminiConfigured() || openRouterConfigured();

/** Models like to wrap JSON in prose or code fences. Dig it out. */
function extractJson(text: string): unknown {
  const cleaned = text.replace(/```json/gi, '').replace(/```/g, '').trim();
  try {
    return JSON.parse(cleaned);
  } catch {
    const start = cleaned.indexOf('{');
    const end = cleaned.lastIndexOf('}');
    if (start === -1 || end <= start) throw new Error('no JSON object in response');
    return JSON.parse(cleaned.slice(start, end + 1));
  }
}

const str = (v: unknown, fallback = ''): string =>
  typeof v === 'string' && v.trim() ? v.trim() : fallback;

const strArray = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && !!x.trim()) : [];

/** Coerce whatever the model returned into our shape — never trust it blindly. */
function normalise(raw: unknown): InsightPayload {
  const o = (raw ?? {}) as Record<string, unknown>;
  const rawTiers = Array.isArray(o.tier_rationales) ? o.tier_rationales : [];
  const byTier = new Map<string, string>();
  for (const t of rawTiers) {
    const rec = (t ?? {}) as Record<string, unknown>;
    const name = str(rec.tier).toLowerCase();
    if (['low', 'medium', 'high'].includes(name)) {
      byTier.set(name, str(rec.rationale));
    }
  }
  return {
    summary: str(o.summary, 'No summary produced.'),
    key_factors: strArray(o.key_factors).slice(0, 6),
    tier_rationales: (['low', 'medium', 'high'] as const).map((tier) => ({
      tier,
      rationale: byTier.get(tier) ?? '',
    })),
    model_commentary: str(o.model_commentary),
    confidence_notes: str(o.confidence_notes),
  };
}

async function callGemini(prompt: string, signal: AbortSignal): Promise<InsightPayload> {
  const res = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent`,
    {
      method: 'POST',
      headers: {
        'x-goog-api-key': process.env.GEMINI_API_KEY || '',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        contents: [{ parts: [{ text: prompt }] }],
        generationConfig: { temperature: 0.4, responseMimeType: 'application/json' },
      }),
      signal,
    }
  );
  if (!res.ok) {
    throw new Error(`gemini ${res.status}: ${(await res.text()).slice(0, 200)}`);
  }
  const data = await res.json();
  const text = data?.candidates?.[0]?.content?.parts?.map((p: { text?: string }) => p.text).join('') ?? '';
  if (!text) throw new Error('gemini returned no text');
  return normalise(extractJson(text));
}

async function callOpenRouter(prompt: string, signal: AbortSignal): Promise<{ payload: InsightPayload; model: string }> {
  let lastError: unknown = null;
  for (const model of OPENROUTER_MODELS) {
    try {
      const res = await fetch('https://openrouter.ai/api/v1/chat/completions', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${process.env.OPENROUTER_API_KEY || ''}`,
          'Content-Type': 'application/json',
          // OpenRouter uses these for its free-tier attribution.
          'HTTP-Referer': process.env.PUBLIC_URL || 'http://localhost:3000',
          'X-Title': 'WinScope',
        },
        body: JSON.stringify({
          model,
          temperature: 0.4,
          response_format: { type: 'json_object' },
          messages: [{ role: 'user', content: prompt }],
        }),
        signal,
      });
      if (!res.ok) {
        lastError = new Error(`openrouter ${model} ${res.status}: ${(await res.text()).slice(0, 160)}`);
        continue;
      }
      const data = await res.json();
      if (data?.error) {
        lastError = new Error(`openrouter ${model}: ${data.error.message}`);
        continue;
      }
      const text: string = data?.choices?.[0]?.message?.content ?? '';
      if (!text) {
        lastError = new Error(`openrouter ${model}: empty response`);
        continue;
      }
      return { payload: normalise(extractJson(text)), model };
    } catch (e) {
      lastError = e;
    }
  }
  throw lastError instanceof Error ? lastError : new Error('all OpenRouter models failed');
}

/**
 * Generate one insight. Tries Gemini, falls back to OpenRouter.
 * Throws only if every provider fails — the caller surfaces that to the user
 * rather than silently showing a blank panel.
 */
export async function generateInsight(prompt: string, timeoutMs = 45_000): Promise<GeneratedInsight> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const errors: string[] = [];

  try {
    if (geminiConfigured()) {
      try {
        const payload = await callGemini(prompt, controller.signal);
        return { ...payload, provider: 'gemini', model: GEMINI_MODEL };
      } catch (e) {
        errors.push(e instanceof Error ? e.message : String(e));
      }
    }
    if (openRouterConfigured()) {
      try {
        const { payload, model } = await callOpenRouter(prompt, controller.signal);
        return { ...payload, provider: 'openrouter', model };
      } catch (e) {
        errors.push(e instanceof Error ? e.message : String(e));
      }
    }
    if (errors.length === 0) {
      throw new Error('No AI provider configured — set GEMINI_API_KEY or OPENROUTER_API_KEY');
    }
    throw new Error(errors.join(' | '));
  } finally {
    clearTimeout(timer);
  }
}
