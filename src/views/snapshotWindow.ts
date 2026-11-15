import type { Snapshot } from '../lib';

export type WindowName = 'measurements' | 'radiation' | 'environment' | 'alerts' | 'events';
export type WindowInfo = { truncated?: boolean; returned?: number; total?: number; active?: number; active_critical?: number; unacknowledged_critical?: number };

// Older servers do not advertise a window; never assume optional metadata exists.
export function snapshotWindow(data: Snapshot, name: WindowName): WindowInfo | undefined {
  const windows = (data as Snapshot & { history_window?: Partial<Record<WindowName, WindowInfo>> }).history_window;
  return windows?.[name];
}

export function hasOmittedActiveAlerts(data: Snapshot) {
  const active = snapshotWindow(data, 'alerts')?.active;
  return typeof active === 'number' && active > data.alerts.filter(alert => !alert.resolved).length;
}

export function limitedWindows(data: Snapshot) {
  const labels: Record<WindowName, string> = { measurements: 'telemetry', radiation: 'radiation', environment: 'environment', alerts: 'alerts', events: 'events' };
  return (Object.keys(labels) as WindowName[]).flatMap(name => {
    const info = snapshotWindow(data, name);
    if (!info?.truncated) return [];
    const count = Number.isFinite(info.returned) && Number.isFinite(info.total) ? ` ${info.returned}/${info.total}` : '';
    return [`${labels[name]}${count}`];
  });
}
