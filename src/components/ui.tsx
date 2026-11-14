import type { ReactNode } from 'react';
import { STATUS_COLOR, STATUS_LABEL, type Status } from '../data';

export function Dot({ color, size = 7 }: { color: string; size?: number }) {
  return (
    <svg width={size} height={size} aria-hidden="true">
      <circle cx={size / 2} cy={size / 2} r={size / 2} fill={color} />
    </svg>
  );
}

export function StatusPill({ status, label, size }: { status: Status; label?: string; size?: number }) {
  return (
    <span className="status" style={{ color: STATUS_COLOR[status] }}>
      <Dot color="currentColor" size={size} />
      {label ?? STATUS_LABEL[status]}
    </span>
  );
}

export function Value({ children, unit }: { children: ReactNode; unit?: string }) {
  return (
    <>
      {children}
      {unit ? <span className="unit">{unit}</span> : null}
    </>
  );
}

/** Dashed threshold line + polyline, stretched to its container. */
export function Sparkline({ points, baseline, line = '#c5cdd9', guide = '#2a3548', viewBox = '0 0 160 36', height = 36 }: {
  points: string; baseline: number; line?: string; guide?: string; viewBox?: string; height?: number;
}) {
  const w = Number(viewBox.split(' ')[2]);
  return (
    <svg viewBox={viewBox} width="100%" height={height} preserveAspectRatio="none" aria-hidden="true">
      <line x1="0" y1={baseline} x2={w} y2={baseline} stroke={guide} strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
      <polyline points={points} fill="none" stroke={line} strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
