import type { ClinicalState, ClinicalVitals, MedicalCatalog } from './data';

export interface MissionSnapshot {
  day: number;
  date: string;
  environment: string;
  comms_delay_seconds: number;
  inputs: { exercise_mins: number; sleep_hours: number; mood_score: number };
  space_metrics: {
    radiation_msv: { value: number; daily_rate: number; status: string };
    bone_density_pct: { value: number; status: string };
    muscle_strength_pct: { value: number; status: string };
    vo2_max: { value: number; status: string };
    mii_score: { value: number; status: string };
  };
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  const contentType = response.headers.get('content-type') ?? '';
  const body = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof body === 'string' ? body : body.error ?? `Request failed (${response.status})`;
    throw new Error(message);
  }
  return body as T;
}

export const api = {
  clinicalState: () => request<ClinicalState>('/api/clinical/state'),
  catalog: () => request<MedicalCatalog>('/api/clinical/catalog'),
  day115: () => request<MissionSnapshot>('/api/day/115'),
  evaluateVitals: (vitals: ClinicalVitals) => request<{ urgency: ClinicalState['triage']['urgency']; flags: ClinicalState['triage']['flags'] }>('/api/clinical/evaluate', {
    method: 'POST', body: JSON.stringify({ vitals }),
  }),
  saveVitals: (vitals: ClinicalVitals, symptoms: string[]) => request<ClinicalState>('/api/clinical/vitals', {
    method: 'POST', body: JSON.stringify({ vitals, symptoms }),
  }),
  saveSymptoms: (symptoms: string[]) => request<ClinicalState>('/api/clinical/symptoms', {
    method: 'POST', body: JSON.stringify({ symptoms }),
  }),
  scenario: (name: string) => request<ClinicalState>(`/api/clinical/scenario/${encodeURIComponent(name)}`, {
    method: 'POST', body: '{}',
  }),
  audit: (type: string, detail: string) => request<ClinicalState>('/api/clinical/audit', {
    method: 'POST', body: JSON.stringify({ type, detail }),
  }),
};
