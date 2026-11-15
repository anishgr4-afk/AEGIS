import type { ChartPoint, TimedPoint } from './chartData';

export function formatTrendValue(value: number): string {
  if (!Number.isFinite(value)) return '—';
  if (value !== 0 && (Math.abs(value) < 0.001 || Math.abs(value) >= 100_000_000)) return value.toExponential(7);
  return value.toLocaleString(undefined, { maximumSignificantDigits: 10 });
}

export function formatTrendAxis(value: number, step: number): string {
  if (!Number.isFinite(value) || !Number.isFinite(step) || step <= 0) return formatTrendValue(value);
  if (value === 0) return '0';
  const significant = Math.max(1, Math.min(12, Math.ceil(Math.log10(Math.max(1, Math.abs(value) / step))) + 1));
  if (Math.abs(value) < 0.001 || Math.abs(value) >= 100_000_000 || significant > 7) return value.toExponential(significant);
  const decimals = Math.max(0, Math.min(12, 1 - Math.floor(Math.log10(step))));
  return value.toLocaleString(undefined, { maximumFractionDigits: decimals });
}

/** Break visual guides at unusually large observed gaps (not a missing-data diagnosis). */
export function chartSegments(actual: ChartPoint[], samples: TimedPoint[], dailyAlerts: boolean): TimedPoint[][] {
  if (!samples.length) return [];
  const times = actual.map(p => Date.parse(p.timestamp)).filter(Number.isFinite).sort((a, b) => a - b);
  const intervals = times.slice(1).map((t, i) => t - times[i]).filter(n => n > 0).sort((a, b) => a - b);
  // Alert bins represent UTC days: a missing plotted day is never bridged.
  // Other series have no declared cadence, so use the median only as a
  // disclosed visual heuristic, not a claim that readings should exist.
  const median = intervals.length ? (intervals[Math.floor((intervals.length - 1) / 2)] + intervals[Math.floor(intervals.length / 2)]) / 2 : Infinity;
  const threshold = dailyAlerts ? 86_400_000 : intervals.length >= 2 ? median * 3 : Infinity;
  const breaks = times.slice(1).filter((t, i) => t - times[i] > threshold);
  let boundary = 0;
  const segments: TimedPoint[][] = [[samples[0]]];
  for (let i = 1; i < samples.length; i++) {
    while (boundary < breaks.length && breaks[boundary] <= samples[i - 1].time) boundary++;
    if (boundary < breaks.length && breaks[boundary] <= samples[i].time) segments.push([]);
    segments[segments.length - 1].push(samples[i]);
  }
  return segments;
}
