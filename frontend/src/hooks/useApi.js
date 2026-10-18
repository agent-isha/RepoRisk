import { useCallback, useEffect, useState } from "react";

// Run `loader` whenever `deps` change; `loader` may be null to stay idle.
// Returns { data, error, loading, reload }.
export function useApi(loader, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: Boolean(loader) });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!loader) {
      setState({ data: null, error: null, loading: false });
      return undefined;
    }
    let active = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    loader().then(
      (data) => active && setState({ data, error: null, loading: false }),
      (error) => active && setState({ data: null, error, loading: false }),
    );
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, attempt]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return { ...state, reload };
}
