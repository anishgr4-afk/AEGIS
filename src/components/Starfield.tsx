import { useMemo } from 'react';

function mulberry32(seed: number) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const BRIGHT = [[220, 140], [1180, 620], [760, 80]];

export function Starfield() {
  const stars = useMemo(() => {
    const rnd = mulberry32(34);
    return Array.from({ length: 160 }, (_, i) => ({
      cx: Math.round(rnd() * 1440), cy: Math.round(rnd() * 900),
      r: +(0.5 + rnd() * 0.8).toFixed(1), o: +(0.25 + rnd() * 0.65).toFixed(2), tw: i % 9 === 0,
    }));
  }, []);
  return (
    <svg className="stars" aria-hidden="true" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice">
      {stars.map((s, i) => (
        <circle key={i} className={s.tw ? 'tw' : undefined} cx={s.cx} cy={s.cy} r={s.r} fill="#dce6ff" opacity={s.o}
          style={s.tw ? { animationDelay: `${(i % 5) * 0.6}s` } : undefined} />
      ))}
      {BRIGHT.map(([x, y]) => (
        <g key={x}>
          <circle cx={x} cy={y} r="1.8" fill="#fff" opacity="0.95" />
          <circle cx={x} cy={y} r="5" fill="#9cc4ff" opacity="0.15" />
        </g>
      ))}
    </svg>
  );
}
