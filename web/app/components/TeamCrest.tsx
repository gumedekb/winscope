"use client";
import React, { useState } from 'react';

interface Props {
  name?: string;
  /** Badge URL from Turso; null means we never found one for this club. */
  crest?: string | null;
  size?: number;
  className?: string;
}

/** Stable colour per club so the fallback badge isn't a wall of identical grey. */
const hueFor = (name: string) => {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) | 0;
  return Math.abs(hash) % 360;
};

/** "Brighton & Hove Albion" -> "BH", "Arsenal" -> "AR". */
const initialsFor = (name: string) => {
  const words = name.replace(/[^A-Za-z0-9 ]/g, ' ').split(/\s+/).filter(Boolean);
  if (words.length === 0) return '?';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
};

/**
 * A club badge, with a drawn fallback.
 *
 * The fallback is rendered locally rather than fetched from an avatar service:
 * every club without a crest was previously an external image request that
 * could fail or hang, and a badge is not worth a network round trip. It also
 * means the card looks right offline.
 */
export const TeamCrest: React.FC<Props> = ({ name, crest, size = 48, className = '' }) => {
  const [failed, setFailed] = useState(false);
  const label = name || '?';

  if (crest && !failed) {
    return (
      <img
        src={crest}
        alt=""
        width={size}
        height={size}
        loading="lazy"
        onError={() => setFailed(true)}
        className={`object-contain ${className}`}
        style={{ width: size, height: size }}
      />
    );
  }

  const hue = hueFor(label);
  return (
    <div
      role="img"
      aria-label={label}
      title={label}
      className={`flex items-center justify-center rounded-full font-black select-none ${className}`}
      style={{
        width: size,
        height: size,
        fontSize: size * 0.36,
        background: `linear-gradient(135deg, hsl(${hue} 45% 26%), hsl(${hue} 45% 16%))`,
        color: `hsl(${hue} 70% 72%)`,
        border: `1px solid hsl(${hue} 40% 32%)`,
      }}
    >
      {initialsFor(label)}
    </div>
  );
};
