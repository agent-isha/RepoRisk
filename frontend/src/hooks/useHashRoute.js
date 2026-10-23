import { useCallback, useEffect, useState } from "react";

// Minimal hash router ("#/files"): deep links and the back button work
// without adding a routing library or needing server-side rewrites.
export function useHashRoute(routes, fallback) {
  const read = useCallback(() => {
    const name = window.location.hash.replace(/^#\/?/, "");
    return routes.includes(name) ? name : fallback;
  }, [routes, fallback]);

  const [route, setRoute] = useState(read);

  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, [read]);

  const navigate = useCallback((name) => {
    if (window.location.hash !== `#/${name}`) window.location.hash = `/${name}`;
    setRoute(name);
  }, []);

  return [route, navigate];
}
