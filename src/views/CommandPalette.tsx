import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ArrowRight, Search, X, type LucideIcon } from 'lucide-react';

export type CommandItem = {
  name: string;
  icon: LucideIcon;
  group: string;
  description: string;
  keywords?: string;
  /** Remove when the corresponding view has been integrated. */
  pending?: boolean;
};

type CommandPaletteProps = {
  items: readonly CommandItem[];
  current: string;
  onSelect: (name: string) => void;
  onClose: () => void;
};

/** A navigation-only dialog: opening it never unmounts or submits the current view. */
export function CommandPalette({ items, current, onSelect, onClose }: CommandPaletteProps) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const id = useId();
  const listId = `${id}-list`;
  const matches = useMemo(() => {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return items.filter(item => {
      const text = `${item.name} ${item.group} ${item.description} ${item.keywords || ''}`.toLowerCase();
      return terms.every(term => text.includes(term));
    });
  }, [items, query]);
  const activeIndex = Math.min(active, matches.length - 1);

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    inputRef.current?.focus();
    return () => {
      // The selected module can unmount the element that originally had focus.
      const fallback = document.getElementById('command-palette-trigger');
      if (previous && previous !== document.body && previous.isConnected) previous.focus();
      else if (fallback instanceof HTMLElement) fallback.focus();
    };
  }, []);

  useEffect(() => {
    if (activeIndex >= 0) document.getElementById(`${id}-option-${activeIndex}`)?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex, id, matches]);

  function handleDialogKeyDown(event: React.KeyboardEvent<HTMLElement>) {
    if (event.nativeEvent.isComposing) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('input:not([disabled]), button:not([disabled])') || []);
    if (!controls.length) return;
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && (document.activeElement === first || !dialogRef.current?.contains(document.activeElement))) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !dialogRef.current?.contains(document.activeElement))) {
      event.preventDefault(); first.focus();
    }
  }

  function handleInputKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    // Leave text-editing shortcuts and IME composition to the browser.
    if (event.nativeEvent.isComposing || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (event.key === 'ArrowDown' && matches.length) {
      event.preventDefault(); setActive((activeIndex + 1) % matches.length);
    } else if (event.key === 'ArrowUp' && matches.length) {
      event.preventDefault(); setActive((activeIndex - 1 + matches.length) % matches.length);
    } else if (event.key === 'Enter' && activeIndex >= 0) {
      event.preventDefault(); onSelect(matches[activeIndex].name);
    }
  }

  return <div className="command-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={dialogRef} className="command-dialog" role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} onKeyDown={handleDialogKeyDown}>
      <div className="command-topline"><div><span className="command-topline-mark" aria-hidden="true" /> CHECKPOINT / QUICK ACCESS</div><span>MODULE NAVIGATION</span></div>
      <div className="command-search-row">
        <Search size={20} className="command-search-icon" aria-hidden="true" />
        <div className="command-input-wrap"><label id={`${id}-title`} className="sr-only" htmlFor={`${id}-input`}>Search console modules</label><input ref={inputRef} id={`${id}-input`} role="combobox" aria-autocomplete="list" aria-expanded="true" aria-controls={listId} aria-activedescendant={activeIndex >= 0 ? `${id}-option-${activeIndex}` : undefined} autoComplete="off" spellCheck={false} value={query} placeholder="Search the console..." onChange={event => { setQuery(event.target.value); setActive(0); }} onKeyDown={handleInputKeyDown} /></div>
        <button type="button" className="command-close" onClick={onClose} aria-label="Close command palette"><X size={18} /><span>ESC</span></button>
      </div>
      <div className="command-list-heading"><span>JUMP TO MODULE</span><span>{matches.length} {matches.length === 1 ? 'RESULT' : 'RESULTS'}</span></div>
      <div className="sr-only" role="status" aria-live="polite">{matches.length} {matches.length === 1 ? 'module' : 'modules'} found.</div>
      <ul id={listId} className="command-results" role="listbox" aria-label="Matching modules">
        {matches.map((item, index) => {
          const Icon = item.icon;
          return <li key={item.name} id={`${id}-option-${index}`} role="option" aria-selected={index === activeIndex} className={`command-result ${index === activeIndex ? 'is-highlighted' : ''}`} onMouseEnter={() => setActive(index)} onMouseDown={event => event.preventDefault()} onClick={() => onSelect(item.name)}>
            <span className="command-result-icon"><Icon size={17} strokeWidth={1.8} aria-hidden="true" /></span><span className="command-result-copy"><strong>{item.name}</strong><small>{item.description}</small></span><span className="command-result-meta">{item.pending ? 'INTEGRATION PENDING' : item.name === current ? 'CURRENT' : item.group}</span><ArrowRight className="command-result-arrow" size={15} aria-hidden="true" />
          </li>;
        })}
      </ul>
      {!matches.length && <div className="command-empty">No modules match “{query}”. Try another search.</div>}
      <div className="command-footer"><span><kbd>↑</kbd><kbd>↓</kbd> Navigate <kbd>↵</kbd> Open</span><span><kbd>ESC</kbd> Close</span></div>
    </section>
  </div>;
}
