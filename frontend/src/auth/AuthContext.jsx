import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, onUnauthorized, setCsrfToken } from "../api/client.js";

const AuthContext = createContext(null);

// status: "checking" → asks the server who is signed in
//         "signedOut" → show the login page (optionally with a notice)
//         "signedIn"  → show the dashboard
//         "error"     → the API could not be reached at all
export function AuthProvider({ children }) {
  const [state, setState] = useState({ status: "checking", user: null, notice: null, error: null });

  const accept = useCallback((session) => {
    setCsrfToken(session.csrf_token);
    setState({ status: "signedIn", user: session.user, notice: null, error: null });
  }, []);

  const signOutLocally = useCallback((notice = null) => {
    setCsrfToken(null);
    setState({ status: "signedOut", user: null, notice, error: null });
  }, []);

  const check = useCallback(async () => {
    setState((s) => ({ ...s, status: "checking", error: null }));
    try {
      accept(await api.me());
    } catch (error) {
      if (error.status === 401) signOutLocally();
      else setState({ status: "error", user: null, notice: null, error });
    }
  }, [accept, signOutLocally]);

  useEffect(() => {
    check();
  }, [check]);

  // Any authenticated request that comes back 401 means the session ended.
  useEffect(
    () => onUnauthorized(() => signOutLocally("Your session has ended. Sign in again to continue.")),
    [signOutLocally],
  );

  const login = useCallback(async (email, password) => accept(await api.login(email, password)), [accept]);

  const logout = useCallback(async () => {
    try {
      await api.logout();
      signOutLocally("You have been signed out.");
    } catch {
      signOutLocally("Signed out on this device. The server could not be reached to end the session.");
    }
  }, [signOutLocally]);

  const value = useMemo(() => ({ ...state, login, logout, retry: check }), [state, login, logout, check]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}
