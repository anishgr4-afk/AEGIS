export type TriageUrgency = 'routine' | 'watch' | 'act';
export type SymptomRegion = 'systemic' | 'head_eent' | 'cardiopulmonary' | 'abdomen_gi' | 'musculoskeletal_skin' | 'toxicology_eva';

export interface ClinicalVitals {
  body_temp_c: number;
  spo2_pct: number;
  heart_rate_bpm: number;
  bp_systolic: number;
  bp_diastolic: number;
  respiratory_rate_bpm: number;
}

export interface TriageFlag {
  id: string;
  label: string;
  value: string;
  unit: string;
  severity: Exclude<TriageUrgency, 'routine'>;
  threshold: string;
}

export interface AuditEvent {
  timestamp_utc: string;
  met: string;
  type: string;
  detail: string;
}

export interface ClinicalState {
  vitals: ClinicalVitals;
  symptoms: string[];
  scenario: string;
  triage: { urgency: TriageUrgency; flags: TriageFlag[] };
  audit: AuditEvent[];
}

export interface ProtocolStep {
  id: string;
  text: string;
  warning?: string;
  source_page?: number;
}

export interface ProtocolCandidate {
  id: string;
  title: string;
  section: string;
  source_id: string;
  page_start: number;
  page_end: number;
  printed_page_reference?: string;
  region: string;
  symptoms: string[];
  required_vitals: string[];
  urgency: string;
  diagnostic_checks: string[];
  steps: ProtocolStep[];
  warnings: string[];
  med_kit_locations: string[];
  evidence_excerpt: string;
  verification_status: string;
}

export interface MedicalSource {
  id: string;
  file_name: string;
  sha256: string;
  page_count: number;
  title: string;
  extraction: string;
}

export interface MedicalCatalog {
  catalog_status: string;
  notice: string;
  sources: MedicalSource[];
  protocols: ProtocolCandidate[];
  medication_evidence: { source_id: string; page: number; excerpt: string; verification_status: string }[];
  warnings_and_contraindications: { source_id: string; page: number; excerpt: string; verification_status: string }[];
  stowage_evidence: { source_id: string; page: number; excerpt: string; verification_status: string }[];
  source_documents_available: boolean;
}

export interface SymptomOption {
  id: string;
  label: string;
  region: SymptomRegion;
}

export const SYMPTOMS: SymptomOption[] = [
  { id: 'fever_chills', label: 'Fever / chills', region: 'systemic' },
  { id: 'weakness_fatigue', label: 'Weakness / fatigue', region: 'systemic' },
  { id: 'shortness_breath', label: 'Shortness of breath / wheezing', region: 'cardiopulmonary' },
  { id: 'chest_pain', label: 'Chest pain', region: 'cardiopulmonary' },
  { id: 'abdominal_pain', label: 'Abdominal pain', region: 'abdomen_gi' },
  { id: 'nausea_vomiting', label: 'Nausea / vomiting', region: 'abdomen_gi' },
  { id: 'headache_dizziness', label: 'Headache / dizziness', region: 'head_eent' },
  { id: 'joint_pain', label: 'Joint pain', region: 'musculoskeletal_skin' },
  { id: 'tingling', label: 'Tingling / numbness', region: 'musculoskeletal_skin' },
  { id: 'ear_sinus_pain', label: 'Ear / sinus pain', region: 'head_eent' },
  { id: 'skin_rash_burn', label: 'Skin rash / burn', region: 'musculoskeletal_skin' },
  { id: 'cough', label: 'Cough', region: 'cardiopulmonary' },
];

export const REGIONS: { id: SymptomRegion | 'all'; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'head_eent', label: 'Head / EENT' },
  { id: 'cardiopulmonary', label: 'Cardiopulmonary' },
  { id: 'abdomen_gi', label: 'Abdomen / GI' },
  { id: 'musculoskeletal_skin', label: 'Musculoskeletal / skin' },
  { id: 'toxicology_eva', label: 'Toxicology / EVA' },
];

export const SCENARIOS = [
  { id: 'nominal_transit', label: 'Nominal transit' },
  { id: 'acute_febrile', label: 'Acute febrile pattern' },
  { id: 'post_eva_dcs', label: 'Post-EVA symptoms' },
  { id: 'hypovolemic_shock', label: 'Low BP pattern' },
] as const;

export const STATUS_COLOR: Record<TriageUrgency, string> = {
  routine: 'var(--ok)',
  watch: 'var(--watch)',
  act: 'var(--act)',
};

export function urgencyLabel(urgency: TriageUrgency): string {
  return urgency === 'routine' ? 'ROUTINE' : urgency.toUpperCase();
}
