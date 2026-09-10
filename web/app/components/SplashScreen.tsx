"use client";
import React, { useEffect, useRef, useState } from 'react';

interface Props {
  isLoading: boolean;
  onFinished: () => void;
}

/** Minimum time on screen, so an already-warm load doesn't flash the splash. */
const MIN_VISIBLE_MS = 1400;
/**
 * Failsafe, NOT a pacing knob. The splash waits for the fixtures fetch, so this
 * only answers "the request never settled".
 *
 * Sized well above a normal load and well below the worst one. Nearly every
 * fixture already has a stored prediction, so /api/fixtures is a handful of
 * Turso reads and one batched model call — a few seconds. The exception is a
 * cold Render model server, where `predictSlate` can sit on its own 30s timeout;
 * waiting that out behind a splash is the "feels broken" case the dashboard's
 * spinner exists for, so hand over rather than match it.
 */
const MAX_VISIBLE_MS = 15_000;
/** How far the bar creeps while we genuinely don't know how long is left. */
const CREEP_CEILING = 92;
/** Where it starts, so the bar reads as "working" from the first frame. */
const CREEP_START = 6;
/**
 * Shapes the creep: ~63% of the way to the ceiling after one TAU, ~86% after
 * two, and always still moving. Tuned so a normal fetch spends its whole life
 * on the rising part of the curve rather than parked at the ceiling.
 */
const CREEP_TAU_MS = 4000;
/** Must match the bar's width transition and the wrapper's opacity duration. */
const FILL_MS = 300;
const FADE_MS = 300;

/**
 * Splash / loading screen.
 *
 * Three things worth noting:
 *
 * `w-full` is load-bearing. `body` is `display:flex` (globals.css), so a child
 * without a width is sized to its content and pinned to the left edge — which
 * is exactly how the wordmark ended up hugging the side of the screen.
 *
 * The progress bar eases toward 92% rather than claiming a real percentage. We
 * don't know how long the first fixtures fetch will take (it predicts the whole
 * slate on a cold start), so a bar that crept to 100% and then sat there would
 * be lying. It only completes once the data is actually in.
 *
 * And it waits for that data. This used to hand over on a 2.6s timer no matter
 * what the fetch was doing, which meant the bar filled, the splash left, and the
 * dashboard came up on ITS OWN "Loading matches…" spinner — two loading screens
 * back to back for one load. The bar is a progress bar for the fetch, so the
 * fetch is what ends it; `isLoading` is the only thing that completes it.
 */
export const SplashScreen: React.FC<Props> = ({ isLoading, onFinished }) => {
  const [progress, setProgress] = useState(CREEP_START);
  const [leaving, setLeaving] = useState(false);
  const mountedAt = useRef(Date.now());

  // `onFinished` is an inline arrow in Dashboard, so its identity changes on
  // every render. Held in a ref, the timers below depend on nothing that churns
  // — otherwise an unrelated re-render (the betslip resolving, say) would clear
  // and restart the handover, and the splash would overstay by a full wait.
  const finish = useRef(onFinished);
  useEffect(() => { finish.current = onFinished; });

  // Creep upward for as long as the fetch runs; never reach the end on its own.
  useEffect(() => {
    if (!isLoading) return;
    const id = setInterval(() => {
      const elapsed = Date.now() - mountedAt.current;
      setProgress(
        CREEP_START + (CREEP_CEILING - CREEP_START) * (1 - Math.exp(-elapsed / CREEP_TAU_MS))
      );
    }, 80);
    return () => clearInterval(id);
  }, [isLoading]);

  // The data arrived — the one thing that fills the bar and ends the splash.
  useEffect(() => {
    if (isLoading) return;
    setProgress(100);
    const elapsed = Date.now() - mountedAt.current;
    // Long enough to actually SEE the bar reach 100 and then fade: fill, a beat,
    // fade. Below that the handover swallows its own animation.
    const wait = Math.max(MIN_VISIBLE_MS - elapsed, FILL_MS + 150 + FADE_MS);
    const fade = setTimeout(() => setLeaving(true), wait - FADE_MS);
    const done = setTimeout(() => finish.current(), wait);
    return () => { clearTimeout(fade); clearTimeout(done); };
  }, [isLoading]);

  // Failsafe: a fetch that never settles must not trap anyone here.
  useEffect(() => {
    const fade = setTimeout(() => setLeaving(true), MAX_VISIBLE_MS - FADE_MS);
    const done = setTimeout(() => finish.current(), MAX_VISIBLE_MS);
    return () => { clearTimeout(fade); clearTimeout(done); };
  }, []);

  return (
    <div
      className={`min-h-screen w-full flex flex-col items-center justify-center px-6
                  transition-opacity duration-300 ${leaving ? 'opacity-0' : 'opacity-100'}`}
    >
      <div className="flex flex-col items-center w-full max-w-xs">
        {/* Crest. The PNG has a solid navy ground, so it sits on a rounded
            plate with a gold ring — the navy then reads as part of the badge. */}
        <div className="splash-crest relative mb-8">
          <img
            src="/favicon.png"
            alt="WinScope"
            width={104}
            height={104}
            className="w-24 h-24 md:w-28 md:h-28 rounded-2xl object-cover"
            style={{ boxShadow: '0 0 0 1px rgba(255,215,0,0.35), 0 12px 40px rgba(0,0,0,0.6)' }}
          />
        </div>

        <h1 className="text-3xl md:text-4xl font-black tracking-tight leading-none">
          <span className="text-[#FFD700]">Win</span><span className="text-white">Scope</span>
        </h1>

        <p className="mt-3 text-[10px] uppercase tracking-[0.35em] text-gray-500">
          Bet with confidence
        </p>

        {/* Progress */}
        <div
          className="mt-10 w-full h-[3px] rounded-full bg-white/10 overflow-hidden"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progress)}
          aria-label="Loading match data"
        >
          <div
            className="h-full rounded-full transition-[width] duration-300 ease-out"
            style={{
              width: `${progress}%`,
              background: 'linear-gradient(90deg, #E30613 0%, #FFD700 100%)',
            }}
          />
        </div>

        <p className="mt-3 text-[10px] uppercase tracking-[0.2em] text-gray-600">
          {isLoading ? 'Loading matches…' : 'Ready'}
        </p>
      </div>
    </div>
  );
};

export default SplashScreen;
