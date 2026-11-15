import { useEffect, useId, useState, type FormEvent } from 'react'
import { api } from '../lib'
import '../evidence.css'

type EvidenceEntry = {
  id: string
  title: string
  source_title: string
  page: number
  page_end: number
  region: string
  verification_status: string
  excerpt: string
}

const PAGE_SIZE = 20

/** An index of unverified source headings; no clinical page body is displayed. */
export function EvidencePanel() {
  const id = useId()
  const [searchInput, setSearchInput] = useState('')
  const [regionInput, setRegionInput] = useState('')
  const [query, setQuery] = useState('')
  const [region, setRegion] = useState('')
  const [offset, setOffset] = useState(0)
  const [retry, setRetry] = useState(0)
  const [rows, setRows] = useState<EvidenceEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams({ limit: String(PAGE_SIZE + 1), offset: String(offset) })
    if (query) params.set('q', query)
    if (region) params.set('region', region)
    setLoading(true)
    setError('')
    setRows([])
    api<EvidenceEntry[]>(`/api/evidence?${params}`, { signal: controller.signal })
      .then(result => {
        if (controller.signal.aborted) return
        if (!Array.isArray(result)) throw new Error('The server returned an unexpected evidence response.')
        setRows(result)
      })
      .catch(err => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Unable to load source evidence.')
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [query, region, offset, retry])

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setQuery(searchInput.trim())
    setRegion(regionInput.trim())
    setOffset(0)
  }

  const visible = rows.slice(0, PAGE_SIZE)
  return <section className="evidence-panel" aria-labelledby={`${id}-title`}>
    <div className="evidence-heading">
      <div><div className="evidence-kicker">SOURCE INDEX / REVIEW ONLY</div><h2 id={`${id}-title`}>Source evidence</h2><p>Search extracted headings and their source pages. Extracted titles may be incomplete or contain errors.</p></div>
      <span className="evidence-review-tag">Unverified</span>
    </div>
    <div className="evidence-warning" role="note"><strong>UNVERIFIED SOURCE EXTRACTS — review only, not approved procedures</strong><p>Only source headings and page references are shown; procedural text and medication details are withheld.</p></div>
    <form className="evidence-search" onSubmit={search}>
      <label htmlFor={`${id}-query`}>Search titles or source<input id={`${id}-query`} type="search" value={searchInput} maxLength={120} onChange={event => setSearchInput(event.target.value)} placeholder="Search extracted titles" /></label>
      <label htmlFor={`${id}-region`}>Region (exact)<input id={`${id}-region`} value={regionInput} maxLength={80} onChange={event => setRegionInput(event.target.value)} placeholder="All regions" /></label>
      <button type="submit">Search</button>
    </form>
    <p className="evidence-hint">The supplied catalog labels its entries as unclassified by region.</p>
    {loading && <div className="evidence-state" role="status">Loading source evidence…</div>}
    {!loading && error && <div className="evidence-state evidence-error" role="alert"><span>{error}</span><button type="button" onClick={() => setRetry(value => value + 1)}>Retry</button></div>}
    {!loading && !error && visible.length === 0 && <div className="evidence-state" role="status">No source extracts match this search.</div>}
    {!loading && !error && visible.length > 0 && <>
      <ul className="evidence-results" aria-label="Source evidence results">{visible.map(row => <li className="evidence-entry" key={row.id}>
        <div className="evidence-entry-head"><h3>{row.title}</h3><span className="evidence-entry-status">Unverified · needs clinical review</span></div>
        <p className="evidence-source">{row.source_title} · PDF {row.page_end > row.page ? `pages ${row.page}–${row.page_end}` : `page ${row.page}`}</p>
        <p className="evidence-meta">Region: {row.region}</p>
        {row.excerpt && <p className="evidence-excerpt"><span>Heading extract</span> {row.excerpt}</p>}
      </li>)}</ul>
      <div className="evidence-pagination"><span>Showing {offset + 1}–{offset + visible.length}</span><div><button type="button" disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - PAGE_SIZE))}>Previous</button><button type="button" disabled={rows.length <= PAGE_SIZE} onClick={() => setOffset(value => value + PAGE_SIZE)}>Next</button></div></div>
    </>}
  </section>
}
