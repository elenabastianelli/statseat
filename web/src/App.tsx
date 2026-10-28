import { useEffect, useState } from "react";
import { api, auth } from "./api/client";
import type { Session } from "./api/types";
import { Login } from "./components/Login";
import { Shell } from "./components/Shell";
import { Loading } from "./components/Status";
import { isLang, useI18n } from "./i18n";

export function App() {
  const { setLang } = useI18n();
  const [session, setSession] = useState<Session | null>(null);
  const [booting, setBooting] = useState(auth.hasToken());

  useEffect(() => {
    auth.onUnauthorized(() => setSession(null));
    if (!auth.hasToken()) return;
    api.session()
      .then((s) => {
        setSession(s);
        if (isLang(s.preferences.language)) setLang(s.preferences.language);
      })
      .catch(() => auth.logout())
      .finally(() => setBooting(false));
  }, [setLang]);

  if (booting) return <Loading />;
  if (!session) return <Login onLogin={setSession} />;
  return (
    <Shell
      session={session}
      setSession={setSession}
      onLogout={() => {
        auth.logout();
        setSession(null);
      }}
    />
  );
}
