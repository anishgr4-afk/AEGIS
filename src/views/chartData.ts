export type ChartPoint = { timestamp: string; value: number; series: string; unit: string };
export type TimedPoint = ChartPoint & { time: number };

const MAX_CHART_SERIES = 200;
const MAX_CHART_POINTS = 1200;
const MAX_POINTS_PER_SERIES = 240;

// Each time bucket contributes its actual minimum and maximum observations in
// time order; endpoints and global extrema therefore survive downsampling.
function sample(rows: TimedPoint[], budget: number): TimedPoint[] {
  if (rows.length <= budget) return rows;
  const bucketCount = Math.max(1, Math.floor((budget - 2) / 2));
  const interior = rows.length - 2;
  const result = [rows[0]];
  for (let bucket = 0; bucket < bucketCount; bucket++) {
    const start = 1 + Math.floor(interior * bucket / bucketCount);
    const end = 1 + Math.floor(interior * (bucket + 1) / bucketCount);
    let minIndex = start, maxIndex = start;
    for (let i = start + 1; i < end; i++) {
      if (rows[i].value < rows[minIndex].value) minIndex = i;
      if (rows[i].value > rows[maxIndex].value) maxIndex = i;
    }
    if (minIndex === maxIndex) result.push(rows[minIndex]);
    else if (minIndex < maxIndex) result.push(rows[minIndex], rows[maxIndex]);
    else result.push(rows[maxIndex], rows[minIndex]);
  }
  result.push(rows[rows.length - 1]);
  return result;
}

export function prepareChart(points: ChartPoint[]) {
  const groups = new Map<string, TimedPoint[]>();
  let validCount = 0;
  for (const point of points) {
    const time = Date.parse(point.timestamp);
    if (!Number.isFinite(point.value) || !Number.isFinite(time)) continue;
    validCount++;
    const group = groups.get(point.series);
    const timed = { ...point, time };
    if (group) group.push(timed);
    else groups.set(point.series, [timed]);
  }
  const series = Array.from(groups.entries()).slice(0, MAX_CHART_SERIES);
  const budget = Math.min(MAX_POINTS_PER_SERIES, Math.floor(MAX_CHART_POINTS / Math.max(1, series.length)));
  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity, displayedCount = 0;
  const plotted = series.map(([name, rows]) => {
    rows.sort((a, b) => a.time - b.time);
    // Bounds use every real record in each visible series, not only samples.
    for (const p of rows) {
      if (p.time < xmin) xmin = p.time;
      if (p.time > xmax) xmax = p.time;
      if (p.value < ymin) ymin = p.value;
      if (p.value > ymax) ymax = p.value;
    }
    const samples = sample(rows, budget);
    displayedCount += samples.length;
    return { name, samples };
  });
  return { plotted, validCount, displayedCount, totalSeries: groups.size, xmin, xmax, ymin, ymax };
}
