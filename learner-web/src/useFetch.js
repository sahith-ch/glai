import { useEffect, useState } from "react";
import { apiFetch } from "./auth.js";

// `key` lets callers force a refetch (e.g. after creating a roadmap) without changing the URL.
export function useFetch(url, key = 0) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  useEffect(() => {
    if (!url) return undefined;
    let live = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    apiFetch(url)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status === 404 ? "Not found" : `Error ${r.status}`))))
      .then((data) => live && setState({ data, error: null, loading: false }))
      .catch((error) => live && setState({ data: null, error, loading: false }));
    return () => {
      live = false;
    };
  }, [url, key]);
  return state;
}
