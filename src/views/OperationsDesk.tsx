import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { AlertTriangle, ArrowRight, CheckCircle2, ClipboardList, Clock3, Plus, RefreshCw, Search, ShieldAlert } from 'lucide-react';
import { api, type Crew, type ViewProps } from '../lib';
import '../workspace.css';

type TaskStatus = 'open' | 'in-progress' | 'completed';
type Priority = 'low' | 'normal' | 'high' | 'urgent';
type Task = { id: string; title: string; description: string; status: TaskStatus; priority: Priority; assigned_crew_id: string | null; due_date: string | null; created_at: string; updated_at: string };
type Handover = { id: string; content: string; author: string; timestamp: string };
type Filters = { query: string; status: string; priority: string; assigned_crew_id: string };
const PAGE_SIZE = 30;
const statuses: { value: TaskStatus; label: string }[] = [{ value: 'open', label: 'Open' }, { value: 'in-progress', label: 'In progress' }, { value: 'completed', label: 'Completed' }];
const priorities: { value: Priority; label: string }[] = [{ value: 'low', label: 'Low' }, { value: 'normal', label: 'Normal' }, { value: 'high', label: 'High' }, { value: 'urgent', label: 'Urgent' }];
const writeRoles = ['Commander', 'Mission Operator', 'Administrator', 'Medical Officer'];
const emptyFilters: Filters = { query: '', status: '', priority: '', assigned_crew_id: '' };
const errText = (error: unknown) => error instanceof Error ? error.message : 'The local service could not complete the request.';
const stamp = (value: string) => {
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? 'Timestamp unavailable' : `${d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' })} UTC`;
};
const statusLabel = (value: TaskStatus) => statuses.find(item => item.value === value)?.label || value;
const priorityLabel = (value: Priority) => priorities.find(item => item.value === value)?.label || value;
const crewLabel = (crew: Crew[], id: string | null) => id ? crew.find(person => person.id === id)?.name || 'Crew unavailable' : 'Unassigned';
const taskPath = (id: string) => `/workspace/tasks/${encodeURIComponent(id)}`;
const dataFromForm = (form: HTMLFormElement) => {
  const fields = new FormData(form);
  return Object.fromEntries(['title', 'description', 'priority', 'status', 'assigned_crew_id', 'due_date'].map(key => [key, String(fields.get(key) ?? '').trim()]));
};

function TaskFields({ crew, task }: { crew: Crew[]; task?: Task }) {
  return <>
    <label>Task title <span>Required</span><input name="title" required maxLength={200} defaultValue={task?.title || ''} placeholder="e.g. Inspect stowage labels" /></label>
    <label>Description <span>Optional · operational only</span><textarea name="description" rows={3} maxLength={4000} defaultValue={task?.description || ''} placeholder="Add simulated operational details" /></label>
    <div className="wd-fields-row"><label>Priority<select name="priority" defaultValue={task?.priority || 'normal'}>{priorities.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
      <label>Status<select name="status" defaultValue={task?.status || 'open'}>{statuses.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label></div>
    <div className="wd-fields-row"><label>Assigned crew <span>Optional</span><select name="assigned_crew_id" defaultValue={task?.assigned_crew_id || ''}><option value="">Unassigned</option>{task?.assigned_crew_id && !crew.some(person => person.id === task.assigned_crew_id) && <option value={task.assigned_crew_id}>Crew unavailable</option>}{crew.map(person => <option key={person.id} value={person.id}>{person.name}</option>)}</select></label>
      <label>Due date <span>Optional</span><input type="date" name="due_date" defaultValue={task?.due_date || ''} /></label></div>
  </>;
}

export function OperationsDesk({ user, data, refresh }: ViewProps) {
  const canWrite = writeRoles.includes(user.role);
  const crew = data.crew || [];
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [handovers, setHandovers] = useState<Handover[]>([]);
  const [taskMore, setTaskMore] = useState(false);
  const [handoverMore, setHandoverMore] = useState(false);
  const [taskLoading, setTaskLoading] = useState(true);
  const [handoverLoading, setHandoverLoading] = useState(true);
  const [taskError, setTaskError] = useState('');
  const [handoverError, setHandoverError] = useState('');
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState('');
  const [actionError, setActionError] = useState('');
  const [message, setMessage] = useState('');
  const taskEpoch = useRef(0);
  const handoverEpoch = useRef(0);
  const filterKey = JSON.stringify(filters);

  const loadTasks = useCallback(async (offset: number, signal?: AbortSignal) => {
    const rows = await api<Task[]>('/workspace/tasks/search', { method: 'POST', signal, body: JSON.stringify({ ...JSON.parse(filterKey), limit: PAGE_SIZE + 1, offset }) });
    if (!Array.isArray(rows)) throw new Error('The service returned an invalid task list.');
    return rows;
  }, [filterKey]);
  const loadHandovers = useCallback(async (offset: number, signal?: AbortSignal) => {
    const rows = await api<Handover[]>(`/workspace/handovers?limit=${PAGE_SIZE + 1}&offset=${offset}`, { signal });
    if (!Array.isArray(rows)) throw new Error('The service returned an invalid handover list.');
    return rows;
  }, []);

  useEffect(() => {
    const epoch = ++taskEpoch.current; const controller = new AbortController();
    setTaskLoading(true); setTaskError(''); setTasks([]); setTaskMore(false);
    const timer = window.setTimeout(() => {
      void loadTasks(0, controller.signal).then(rows => {
        if (taskEpoch.current === epoch) { setTasks(rows.slice(0, PAGE_SIZE)); setTaskMore(rows.length > PAGE_SIZE); }
      }).catch(error => { if (taskEpoch.current === epoch && !controller.signal.aborted) setTaskError(errText(error)); })
        .finally(() => { if (taskEpoch.current === epoch) setTaskLoading(false); });
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); taskEpoch.current++; };
  }, [loadTasks, reload]);
  useEffect(() => {
    const epoch = ++handoverEpoch.current; const controller = new AbortController();
    setHandoverLoading(true); setHandoverError(''); setHandovers([]); setHandoverMore(false);
    void loadHandovers(0, controller.signal).then(rows => {
      if (handoverEpoch.current === epoch) { setHandovers(rows.slice(0, PAGE_SIZE)); setHandoverMore(rows.length > PAGE_SIZE); }
    }).catch(error => { if (handoverEpoch.current === epoch && !controller.signal.aborted) setHandoverError(errText(error)); })
      .finally(() => { if (handoverEpoch.current === epoch) setHandoverLoading(false); });
    return () => { controller.abort(); handoverEpoch.current++; };
  }, [loadHandovers, reload]);

  async function moreTasks() {
    const epoch = taskEpoch.current; setTaskLoading(true); setTaskError('');
    try { const rows = await loadTasks(tasks.length); if (taskEpoch.current === epoch) { setTasks(old => [...old, ...rows.slice(0, PAGE_SIZE)]); setTaskMore(rows.length > PAGE_SIZE); } }
    catch (error) { if (taskEpoch.current === epoch) setTaskError(errText(error)); }
    finally { if (taskEpoch.current === epoch) setTaskLoading(false); }
  }
  async function moreHandovers() {
    const epoch = handoverEpoch.current; setHandoverLoading(true); setHandoverError('');
    try { const rows = await loadHandovers(handovers.length); if (handoverEpoch.current === epoch) { setHandovers(old => [...old, ...rows.slice(0, PAGE_SIZE)]); setHandoverMore(rows.length > PAGE_SIZE); } }
    catch (error) { if (handoverEpoch.current === epoch) setHandoverError(errText(error)); }
    finally { if (handoverEpoch.current === epoch) setHandoverLoading(false); }
  }
  async function run(key: string, work: () => Promise<unknown>, success: string, after?: () => void) {
    setBusy(key); setActionError(''); setMessage('');
    try {
      await work(); after?.(); setMessage(success); setReload(value => value + 1);
      // Workspace data has its own endpoints; refresh the shell's event feed too.
      void refresh().catch(() => setMessage(`${success} The mission overview could not refresh; use its refresh control.`));
    } catch (error) {
      setActionError(`${errText(error)} If the response was interrupted, refresh and check the list before trying again.`);
    } finally { setBusy(''); }
  }
  function createTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const body = dataFromForm(form);
    void run('task-create', () => api('/workspace/tasks', { method: 'POST', body: JSON.stringify(body) }), 'Task saved locally. Active filters may hide it.', () => form.reset());
  }
  function editTask(event: FormEvent<HTMLFormElement>, task: Task) {
    event.preventDefault(); const body = dataFromForm(event.currentTarget);
    void run(`edit-${task.id}`, () => api(taskPath(task.id), { method: 'PATCH', body: JSON.stringify(body) }), 'Task updated locally.');
  }
  function changeStatus(event: FormEvent<HTMLFormElement>, task: Task) {
    event.preventDefault(); const status = new FormData(event.currentTarget).get('next_status');
    void run(`status-${task.id}`, () => api(taskPath(task.id), { method: 'PATCH', body: JSON.stringify({ status }) }), 'Task status updated locally.');
  }
  function removeTask(task: Task) {
    if (!window.confirm('Delete this task from the local workspace? This cannot be undone.')) return;
    void run(`delete-${task.id}`, () => api(taskPath(task.id), { method: 'DELETE' }), 'Task deleted locally.');
  }
  function createHandover(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const content = String(new FormData(form).get('content') || '').trim();
    void run('handover', () => api('/workspace/handovers', { method: 'POST', body: JSON.stringify({ content }) }), 'Handover entry recorded locally.', () => form.reset());
  }
  const hasFilters = Boolean(filters.query || filters.status || filters.priority || filters.assigned_crew_id);
  return <div className="wd-view">
    <header className="wd-header"><div><div className="wd-eyebrow"><ClipboardList size={15} aria-hidden="true" /> MISSION OPERATIONS / WORKSPACE</div><h1>Operations Desk</h1><p>Coordinate local mission tasks and keep the next shift informed.</p></div><button className="wd-button" type="button" onClick={() => setReload(value => value + 1)} disabled={taskLoading || handoverLoading}><RefreshCw size={15} aria-hidden="true" /> Refresh desk</button></header>
    <div className="wd-notice"><ShieldAlert size={20} aria-hidden="true" /><div><strong>SIMULATION · OPERATIONAL INFORMATION ONLY</strong><p>Visible to all signed-in roles. Do not enter real personal, medical or health information in tasks or handovers. This local workspace is not a clinical record or approved procedure.</p></div></div>
    {actionError && <div className="wd-feedback wd-feedback-error" role="alert">{actionError}</div>}{message && <div className="wd-feedback wd-feedback-success" role="status"><CheckCircle2 size={16} aria-hidden="true" />{message}</div>}
    {!canWrite && <div className="wd-feedback" role="status">Your role has read-only access to this workspace.</div>}
    <div className="wd-main-grid"><section className="wd-panel" aria-labelledby="wd-tasks-heading"><div className="wd-panel-head"><div><div className="wd-kicker">MISSION WORK / 01</div><h2 id="wd-tasks-heading">Task register</h2><p>Search titles and descriptions, or narrow by assignment and status.</p></div><span className="wd-count">{tasks.length} SHOWN</span></div>
      <div className="wd-filters"><label className="wd-search"><span>Search tasks</span><div><Search size={16} aria-hidden="true" /><input type="search" maxLength={200} value={filters.query} onChange={event => setFilters({ ...filters, query: event.target.value })} placeholder="Search operational tasks" /></div></label>
        <div className="wd-filter-row"><label>Status<select value={filters.status} onChange={event => setFilters({ ...filters, status: event.target.value })}><option value="">All statuses</option>{statuses.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label><label>Priority<select value={filters.priority} onChange={event => setFilters({ ...filters, priority: event.target.value })}><option value="">All priorities</option>{priorities.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label><label>Crew<select value={filters.assigned_crew_id} onChange={event => setFilters({ ...filters, assigned_crew_id: event.target.value })}><option value="">All crew</option>{crew.map(person => <option key={person.id} value={person.id}>{person.name}</option>)}</select></label></div>
        {hasFilters && <button className="wd-text-button" type="button" onClick={() => setFilters(emptyFilters)}>Clear filters</button>}</div>
      {taskLoading && <p className="wd-state" role="status">Loading tasks…</p>}{taskError && <div className="wd-state wd-error" role="alert">{taskError} <button type="button" className="wd-text-button" onClick={() => setReload(value => value + 1)}>Retry</button></div>}
      {!taskError && !taskLoading && !tasks.length && <p className="wd-state">{hasFilters ? 'No tasks match these filters. Try a different search or clear filters.' : 'No tasks yet. Create a simulated task to begin.'}</p>}
      {!taskError && <div className="wd-task-list">{tasks.map(task => <article className="wd-task" key={`${task.id}:${task.updated_at}`}><div className="wd-task-top"><div><div className="wd-task-label">{priorityLabel(task.priority)} priority</div><h3>{task.title}</h3></div><span className={`wd-status wd-status-${task.status}`}>{statusLabel(task.status)}</span></div>
        {task.description && <p className="wd-task-description">{task.description}</p>}
        <div className="wd-task-meta"><span><b>ASSIGNED</b>{crewLabel(crew, task.assigned_crew_id)}</span><span><b>DUE DATE</b>{task.due_date || 'Not set'}</span><span><b>UPDATED</b>{stamp(task.updated_at)}</span></div>
        {canWrite && <><form className="wd-status-form" onSubmit={event => changeStatus(event, task)}><label>Change status<select name="next_status" defaultValue={task.status} aria-label={`Change status for ${task.title}`}>{statuses.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label><button className="wd-button wd-button-small" disabled={!!busy} type="submit">Update status</button></form>
          <details className="wd-edit"><summary>Edit task</summary><form className="wd-form wd-edit-form" onSubmit={event => editTask(event, task)}><TaskFields crew={crew} task={task} /><div className="wd-form-actions"><button className="wd-button wd-primary" disabled={!!busy} type="submit">Save changes</button><button className="wd-button wd-delete" disabled={!!busy} type="button" onClick={() => removeTask(task)}>Delete task</button></div></form></details></>}
      </article>)}</div>}
      {!taskError && taskMore && <div className="wd-load-more"><button className="wd-button" type="button" disabled={taskLoading} onClick={() => void moreTasks()}>Load more tasks</button></div>}
    </section>
    <section className="wd-panel wd-create-panel" aria-labelledby="wd-create-heading"><div className="wd-panel-head"><div><div className="wd-kicker">NEW WORK / 02</div><h2 id="wd-create-heading">Create a task</h2><p>A locally saved entry for the mission team.</p></div><Plus size={19} aria-hidden="true" /></div>{canWrite ? <form className="wd-form" onSubmit={createTask}><TaskFields crew={crew} /><p className="wd-hint">Use fictional, non-medical operational details only.</p><button className="wd-button wd-primary wd-full" type="submit" disabled={!!busy}>{busy === 'task-create' ? 'Saving…' : 'Create task'} <ArrowRight size={15} aria-hidden="true" /></button></form> : <p className="wd-state">An authorized operator can create tasks.</p>}</section></div>
    <div className="wd-handover-grid"><section className="wd-panel" aria-labelledby="wd-handover-heading"><div className="wd-panel-head"><div><div className="wd-kicker">SHIFT CHANGE / 03</div><h2 id="wd-handover-heading">Shift handover</h2><p>Locally recorded notes, newest first. Entries cannot be edited or deleted here.</p></div><Clock3 size={19} aria-hidden="true" /></div>
      {handoverLoading && <p className="wd-state" role="status">Loading handovers…</p>}{handoverError && <div className="wd-state wd-error" role="alert">{handoverError} <button type="button" className="wd-text-button" onClick={() => setReload(value => value + 1)}>Retry</button></div>}{!handoverError && !handoverLoading && !handovers.length && <p className="wd-state">No handover entries yet. Add an operational note for the next shift.</p>}
      {!handoverError && <div className="wd-handover-list">{handovers.map(entry => <article className="wd-handover" key={entry.id}><div><strong>{entry.author}</strong><time dateTime={entry.timestamp}>{stamp(entry.timestamp)}</time></div><p>{entry.content}</p></article>)}</div>}{!handoverError && handoverMore && <div className="wd-load-more"><button className="wd-button" type="button" disabled={handoverLoading} onClick={() => void moreHandovers()}>Load more handovers</button></div>}
    </section><section className="wd-panel" aria-labelledby="wd-note-heading"><div className="wd-panel-head"><div><div className="wd-kicker">HANDOVER / 04</div><h2 id="wd-note-heading">Add an entry</h2><p>Author and UTC timestamp are set by the local service.</p></div></div>{canWrite ? <form className="wd-form" onSubmit={createHandover}><label>Operational handover <span>Required</span><textarea name="content" rows={6} required maxLength={4000} placeholder="What should the next shift know?" /></label><p className="wd-hint">Simulated, non-medical information only. Entries are visible to all signed-in roles and are append-only.</p><button className="wd-button wd-primary wd-full" type="submit" disabled={!!busy}>{busy === 'handover' ? 'Saving…' : 'Record handover'} <ArrowRight size={15} aria-hidden="true" /></button></form> : <p className="wd-state">An authorized operator can add handovers.</p>}</section></div>
    <div className="wd-footer"><AlertTriangle size={14} aria-hidden="true" /> LOCAL SIMULATION ONLY · NO EXTERNAL COMMUNICATIONS</div>
  </div>;
}
