import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Activity, AlertCircle, ArrowDownToLine, ArrowRight, Check, CheckCircle2, ChevronRight, ClipboardList, Clock3, FileText, Globe2, History, Link2, LockKeyhole, Package, Plus, Radio, RefreshCw, Send, Settings2, ShieldCheck, Siren, UserRound, WifiOff } from 'lucide-react'
import { api, expireSession, TOKEN_KEY, type ViewProps } from '../lib'
import { EvidencePanel } from './EvidencePanel'
import '../ops.css'

type Row = Record<string, any>
type ChecklistItem = { text: string; done: boolean }
const roles = ['Commander', 'Medical Officer', 'Mission Operator', 'Administrator']
const humanize = (value: unknown) => String(value ?? '—').replace(/_/g, ' ')
const stamp = (value: unknown) => {
  if (!value) return 'Not recorded'
  const parsed = new Date(String(value))
  return Number.isNaN(parsed.getTime()) ? String(value) : `${parsed.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' })} UTC`
}
const errText = (error: unknown) => error instanceof Error ? error.message : String(error)
const json = (value: unknown) => JSON.stringify(value, null, 2)
const post = (path: string, value?: unknown) => api(path, { method: 'POST', ...(value === undefined ? {} : { body: JSON.stringify(value) }) })
const patch = (path: string, value: unknown) => api(path, { method: 'PATCH', body: JSON.stringify(value) })
const idPath = (id: unknown) => encodeURIComponent(String(id))
const actorName = (value: any) => value && typeof value === 'object' ? (value.display_name || value.username || value.name || value.id || '—') : (value || '—')
const crewName = (crew: Row[], id: unknown) => crew.find(person => String(person.id) === String(id))?.name || (id ? String(id) : 'Not assigned')
const formData = (event: FormEvent<HTMLFormElement>) => new FormData(event.currentTarget)
const field = (data: FormData, key: string) => String(data.get(key) ?? '').trim()

// Retain only a key and a digest, not medical notes or message contents. A
// failed/ambiguous response must be retried with the same payload and key.
const pendingKey = (userId: string, path: string) => `checkpoint:pending:${userId}:${path}`
async function idempotentPost(userId: string, path: string, value: unknown) {
  const slot = pendingKey(userId, path)
  let pending: { key: string; digest?: string } = JSON.parse(sessionStorage.getItem(slot) || 'null') || { key: crypto.randomUUID() }
  sessionStorage.setItem(slot, JSON.stringify(pending))
  const bytes = new TextEncoder().encode(JSON.stringify(value))
  const hash = await crypto.subtle.digest('SHA-256', bytes)
  const digest = Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('')
  pending = JSON.parse(sessionStorage.getItem(slot) || 'null') || pending
  if (pending.digest && pending.digest !== digest) throw new Error('The previous submission may have succeeded. Restore its exact values and retry with the same key, or check history before explicitly clearing the pending retry.')
  pending.digest = digest
  sessionStorage.setItem(slot, JSON.stringify(pending))
  const result = await api(path, { method: 'POST', headers: { 'Idempotency-Key': pending.key }, body: JSON.stringify(value) })
  if (JSON.parse(sessionStorage.getItem(slot) || 'null')?.key === pending.key) sessionStorage.removeItem(slot)
  return result
}
function clearPending(userId: string, path: string) {
  if (window.confirm('Only clear after checking the server history. If the prior request succeeded, a new submission could create a duplicate. Clear its retry key?')) sessionStorage.removeItem(pendingKey(userId, path))
}

function useCollection(path: string, enabled = true) {
  const [rows, setRows] = useState<Row[]>([])
  const [loading, setLoading] = useState(enabled)
  const [error, setError] = useState('')
  const reload = useCallback(async () => {
    if (!enabled) { setRows([]); setLoading(false); return }
    setLoading(true)
    try {
      const result = await api(path)
      if (!Array.isArray(result)) throw new Error('The server returned an unexpected collection response.')
      setRows(result)
      setError('')
    } catch (e) { setError(errText(e)) } finally { setLoading(false) }
  }, [path, enabled])
  useEffect(() => { void reload() }, [reload])
  return { rows, loading, error, reload }
}

function useAction(after?: () => Promise<unknown>) {
  const [pending, setPending] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const run = async (key: string, work: () => Promise<unknown>, message = 'Saved successfully.') => {
    setPending(key); setError(''); setSuccess('')
    try {
      await work()
      setSuccess(message)
      if (after) {
        try { await after() } catch (e) { setError(`Saved, but the view could not refresh: ${errText(e)}`) }
      }
      return true
    } catch (e) { setError(errText(e)); return false } finally { setPending('') }
  }
  return { pending, error, success, run }
}

function Heading({ eyebrow, title, description, icon, actions }: { eyebrow: string; title: string; description: string; icon: ReactNode; actions?: ReactNode }) {
  return <header className="op-heading"><div><div className="op-eyebrow"><span className="op-eyebrow-icon">{icon}</span>{eyebrow}<span className="op-eyebrow-line" /></div><h1>{title}</h1><p>{description}</p></div>{actions && <div className="op-heading-actions">{actions}</div>}</header>
}
function Panel({ title, subtitle, icon, children, aside, className = '' }: { title: string; subtitle?: string; icon?: ReactNode; children: ReactNode; aside?: ReactNode; className?: string }) {
  return <section className={`op-panel ${className}`}><div className="op-panel-head"><div><div className="op-panel-title">{icon && <span className="op-panel-icon">{icon}</span>}<h2>{title}</h2></div>{subtitle && <p>{subtitle}</p>}</div>{aside}</div>{children}</section>
}
function Banner({ error, success }: { error?: string; success?: string }) {
  return <>{error && <div className="op-banner op-banner-error" role="alert"><AlertCircle size={16} /><span>{error}</span></div>}{success && <div className="op-banner op-banner-success" role="status"><CheckCircle2 size={16} /><span>{success}</span></div>}</>
}
function LoadingState({ loading, error, count, empty }: { loading: boolean; error: string; count: number; empty: string }) {
  if (loading && !count) return <div className="op-empty" role="status"><RefreshCw size={18} /> Loading records…</div>
  if (error) return <div className="op-empty op-error" role="alert"><AlertCircle size={18} /> {error}</div>
  if (!count) return <div className="op-empty"><span className="op-empty-mark">◇</span>{empty}</div>
  return null
}
function Badge({ value }: { value: unknown }) {
  const label = humanize(value)
  const lower = label.toLowerCase()
  const variant = /critical|fail|expired|offline|high|inactive/.test(lower) ? 'danger' : /warning|queued|pending|medium|low stock/.test(lower) ? 'warning' : /delivered|acknowledged|resolved|connected|normal|active/.test(lower) ? 'success' : 'info'
  return <span className={`op-badge op-badge-${variant}`}><span />{label}</span>
}
function Button({ children, icon, className = '', ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { icon?: ReactNode }) {
  return <button {...props} className={`op-button ${className}`}>{icon}{children}</button>
}
function CrewSelect({ crew, name = 'crew_id', required = false, defaultValue = '', empty = 'Select crew member' }: { crew: Row[]; name?: string; required?: boolean; defaultValue?: string; empty?: string }) {
  return <select name={name} required={required} defaultValue={defaultValue}><option value="">{empty}</option>{crew.map(person => <option key={person.id} value={person.id}>{person.name}</option>)}</select>
}
function DetailLine({ label, children }: { label: string; children: ReactNode }) { return <div className="op-detail-line"><span>{label}</span><strong>{children}</strong></div> }
function ValueCard({ icon, label, value, sub, accent = '' }: { icon: ReactNode; label: string; value: ReactNode; sub: string; accent?: string }) { return <div className={`op-value-card ${accent}`}><div className="op-value-top"><span>{label}</span>{icon}</div><div className="op-value">{value}</div><div className="op-value-sub">{sub}</div></div> }

export function EarthLinkView({ user, data, refresh }: ViewProps) {
  const messages = useCollection('/api/messages')
  useEffect(() => {
    const poll = () => { if (document.visibilityState === 'visible') void messages.reload() }
    const timer = window.setInterval(poll, 5000)
    document.addEventListener('visibilitychange', poll)
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', poll) }
  }, [messages.reload])
  const [syncResult, setSyncResult] = useState<any>(null)
  const [filter, setFilter] = useState('all')
  const action = useAction(async () => { await messages.reload(); await refresh() })
  const settings = (data as any).settings || (data as any).mission || {}
  const delay = Number(settings.delay_minutes)
  const connected = Boolean(settings.earth_connected)
  const canConfigure = ['Commander', 'Administrator'].includes(user.role)
  const counts = (status: string) => messages.rows.filter(row => String(row.status).toLowerCase() === status).length
  const shown = messages.rows.filter(row => filter === 'all' || String(row.status).toLowerCase() === filter).slice().sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))
  const onCompose = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = event.currentTarget; const values = formData(event)
    if (await action.run('compose', () => idempotentPost(user.id, '/api/messages', { direction: field(values, 'direction'), subject: field(values, 'subject'), body: field(values, 'body'), priority: field(values, 'priority') }), 'Message saved. See its server-reported simulated transmission status below.')) form.reset()
  }
  const toggle = () => action.run('connection', () => patch('/api/settings', { earth_connected: !connected }), `Connection setting changed to ${connected ? 'disconnected' : 'connected'}.`)
  const synchronize = () => action.run('sync', async () => { const result = await post('/api/sync'); setSyncResult(result) }, 'Synchronization request completed. Review the outcome below.')
  const operate = (id: unknown, operation: string) => action.run(`${operation}-${id}`, () => post(`/api/messages/${idPath(id)}/${operation}`), `Message ${operation === 'acknowledge' ? 'acknowledged locally; this is not an Earth reply' : operation === 'fail' ? 'marked failed' : 'submitted for retry'}.`)
  return <div className="op-view">
    <Heading eyebrow="COMMUNICATIONS / EARTH LINK" title="Earth Link" description="Manage delayed communications and the local transmission queue." icon={<Radio size={15} />} actions={<Button icon={<RefreshCw size={15} />} onClick={() => void messages.reload()} disabled={messages.loading}>Refresh queue</Button>} />
    <div className="op-stat-grid op-stat-grid-four">
      <ValueCard label="LINK STATUS" icon={<Link2 size={17} />} value={connected ? 'Connected' : 'Disconnected'} sub="Simulated Earth connection" accent={connected ? 'op-accent-green' : 'op-accent-amber'} />
      <ValueCard label="ONE-WAY DELAY" icon={<Clock3 size={17} />} value={Number.isFinite(delay) ? <>{delay.toFixed(1)} <small>min</small></> : '—'} sub="Default simulation: 18.7 min" />
      <ValueCard label="ROUND TRIP" icon={<Activity size={17} />} value={Number.isFinite(delay) ? <>{(delay * 2).toFixed(1)} <small>min</small></> : '—'} sub="Theoretical; no Earth reply is simulated" />
      <ValueCard label="QUEUED MESSAGES" icon={<Send size={17} />} value={(messages.loading && !messages.rows.length) || messages.error ? '—' : counts('queued')} sub={`Last sync: ${stamp(settings.last_sync)}`} />
    </div>
    <Banner error={action.error} success={action.success} />
    <div className="op-link-bar"><div><div className="op-kicker">LOCAL-FIRST COMMUNICATIONS</div><strong>{connected ? 'Earth link available' : 'Earth link disconnected'}</strong><p>Messages persist locally. {settings.message_scheduler_enabled ? 'The backend advances simulated traffic while running and connected; delivery may occur shortly after its due time.' : 'Automatic simulation is disabled; sync or refresh processes eligible traffic.'} The queue refreshes every 5 seconds while this view is visible. Acknowledgment is a local record only, not an Earth reply.{!canConfigure && ' Connection settings require a Commander or Administrator.'}</p></div><div className="op-actions"><Button icon={connected ? <WifiOff size={15} /> : <Link2 size={15} />} onClick={() => void toggle()} disabled={!!action.pending || !canConfigure} title={canConfigure ? undefined : 'Only a Commander or Administrator can change mission settings.'}>{connected ? 'Disconnect' : 'Connect'}</Button><Button className="op-button-primary" icon={<RefreshCw size={15} />} onClick={() => void synchronize()} disabled={!!action.pending}>{action.pending === 'sync' ? 'Syncing…' : 'Sync now'}</Button></div></div>
    {syncResult !== null && <section className="op-sync-result"><div className="op-kicker">LAST SYNCHRONIZATION OUTCOME</div><p>Server-reported result, including any duplicates or conflicts:</p><pre>{json(syncResult)}</pre></section>}
    <div className="op-columns op-columns-wide">
      <Panel title="Message queue" subtitle="Local message history and delivery state" icon={<Radio size={17} />} aside={<label className="op-sr-label">Filter messages<select aria-label="Filter messages by status" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">All statuses</option>{['queued', 'transmitted', 'delivered', 'acknowledged', 'failed'].map(s => <option key={s} value={s}>{humanize(s)}</option>)}</select></label>}>
        <div className="op-status-strip">{['queued', 'transmitted', 'delivered', 'acknowledged', 'failed'].map(s => <div key={s}><b>{(messages.loading && !messages.rows.length) || messages.error ? '—' : counts(s)}</b><span>{s}</span></div>)}</div>
        <LoadingState loading={messages.loading} error={messages.error} count={shown.length} empty={filter === 'all' ? 'No messages yet. Compose a message to add it to the queue.' : 'No messages match this status.'} />
        {!messages.error && <div className="op-record-list">{shown.map(message => {
          const status = String(message.status || '').toLowerCase()
          return <article className="op-record" key={message.id}><div className="op-record-heading"><div><div className="op-record-meta">{humanize(message.direction)} <span>•</span> {humanize(message.priority)} priority</div><h3>{message.content_pending ? 'Incoming content withheld until delivery' : (message.subject || 'Untitled message')}</h3></div><Badge value={message.status} /></div><p className="op-record-body">{message.content_pending ? 'Simulated incoming content is not available to the crew before delivery.' : message.body}</p><div className="op-time-grid"><div><span>CREATED</span><b>{stamp(message.created_at)}</b></div><div><span>TRANSMITTED</span><b>{stamp(message.transmitted_at)}</b></div><div><span>DUE</span><b>{stamp(message.due_at)}</b></div><div><span>DELIVERED</span><b>{stamp(message.delivered_at)}</b></div>{message.acknowledged_at && <div><span>LOCAL RECORD ACKNOWLEDGED</span><b>{stamp(message.acknowledged_at)}</b></div>}{message.failed_at && <div><span>FAILED</span><b>{stamp(message.failed_at)}</b></div>}</div>{message.failure_reason && <DetailLine label="Failure reason">{message.failure_reason}</DetailLine>}{Array.isArray(message.history) && <details className="op-details"><summary>Transmission history <ChevronRight size={14} /></summary><pre className="op-code">{json(message.history)}</pre></details>}<div className="op-record-actions">{status === 'failed' && <Button icon={<RefreshCw size={14} />} disabled={!!action.pending} onClick={() => void operate(message.id, 'retry')}>Retry</Button>}{(status === 'queued' || status === 'transmitted') && <Button icon={<AlertCircle size={14} />} disabled={!!action.pending} onClick={() => void operate(message.id, 'fail')}>Mark failed</Button>}{status === 'delivered' && <Button icon={<Check size={14} />} disabled={!!action.pending} onClick={() => void operate(message.id, 'acknowledge')}>Acknowledge local record</Button>}</div></article>
        })}</div>}
      </Panel>
      <Panel title="Compose or simulate traffic" subtitle="Enter outgoing traffic or simulate an Earth-origin message" icon={<Send size={17} />}><form className="op-form" onSubmit={onCompose}><label>Simulation direction<select name="direction" defaultValue="outgoing"><option value="outgoing">Outgoing to Earth</option><option value="incoming">Simulate incoming from Earth</option></select></label><label>Subject<input name="subject" required maxLength={200} placeholder="Enter message subject" /></label><label>Priority<select name="priority" defaultValue="normal"><option value="normal">Normal</option><option value="high">High</option><option value="urgent">Urgent</option></select></label><label>Message<textarea name="body" required rows={7} placeholder="Enter simulated message content…" /></label><p className="op-form-hint"><Clock3 size={14} /> Simulation input is entered here; incoming content is withheld from the queue and exports until delivery. No real message is transmitted. After an error, retry unchanged to reuse the same key.</p><Button type="submit" className="op-button-primary op-button-block" icon={<Send size={15} />} disabled={!!action.pending}>{action.pending === 'compose' ? 'Saving…' : 'Queue simulated message'}</Button><Button type="button" disabled={!!action.pending} onClick={() => clearPending(user.id, '/api/messages')}>Clear pending retry after checking history</Button></form></Panel>
    </div>
  </div>
}

export function MedicalView({ user, data, refresh }: ViewProps) {
  const allowed = ['Medical Officer', 'Administrator'].includes(user.role)
  const records = useCollection('/api/medical-records', allowed)
  const medications = useCollection('/api/medications', allowed)
  const administrations = useCollection('/api/administrations', allowed)
  const crew: Row[] = (data as any).crew || []
  const [tab, setTab] = useState<'records' | 'inventory' | 'history'>('records')
  const action = useAction(async () => { await Promise.all([records.reload(), medications.reload(), administrations.reload()]); await refresh() })
  const addRecord = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = event.currentTarget; const f = formData(event)
    if (await action.run('record', () => post('/api/medical-records', { crew_id: field(f, 'crew_id'), category: field(f, 'category'), note: field(f, 'note'), follow_up: field(f, 'follow_up') }), 'Medical record added.')) form.reset()
  }
  const editRecord = (event: FormEvent<HTMLFormElement>, id: unknown) => { event.preventDefault(); const f = formData(event); void action.run(`edit-${id}`, () => patch(`/api/medical-records/${idPath(id)}`, { category: field(f, 'category'), note: field(f, 'note'), follow_up: field(f, 'follow_up') }), 'Medical record updated; the server retains revision history.') }
  const addMedication = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = event.currentTarget; const f = formData(event)
    if (await action.run('medication', () => post('/api/medications', { name: field(f, 'name'), quantity: Number(field(f, 'quantity')), unit: field(f, 'unit'), expiry_date: field(f, 'expiry_date'), storage: field(f, 'storage'), low_stock_threshold: Number(field(f, 'low_stock_threshold')) }), 'Medication added to inventory.')) form.reset()
  }
  const editMedication = (event: FormEvent<HTMLFormElement>, id: unknown) => { event.preventDefault(); const f = formData(event); void action.run(`stock-${id}`, () => patch(`/api/medications/${idPath(id)}`, { name: field(f, 'name'), quantity: Number(field(f, 'quantity')), unit: field(f, 'unit'), expiry_date: field(f, 'expiry_date'), storage: field(f, 'storage'), low_stock_threshold: Number(field(f, 'low_stock_threshold')) }), 'Inventory updated.') }
  const administer = async (event: FormEvent<HTMLFormElement>, id: unknown) => { event.preventDefault(); const form = event.currentTarget; const f = formData(event); if (await action.run(`admin-${id}`, () => idempotentPost(user.id, `/api/medications/${idPath(id)}/administer`, { crew_id: field(f, 'crew_id'), quantity: Number(field(f, 'quantity')), note: field(f, 'note') }), 'Administration recorded; inventory was updated by the server.')) form.reset() }
  return <div className="op-view"><Heading eyebrow="CREW CARE / MEDICAL" title="Medical" description="Human-entered crew records, medication inventory and administration history." icon={<Activity size={15} />} />
    <div className="op-notice"><ShieldCheck size={18} /><div><strong>Simulation only · human review required</strong><p>This is not a medical device or approved clinical guidance. Diagnoses are entered by authorized personnel, never automatically inferred.</p></div></div>
    {!allowed ? <div className="op-access"><LockKeyhole size={23} /><h2>Restricted medical information</h2><p>Medical records and medication management are available to Medical Officers and Administrators. Your current role is {user.role}.</p></div> : <>
      <Banner error={action.error} success={action.success} />
      <EvidencePanel />
      <div className="op-tabs" role="group" aria-label="Medical sections">{([['records', 'Medical records'], ['inventory', 'Medication inventory'], ['history', 'Administration history']] as const).map(([key, text]) => <button key={key} aria-pressed={tab === key} onClick={() => setTab(key)}>{text}</button>)}</div>
      {tab === 'records' && <div className="op-columns op-columns-wide"><Panel title="Crew medical records" subtitle="Notes, follow-up and recorded revisions" icon={<FileText size={17} />} aside={<span className="op-count">{records.rows.length} RECORDS</span>}><LoadingState loading={records.loading} error={records.error} count={records.rows.length} empty="No medical records have been entered." />{!records.error && <div className="op-record-list">{records.rows.slice().reverse().map(record => <article className="op-record" key={record.id}><div className="op-record-heading"><div><div className="op-record-meta">{crewName(crew, record.crew_id)} <span>•</span> {humanize(record.category)}</div><h3>{humanize(record.category)} record</h3></div><Badge value="Human entered" /></div><p className="op-record-body">{record.note || 'No note recorded.'}</p><DetailLine label="Follow-up">{record.follow_up || 'None recorded'}</DetailLine><DetailLine label="Created by / at">{actorName(record.creator || record.created_by || record.actor)} · {stamp(record.created_at)}</DetailLine><DetailLine label="Last updated">{stamp(record.updated_at)}</DetailLine><details className="op-details"><summary>Edit record <ChevronRight size={14} /></summary><form className="op-form op-inset-form" onSubmit={e => editRecord(e, record.id)}><label>Category<input name="category" defaultValue={record.category} required /></label><label>Note<textarea name="note" defaultValue={record.note} required rows={4} /></label><label>Follow-up<textarea name="follow_up" defaultValue={record.follow_up || ''} rows={2} /></label><Button type="submit" disabled={!!action.pending} className="op-button-primary">Save changes</Button></form></details>{(record.history || record.revisions) && <details className="op-details"><summary>Revision history <ChevronRight size={14} /></summary><pre className="op-code">{json(record.history || record.revisions)}</pre></details>}</article>)}</div>}</Panel>
        <Panel title="Add record" subtitle="Create an operator-entered clinical note" icon={<Plus size={17} />}><form className="op-form" onSubmit={addRecord}><label>Crew member<CrewSelect crew={crew} required /></label><label>Category<select name="category" defaultValue="observation"><option value="observation">Observation</option><option value="diagnosis">Diagnosis (operator-entered)</option><option value="examination">Examination</option><option value="treatment">Treatment</option><option value="incident">Incident</option><option value="follow-up">Follow-up</option></select></label><label>Note<textarea name="note" rows={6} required placeholder="Enter observation or record details" /></label><label>Follow-up<textarea name="follow_up" rows={3} placeholder="Optional follow-up" /></label><Button type="submit" className="op-button-primary op-button-block" disabled={!!action.pending} icon={<Plus size={15} />}>Add medical record</Button></form></Panel></div>}
      {tab === 'inventory' && <div className="op-columns op-columns-wide"><Panel title="Medication inventory" subtitle="Review stock and storage; expiry warnings use the configured mission lead time and UTC dates" icon={<Package size={17} />} aside={<span className="op-count">{medications.rows.length} ITEMS</span>}><LoadingState loading={medications.loading} error={medications.error} count={medications.rows.length} empty="No medications have been added to inventory." />{!medications.error && <div className="op-record-list">{medications.rows.map(med => {
        const isLow = med.low_stock ?? (Number(med.quantity) <= Number(med.low_stock_threshold))
        return <article className="op-record" key={med.id}><div className="op-record-heading"><div><div className="op-record-meta">MEDICATION INVENTORY</div><h3>{med.name}</h3></div><div className="op-badge-group">{isLow && <Badge value="Low stock" />}{med.expired && <Badge value="Expired" />}{med.expiring_soon && <Badge value="Expiry approaching" />}</div></div><div className="op-stock-amount">{med.quantity} <small>{med.unit}</small></div><DetailLine label="Expiry">{med.expiry_date || 'Not recorded'}</DetailLine><DetailLine label="Expiry warning lead">{med.expiry_warning_days} days (UTC)</DetailLine><DetailLine label="Storage">{med.storage || 'Not recorded'}</DetailLine><DetailLine label="Low-stock threshold">{med.low_stock_threshold} {med.unit}</DetailLine><details className="op-details"><summary>Record administration <ChevronRight size={14} /></summary><p className="op-form-hint">A separate immutable administration record is created and stock is reduced atomically. If the result is uncertain, retry unchanged with the same key; check administration history before clearing a pending retry.</p><form className="op-form op-inset-form" onSubmit={e => void administer(e, med.id)}><label>Crew member<CrewSelect crew={crew} required /></label><label>Quantity ({med.unit})<input name="quantity" type="number" min="0.001" step="any" required /></label><label>Administration note<textarea name="note" rows={2} /></label><Button type="submit" className="op-button-primary" disabled={!!action.pending}>Record administration</Button><Button type="button" disabled={!!action.pending} onClick={() => clearPending(user.id, `/api/medications/${idPath(med.id)}/administer`)}>Clear pending retry after checking history</Button></form></details><details className="op-details"><summary>Edit inventory <ChevronRight size={14} /></summary><MedicationFields med={med} onSubmit={e => editMedication(e, med.id)} disabled={!!action.pending} submitLabel="Save inventory" /></details></article>
      })}</div>}</Panel><Panel title="Add medication" subtitle="Enter inventory details" icon={<Plus size={17} />}><MedicationFields onSubmit={addMedication} disabled={!!action.pending} submitLabel="Add medication" /></Panel></div>}
      {tab === 'history' && <Panel title="Administration history" subtitle="Append-only records of medication administered" icon={<History size={17} />}><LoadingState loading={administrations.loading} error={administrations.error} count={administrations.rows.length} empty="No medication administrations have been recorded." />{!administrations.error && <div className="op-record-list">{administrations.rows.slice().reverse().map(item => <article className="op-record" key={item.id}><div className="op-record-heading"><div><div className="op-record-meta">{crewName(crew, item.crew_id)} <span>•</span> {stamp(item.created_at || item.timestamp)}</div><h3>{item.medication_name || medications.rows.find(m => String(m.id) === String(item.medication_id))?.name || 'Medication administration'}</h3></div><Badge value="Recorded" /></div><DetailLine label="Quantity">{item.quantity} {item.unit || ''}</DetailLine><DetailLine label="Responsible operator">{actorName(item.actor || item.administered_by || item.created_by)}</DetailLine><DetailLine label="Note">{item.note || 'None recorded'}</DetailLine></article>)}</div>}</Panel>}
    </>}
  </div>
}
function MedicationFields({ med = {}, onSubmit, disabled, submitLabel }: { med?: Row; onSubmit: (event: FormEvent<HTMLFormElement>) => void; disabled: boolean; submitLabel: string }) { return <form className="op-form op-inset-form" onSubmit={onSubmit}><label>Medication name<input name="name" required defaultValue={med.name || ''} /></label><div className="op-form-row"><label>Quantity<input name="quantity" type="number" min="0" step="any" required defaultValue={med.quantity ?? ''} /></label><label>Unit<input name="unit" required defaultValue={med.unit || ''} placeholder="e.g. tablets" /></label></div><label>Expiry date<input name="expiry_date" type="date" required defaultValue={med.expiry_date || ''} /></label><label>Storage requirements<input name="storage" defaultValue={med.storage || ''} /></label><label>Low-stock threshold<input name="low_stock_threshold" type="number" min="0" step="any" required defaultValue={med.low_stock_threshold ?? '0'} /></label><Button type="submit" className="op-button-primary" disabled={disabled}>{submitLabel}</Button></form> }

export function EmergencyView({ user, data, refresh }: ViewProps) {
  const incidents = useCollection('/api/incidents')
  const crew: Row[] = (data as any).crew || []
  const action = useAction(async () => { await incidents.reload(); await refresh() })
  const create = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const form = event.currentTarget; const f = formData(event); if (await action.run('create', () => post('/api/incidents', { title: field(f, 'title'), severity: field(f, 'severity'), crew_id: field(f, 'crew_id'), assigned_to: field(f, 'assigned_to'), sensitivity: field(f, 'sensitivity'), report: '' }), 'Incident created.')) form.reset() }
  const changeStatus = (id: unknown, status: string) => action.run(`${status}-${id}`, () => patch(`/api/incidents/${idPath(id)}`, { status }), `Incident marked ${status}.`)
  return <div className="op-view"><Heading eyebrow="MISSION OPERATIONS / EMERGENCY" title="Emergency management" description="Track incidents, assign responsibility and document response actions." icon={<Siren size={15} />} />
    <div className="op-notice op-notice-warning"><AlertCircle size={18} /><div><strong>Demonstration checklists are not approved procedures</strong><p>Operational incidents are visible to all roles and exports: never put medical details in their title, checklist or report. Medical incidents are restricted to Medical Officers and Administrators. Existing unclassified incidents are restricted. Checklists are not approved procedures.</p></div></div>
    <Banner error={action.error} success={action.success} />
    <div className="op-columns op-columns-wide"><Panel title="Incident register" subtitle="Open incidents and documented response history" icon={<ClipboardList size={17} />} aside={<span className="op-count">{incidents.rows.length} INCIDENTS</span>}><LoadingState loading={incidents.loading} error={incidents.error} count={incidents.rows.length} empty="No incidents have been recorded." />{!incidents.error && <div className="op-record-list">{incidents.rows.slice().reverse().map(item => <IncidentCard key={item.id} item={item} crew={crew} pending={!!action.pending} onStatus={status => void changeStatus(item.id, status)} onSave={(checklist, report) => void action.run(`edit-${item.id}`, () => patch(`/api/incidents/${idPath(item.id)}`, { checklist, report }), 'Incident checklist and report saved.')} />)}</div>}</Panel>
      <Panel title="Create incident" subtitle="Log a new event for human review" icon={<Plus size={17} />}><form className="op-form" onSubmit={create}><label>Sensitivity<select name="sensitivity" defaultValue={(['Medical Officer', 'Administrator'].includes(user.role) ? 'medical' : 'operational')}><option value="operational">Operational only — visible to all roles</option>{['Medical Officer', 'Administrator'].includes(user.role) && <option value="medical">Medical — restricted</option>}</select></label><label>Incident title<input name="title" required placeholder="Brief incident description" /></label><label>Severity<select name="severity" defaultValue="WARNING"><option value="INFORMATIONAL">Informational</option><option value="WARNING">Warning</option><option value="CRITICAL">Critical</option></select></label><label>Associated crew member<CrewSelect crew={crew} empty="No crew member assigned" /></label><label>Responsible operator<input name="assigned_to" placeholder="Name or operator ID" /></label><Button type="submit" className="op-button-primary op-button-block" icon={<Plus size={15} />} disabled={!!action.pending}>Create incident</Button></form></Panel>
    </div></div>
}
function IncidentCard({ item, crew, pending, onStatus, onSave }: { item: Row; crew: Row[]; pending: boolean; onStatus: (status: string) => void; onSave: (checklist: ChecklistItem[], report: string) => void }) {
  const [checklist, setChecklist] = useState<ChecklistItem[]>(Array.isArray(item.checklist) ? item.checklist.map((v: Row) => ({ text: String(v.text || ''), done: Boolean(v.done) })) : [])
  const [report, setReport] = useState(String(item.report || ''))
  const [newItem, setNewItem] = useState('')
  useEffect(() => { setChecklist(Array.isArray(item.checklist) ? item.checklist.map((v: Row) => ({ text: String(v.text || ''), done: Boolean(v.done) })) : []); setReport(String(item.report || '')) }, [item.id, item.updated_at])
  const status = String(item.status || '').toLowerCase()
  return <article className="op-record op-incident"><div className="op-record-heading"><div><div className="op-record-meta">OPENED {stamp(item.created_at)}</div><h3>{item.title}</h3></div><div className="op-badge-group"><Badge value={item.sensitivity === 'operational' ? 'Operational only' : 'Medical / restricted'} /><Badge value={item.severity} /><Badge value={item.status} /></div></div><div className="op-time-grid"><div><span>CREW</span><b>{crewName(crew, item.crew_id)}</b></div><div><span>ASSIGNED TO</span><b>{actorName(item.assigned_to) || 'Not assigned'}</b></div><div><span>UPDATED</span><b>{stamp(item.updated_at)}</b></div></div><div className="op-record-actions">{status !== 'acknowledged' && status !== 'resolved' && <Button icon={<Check size={14} />} disabled={pending} onClick={() => onStatus('acknowledged')}>Acknowledge</Button>}{status !== 'resolved' && <Button icon={<CheckCircle2 size={14} />} disabled={pending} onClick={() => onStatus('resolved')}>Resolve</Button>}</div><details className="op-details"><summary>Checklist & post-incident report <ChevronRight size={14} /></summary><div className="op-incident-editor"><p className="op-form-hint">{item.sensitivity === 'operational' ? 'Operational only: visible to all roles and exports. Do not enter medical details. ' : 'Restricted medical incident. '}Generic demonstration checklist only; items are not approved procedures.</p>{checklist.length === 0 && <p className="op-muted">No checklist items recorded.</p>}{checklist.map((entry, index) => <div className="op-check-row" key={index}><input type="checkbox" aria-label={`Mark item ${index + 1} complete`} checked={entry.done} onChange={e => setChecklist(checklist.map((v, i) => i === index ? { ...v, done: e.target.checked } : v))} /><input aria-label={`Checklist item ${index + 1}`} value={entry.text} onChange={e => setChecklist(checklist.map((v, i) => i === index ? { ...v, text: e.target.value } : v))} /><button type="button" className="op-text-button" onClick={() => setChecklist(checklist.filter((_, i) => i !== index))}>Remove</button></div>)}<div className="op-inline-entry"><input aria-label="New checklist item" value={newItem} onChange={e => setNewItem(e.target.value)} placeholder="New checklist item" /><Button onClick={() => { if (newItem.trim()) { setChecklist([...checklist, { text: newItem.trim(), done: false }]); setNewItem('') } }} icon={<Plus size={14} />}>Add</Button></div><label className="op-form-label">{item.sensitivity === 'operational' ? 'Operational-only post-incident report' : 'Restricted post-incident report'}<textarea rows={4} value={report} onChange={e => setReport(e.target.value)} placeholder="Document the incident and review outcome" /></label><Button className="op-button-primary" disabled={pending || checklist.some(v => !v.text.trim())} onClick={() => onSave(checklist, report)}>Save checklist & report</Button></div></details></article>
}

export function LogView({ user }: ViewProps) {
  const events = useCollection('/api/events')
  const isAdmin = user.role === 'Administrator'
  const audit = useCollection('/api/audit', isAdmin)
  const [tab, setTab] = useState<'events' | 'audit'>('events')
  const [exporting, setExporting] = useState('')
  const [exportError, setExportError] = useState('')
  const [exportSuccess, setExportSuccess] = useState('')
  const download = async (format: string) => {
    setExporting(format); setExportError(''); setExportSuccess('')
    try {
      // Export responses are binary/text, so use fetch with the same bearer token as the JSON API client.
      const token = sessionStorage.getItem(TOKEN_KEY)
      if (!token) throw new Error('Your session token is unavailable. Please sign in again.')
      const response = await fetch(`/api/export/${format}`, { headers: { Authorization: `Bearer ${token}` } })
      if (!response.ok) { if (response.status === 401) expireSession(token); let message = `Export failed (${response.status}).`; try { const body = await response.json(); message = body.error || message } catch { /* response was not JSON */ } throw new Error(message) }
      const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a')
      link.href = url; link.download = `checkpoint-export.${format}`; document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000)
      setExportSuccess(`${format.toUpperCase()} export downloaded.`)
    } catch (e) { setExportError(errText(e)) } finally { setExporting('') }
  }
  return <div className="op-view"><Heading eyebrow="MISSION OPERATIONS / LOG" title="Mission log" description="Review the chronological mission record and export authorized reports." icon={<History size={15} />} actions={<div className="op-actions">{['json', 'csv', 'pdf'].map(format => <Button key={format} icon={<ArrowDownToLine size={15} />} disabled={!!exporting} onClick={() => void download(format)}>{exporting === format ? 'Exporting…' : format.toUpperCase()}</Button>)}</div>} />
    <Banner error={exportError} success={exportSuccess} /><div className="op-export-note"><LockKeyhole size={15} /> Exports use your authenticated session and are subject to server authorization. Export activity is logged.</div>
    <div className="op-tabs" role="group" aria-label="Log sections"><button aria-pressed={tab === 'events'} onClick={() => setTab('events')}>Mission events</button>{isAdmin && <button aria-pressed={tab === 'audit'} onClick={() => setTab('audit')}>Admin audit</button>}</div>
    {tab === 'events' ? <Panel title="Mission event timeline" subtitle="Chronological operational activity" icon={<Activity size={17} />} aside={<span className="op-count">{events.rows.length} EVENTS</span>}><LoadingState loading={events.loading} error={events.error} count={events.rows.length} empty="No mission events recorded." />{!events.error && <div className="op-timeline">{events.rows.slice().sort((a, b) => String(b.timestamp || b.created_at || '').localeCompare(String(a.timestamp || a.created_at || ''))).map(event => <article className="op-timeline-item" key={event.id}><div className="op-timeline-dot" /><div className="op-timeline-top"><Badge value={event.category} /><time>{stamp(event.timestamp || event.created_at)}</time></div><h3>{humanize(event.entity)}</h3><p>{event.detail}</p></article>)}</div>}</Panel> : <Panel title="Administrative audit" subtitle="Server-recorded actions, actors and outcomes" icon={<ShieldCheck size={17} />} aside={<span className="op-count">{audit.rows.length} ENTRIES</span>}><LoadingState loading={audit.loading} error={audit.error} count={audit.rows.length} empty="No audit records available." />{!audit.error && <div className="op-record-list">{audit.rows.slice().sort((a, b) => String(b.timestamp || b.created_at || '').localeCompare(String(a.timestamp || a.created_at || ''))).map(row => <article className="op-record" key={row.id}><div className="op-record-heading"><div><div className="op-record-meta">{stamp(row.timestamp || row.created_at)} <span>•</span> {actorName(row.actor || row.user || row.username)}</div><h3>{humanize(row.action)}</h3></div><Badge value={row.outcome || 'recorded'} /></div><DetailLine label="Record">{humanize(row.entity)} {row.entity_id ? `· ${row.entity_id}` : ''}</DetailLine>{(row.before || row.after || row.old_value || row.new_value) && <details className="op-details"><summary>Recorded changes <ChevronRight size={14} /></summary><pre className="op-code">{json({ before: row.before ?? row.old_value, after: row.after ?? row.new_value })}</pre></details>}</article>)}</div>}</Panel>}
  </div>
}

export function SettingsView({ user, data, refresh }: ViewProps) {
  const isAdmin = user.role === 'Administrator'
  const canConfigure = isAdmin || user.role === 'Commander'
  const users = useCollection('/api/users', isAdmin)
  const settings: Row = (data as any).settings || (data as any).mission || {}
  const thresholds: Row = settings.thresholds && typeof settings.thresholds === 'object' ? settings.thresholds : {}
  const action = useAction(async () => { if (isAdmin) await users.reload(); await refresh() })
  const updateMission = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const f = formData(event); void action.run('settings', () => patch('/api/settings', { mission_day: Number(field(f, 'mission_day')), location: field(f, 'location'), delay_minutes: Number(field(f, 'delay_minutes')), earth_connected: field(f, 'earth_connected') === 'connected', retention_days: Number(field(f, 'retention_days')), expiry_warning_days: Number(field(f, 'expiry_warning_days')) }), 'Mission settings saved.') }
  const updateThreshold = (event: FormEvent<HTMLFormElement>, metric: string) => { event.preventDefault(); const f = formData(event); const value: Row = { ...thresholds[metric] }; for (const k of ['min', 'max', 'critical_min', 'critical_max']) { const text = field(f, k); value[k] = text === '' ? null : Number(text) } value.unit = field(f, 'unit'); void action.run(`threshold-${metric}`, () => patch('/api/settings', { thresholds: { ...thresholds, [metric]: value } }), `Thresholds for ${humanize(metric)} saved.`) }
  const addUser = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const form = event.currentTarget; const f = formData(event); if (await action.run('user', () => post('/api/users', { username: field(f, 'username'), display_name: field(f, 'display_name'), role: field(f, 'role'), password: String(f.get('password') || '') }), 'User created.')) form.reset() }
  const editUser = (event: FormEvent<HTMLFormElement>, id: unknown) => { event.preventDefault(); const form = event.currentTarget; const f = formData(event); const values: Row = { display_name: field(f, 'display_name'), role: field(f, 'role'), active: f.get('active') === 'on' }; const password = String(f.get('password') || ''); if (password) values.password = password; void action.run(`user-${id}`, () => patch(`/api/users/${idPath(id)}`, values), 'User account updated.').then(ok => { if (ok) { const passwordField = form.elements.namedItem('password') as HTMLInputElement | null; if (passwordField) passwordField.value = '' } }) }
  return <div className="op-view"><Heading eyebrow="SYSTEM / CONFIGURATION" title="Settings" description="Manage mission parameters, operating thresholds and local access." icon={<Settings2 size={15} />} />
    <div className="op-notice"><LockKeyhole size={18} /><div><strong>Local prototype security</strong><p>Passwords are stored as hashes and sessions expire. This prototype does not guarantee encryption at rest, hardened deployment or production-grade physical security. Do not enter real personnel or health information.</p></div></div>
    <Banner error={action.error} success={action.success} />
    <div className="op-columns op-columns-even"><Panel title="Mission configuration" subtitle="Simulation timing and local data settings" icon={<Globe2 size={17} />}><form key={json(settings)} className="op-form" onSubmit={updateMission}><div className="op-form-row"><label>Mission day<input name="mission_day" type="number" min="0" required defaultValue={settings.mission_day ?? ''} disabled={!canConfigure} /></label><label>Location<input name="location" required defaultValue={settings.location ?? ''} disabled={!canConfigure} /></label></div><div className="op-form-row"><label>One-way delay (minutes)<input name="delay_minutes" type="number" min="0" step="any" required defaultValue={settings.delay_minutes ?? ''} disabled={!canConfigure} /></label><label>Retention (days)<input name="retention_days" type="number" min="1" step="1" required defaultValue={settings.retention_days ?? ''} disabled={!canConfigure} /></label></div><label>Medication expiry warning lead (days)<input name="expiry_warning_days" type="number" min="0" max="36500" step="1" required defaultValue={settings.expiry_warning_days ?? ''} disabled={!canConfigure} /><span className="op-form-hint">Demonstration only: warn on the expiry date and up to this many UTC calendar days beforehand. 0 means expiry date only.</span></label><label>Simulated Earth link<select name="earth_connected" defaultValue={settings.earth_connected ? 'connected' : 'disconnected'} disabled={!canConfigure}><option value="connected">Connected</option><option value="disconnected">Disconnected</option></select></label><p className="op-form-hint">Last synchronization: {stamp(settings.last_sync)}. Retention settings do not authorize ordinary deletion of critical medical or audit records.</p>{canConfigure ? <Button type="submit" className="op-button-primary" disabled={!!action.pending}>Save mission settings</Button> : <p className="op-muted">Only a Commander or Administrator can change mission settings; the server enforces access.</p>}</form></Panel>
      <Panel title="Access & roles" subtitle="Your current local session" icon={<UserRound size={17} />}><div className="op-profile"><div className="op-avatar">{String(user.display_name || user.username || '?').slice(0, 1).toUpperCase()}</div><div><h3>{user.display_name || user.username}</h3><p>@{user.username}</p></div><Badge value={user.role} /></div><div className="op-role-list"><DetailLine label="Authentication">Local password · expiring session</DetailLine><DetailLine label="Authorization">Server-enforced role permissions</DetailLine><DetailLine label="Account management">Administrator only</DetailLine></div></Panel></div>
    <Panel title="Alert thresholds" subtitle="Configured rules are simulation values, not clinical standards" icon={<Activity size={17} />} aside={<span className="op-count">{Object.keys(thresholds).length} RULES</span>}>{!Object.keys(thresholds).length ? <div className="op-empty">No threshold rules are available in mission settings.</div> : <div className="op-threshold-list">{Object.entries(thresholds).map(([metric, rule]) => { const r = (rule || {}) as Row; return <details className="op-details op-threshold" key={metric}><summary><span>{humanize(metric)} <small>{r.unit || ''}</small></span><ChevronRight size={14} /></summary><form key={json(r)} className="op-form op-inset-form" onSubmit={e => updateThreshold(e, metric)}><div className="op-form-row op-form-row-four">{(['min', 'max', 'critical_min', 'critical_max'] as const).map(k => <label key={k}>{humanize(k)}<input name={k} type="number" step="any" required defaultValue={r[k] ?? ''} disabled={!canConfigure} /></label>)}</div><label>Unit<input name="unit" defaultValue={r.unit || ''} readOnly /></label><p className="op-form-hint">Required ordering: critical minimum ≤ minimum ≤ maximum ≤ critical maximum. Units must match the metric.</p>{canConfigure && <Button type="submit" className="op-button-primary" disabled={!!action.pending}>Save rule</Button>}</form></details> })}</div>}</Panel>
    {isAdmin && <div className="op-columns op-columns-wide"><Panel title="Local users" subtitle="Role assignment and password management" icon={<UserRound size={17} />} aside={<span className="op-count">{users.rows.length} USERS</span>}><LoadingState loading={users.loading} error={users.error} count={users.rows.length} empty="No users available." />{!users.error && <div className="op-record-list">{users.rows.map(account => <article className="op-record" key={account.id}><div className="op-record-heading"><div><h3>{account.display_name || account.username}</h3><div className="op-record-meta">@{account.username}</div></div><div className="op-badge-group"><Badge value={account.role} /><Badge value={account.active ? 'Active' : 'Inactive'} /></div></div><details className="op-details"><summary>Manage account <ChevronRight size={14} /></summary><form className="op-form op-inset-form" onSubmit={e => editUser(e, account.id)}><label>Display name<input name="display_name" required defaultValue={account.display_name || account.username} /></label><label>Role<select name="role" defaultValue={account.role}>{roles.map(role => <option key={role}>{role}</option>)}</select></label><label className="op-check-label"><input name="active" type="checkbox" defaultChecked={Boolean(account.active)} /> Account active</label><label>New password<input name="password" type="password" minLength={12} maxLength={256} autoComplete="new-password" placeholder="Leave blank to keep current password" /><span className="op-form-hint">Use a unique passphrase. The demo password, a limited set of common values, repeated single characters and the current password are rejected; this is not a comprehensive check for common or compromised passwords.</span></label><Button type="submit" className="op-button-primary" disabled={!!action.pending}>Save account</Button></form></details></article>)}</div>}</Panel><Panel title="Add user" subtitle="Create a local prototype account" icon={<Plus size={17} />}><form className="op-form" onSubmit={addUser}><label>Username<input name="username" required autoComplete="off" /></label><label>Display name<input name="display_name" required /></label><label>Role<select name="role" defaultValue="Mission Operator">{roles.map(role => <option key={role}>{role}</option>)}</select></label><label>Temporary password<input name="password" type="password" minLength={12} maxLength={256} required autoComplete="new-password" /><span className="op-form-hint">12–256 characters. Use a unique passphrase without leading or trailing whitespace or control characters. The demo password, a limited set of common values and repeated single characters are rejected; this is not a comprehensive check for common or compromised passwords. Share temporary credentials securely.</span></label><Button type="submit" className="op-button-primary op-button-block" disabled={!!action.pending} icon={<Plus size={15} />}>Create user</Button></form></Panel></div>}
  </div>
}
