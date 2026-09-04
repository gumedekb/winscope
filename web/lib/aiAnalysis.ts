/**
 * Decode the AI analysis stored on a predictions row into a proper object.
 *
 * The predict route bundles the whole analysis object as JSON into the
 * `gemini_summary` column (with the individual arrays mirrored into their own
 * columns as a fallback). This helper reverses that safely so both the list
 * (`/api/teams`) and detail (`/api/predict`) endpoints return a real object —
 * never the raw JSON string, which previously leaked into the UI as the summary.
 */
export interface AiAnalysis {
  summary: string;
  safe_predictions: string[];
  risky_predictions: string[];
  key_factors: string[];
  model_commentary: string;
  confidence_notes: string;
}

const arr = (v: any): string[] => {
  if (Array.isArray(v)) return v;
  try { const p = JSON.parse(v || '[]'); return Array.isArray(p) ? p : []; } catch { return []; }
};

export function parseStoredAnalysis(pred: any): AiAnalysis | null {
  if (!pred || !pred.gemini_summary) return null;

  const raw = String(pred.gemini_summary).trim();

  // Preferred: the whole object was bundled as JSON into gemini_summary.
  if (raw.startsWith('{')) {
    try {
      const o = JSON.parse(raw);
      return {
        summary: o.summary || '',
        safe_predictions: arr(o.safe_predictions),
        risky_predictions: arr(o.risky_predictions),
        key_factors: arr(o.key_factors),
        model_commentary: o.model_commentary || '',
        confidence_notes: o.confidence_notes || '',
      };
    } catch { /* fall through to column fallback */ }
  }

  // Fallback: plain-text summary + the mirrored array columns.
  return {
    summary: raw,
    safe_predictions: arr(pred.gemini_safe_predictions),
    risky_predictions: arr(pred.gemini_risky_predictions),
    key_factors: arr(pred.gemini_key_factors),
    model_commentary: '',
    confidence_notes: '',
  };
}
