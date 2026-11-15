// A single scheduler owns both automatic and user-requested snapshot reads.
// Manual reads during a request coalesce into one trailing read, so an action
// that just wrote data never relies on a snapshot started before that write.
export type SnapshotPollerOptions<T> = {
  request: (signal: AbortSignal) => Promise<T>;
  onData: (data: T) => void;
  onError: (error: Error) => void;
  onManualLoading: (loading: boolean) => void;
  isVisible: () => boolean;
  intervalMs?: number;
  timeoutMs?: number;
};

export function startSnapshotPolling<T>(options: SnapshotPollerOptions<T>) {
  // Poll slightly faster than one second so normal transport/render time has a margin.
  const intervalMs = options.intervalMs ?? 850;
  const timeoutMs = options.timeoutMs ?? 8000;
  let stopped = false;
  let failures = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  let inFlight: Promise<void> | undefined;
  let trailing: Promise<void> | undefined;
  let resumeQueued = false;

  function clearTimer() {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  }

  function schedule(startedAt: number) {
    clearTimer();
    if (stopped || !options.isVisible()) return;
    // Start-to-start cadence when healthy; slow requests never overlap or spin.
    // Network failures back off to at most 30 seconds.
    const delay = failures
      ? Math.min(30000, intervalMs * 2 ** Math.min(failures, 5))
      : Math.max(50, intervalMs - (Date.now() - startedAt));
    timer = setTimeout(() => { void run(false).catch(() => {}); }, delay);
  }

  function run(manual: boolean): Promise<void> {
    if (stopped) return Promise.reject(new Error('Snapshot polling has stopped.'));
    if (inFlight) {
      if (!manual) return inFlight;
      if (!trailing) {
        options.onManualLoading(true);
        const pending = inFlight.catch(() => {}).then(() => {
          if (trailing === pending) trailing = undefined;
          return run(true);
        });
        trailing = pending;
      }
      return trailing;
    }
    clearTimer();
    const startedAt = Date.now();
    controller = new AbortController();
    const activeController = controller;
    let timedOut = false;
    const deadline = setTimeout(() => { timedOut = true; activeController.abort(); }, timeoutMs);
    if (manual) options.onManualLoading(true);
    const running = Promise.resolve().then(() => options.request(activeController.signal)).then(data => {
      if (!stopped) { failures = 0; options.onData(data); }
    }, cause => {
      const error = timedOut ? new Error('Snapshot request timed out. Retrying when the service is available.') : cause instanceof Error ? cause : new Error('Could not load mission data.');
      if (!stopped) { failures++; options.onError(error); }
      throw error;
    }).finally(() => {
      clearTimeout(deadline);
      if (controller === activeController) controller = undefined;
      if (inFlight === running) inFlight = undefined;
      if (!stopped) {
        if (manual && !trailing) options.onManualLoading(false);
        schedule(startedAt);
      }
    });
    inFlight = running;
    return running;
  }

  // Initial load does not wait for the first interval.
  void run(false).catch(() => {});
  return {
    refresh: () => run(true),
    visibilityChanged() {
      clearTimer();
      if (stopped || !options.isVisible()) return;
      if (!inFlight) { void run(false).catch(() => {}); return; }
      // If visibility changes while a read is in progress, refresh after it
      // only if the tab is still visible. Coalesce rapid visibility changes.
      if (!resumeQueued) {
        resumeQueued = true;
        void inFlight.catch(() => {}).then(() => {
          resumeQueued = false;
          if (!stopped && options.isVisible()) return run(false);
        }).catch(() => {});
      }
    },
    stop() {
      stopped = true;
      clearTimer();
      controller?.abort();
    },
  };
}
