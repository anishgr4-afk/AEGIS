export type Status = 'ok' | 'watch' | 'act';
export type SolarLevel = 'calm' | 'elevated' | 'storm';
export type RegionId = 'head' | 'chest' | 'arms' | 'stomach' | 'legs';

export const STATUS_COLOR: Record<Status, string> = {
  ok: 'var(--ok)',
  watch: 'var(--watch)',
  act: 'var(--act)',
};
export const STATUS_LABEL: Record<Status, string> = { ok: 'OK', watch: 'WATCH', act: 'ACT' };

export interface Vital {
  name: string;
  value: string;
  unit: string;
  status: Status;
  note: string;
  baseline: number;
  /** polyline points in a 160x36 box; omit when `bars` is used */
  points?: string;
  /** relative bar heights (%) for the last 7 days; the last bar is today */
  bars?: number[];
}

export const VITALS: Vital[] = [
  { name: 'Body temperature', value: '36.8', unit: '°C', status: 'ok', note: 'Earth normal 36.7 °C', baseline: 18, points: '0,19 27,17 53,18 80,16 107,19 133,18 160,17' },
  { name: 'SpO₂', value: '97', unit: '%', status: 'ok', note: 'Earth normal 98 %', baseline: 12, points: '0,12 27,14 53,12 80,13 107,11 133,14 160,13' },
  { name: 'Sleep', value: '5.4', unit: 'h', status: 'watch', note: 'Under 6 h for 3 nights', baseline: 7, points: '0,6 27,10 53,12 80,18 107,25 133,27 160,30' },
  { name: 'Light exposure', value: '0.6', unit: 'h bright', status: 'watch', note: 'Target 2 h a day', baseline: 0, bars: [85, 80, 60, 45, 35, 30, 28] },
  { name: 'Avg heart rate', value: '64', unit: 'bpm', status: 'ok', note: 'Earth normal 61 bpm', baseline: 20, points: '0,22 27,19 53,17 80,21 107,16 133,19 160,18' },
];

export interface Region {
  id: RegionId;
  name: string;
  status: Status;
  /** short label on the body callout */
  summary: string;
  /** longer explanation under the body */
  note: string;
}

export const REGIONS: Region[] = [
  { id: 'head', name: 'HEAD', status: 'watch', summary: 'Sleep low', note: 'Short sleep and low morning light can cause headaches and slow thinking.' },
  { id: 'chest', name: 'CHEST', status: 'ok', summary: '64 bpm · SpO₂ 97%', note: 'Heart rate and SpO₂ are close to your Earth normal.' },
  { id: 'arms', name: 'ARMS', status: 'ok', summary: 'No issues', note: 'No symptoms logged this week.' },
  { id: 'stomach', name: 'STOMACH', status: 'ok', summary: 'No issues', note: 'No symptoms logged this week.' },
  { id: 'legs', name: 'LEGS', status: 'ok', summary: 'Exercise on track', note: 'Resistance exercise is on track, which protects bone and muscle.' },
];

export interface Measurement {
  name: string;
  when: string;
  value: string;
  unit: string;
  age: string;
  due?: boolean;
}

export const MEASUREMENTS: Measurement[] = [
  { name: 'Blood pressure', when: '11 Nov · 08:05', value: '118/76', unit: 'mmHg', age: '3 days ago' },
  { name: 'Blood sugar', when: '05 Nov · 07:40', value: '94', unit: 'mg/dL', age: '9 days ago' },
  { name: 'Body mass', when: '12 Nov · 07:15', value: '72.4', unit: 'kg', age: '2 days ago' },
  { name: 'Vision check', when: '08 Nov · 18:20', value: 'Clear', unit: '', age: 'Due today', due: true },
];

export interface SolarState {
  label: string;
  status: Status;
  line: string;
  level: number;
  warning?: { title: string; text: string };
  radToday: string;
  radLabel: string;
  radStatus: Status;
  nextTitle: string;
  nextWhy: string;
}

export const SOLAR: Record<SolarLevel, SolarState> = {
  calm: {
    label: 'CALM', status: 'ok', line: 'No flares in the last 24 h.', level: 2,
    radToday: '0.47', radLabel: 'NORMAL', radStatus: 'ok',
    nextTitle: 'Take your blood pressure', nextWhy: 'You reported a headache after 3 short nights.',
  },
  elevated: {
    label: 'ELEVATED', status: 'watch', line: 'Flare detected 08:14. Particle event possible in ~6 h.', level: 7,
    warning: { title: 'Solar flare', text: 'Check your shelter route.' },
    radToday: '0.47', radLabel: 'NORMAL', radStatus: 'ok',
    nextTitle: 'Take your blood pressure', nextWhy: 'You reported a headache after 3 short nights.',
  },
  storm: {
    label: 'STORM', status: 'act', line: 'Particle storm in progress. Shelter now.', level: 11,
    warning: { title: 'Radiation storm', text: 'Shelter now.' },
    radToday: '2.10', radLabel: 'HIGH', radStatus: 'act',
    nextTitle: 'Go to the storm shelter', nextWhy: 'Radiation storm in progress. Stay until the alert clears.',
  },
};
