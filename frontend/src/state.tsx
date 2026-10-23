// What the whole app shares: who is signed in, the live GPU queue, models,
// the sidebar lists, toasts and the colour theme.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, type AuthStatus, type ChatListItem, type ModelsData, type Project, type QueueSnapshot } from "./lib/api";

export type Toast = { id: number; text: string; kind: "info" | "fault" | "run" };
export type Theme = "auto" | "light" | "dark";

interface AppState {
  auth: AuthStatus | null;
  refreshAuth: () => Promise<void>;
  queue: QueueSnapshot | null;
  queueLive: boolean;
  models: ModelsData | null;
  refreshModels: () => Promise<void>;
  modelNames: Record<string, string>;
  projects: Project[];
  chats: ChatListItem[];
  refreshLists: () => Promise<void>;
  toast: (text: string, kind?: Toast["kind"]) => void;
  toasts: Toast[];
  dismiss: (id: number) => void;
  theme: Theme;
  setTheme: (t: Theme) => void;
  sidebarOpen: boolean;
  setSidebarOpen: (v: boolean) => void;
}

const Ctx = createContext<AppState | null>(null);

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp outside AppProvider");
  return v;
}

function readTheme(): Theme {
  try {
    const t = localStorage.getItem("hub.theme");
    return t === "light" || t === "dark" ? t : "auto";
  } catch {
    return "auto";
  }
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [queue, setQueue] = useState<QueueSnapshot | null>(null);
  const [queueLive, setQueueLive] = useState(false);
  const [models, setModels] = useState<ModelsData | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [chats, setChats] = useState<ChatListItem[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [theme, setThemeState] = useState<Theme>(readTheme);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const toastId = useRef(0);

  const refreshAuth = useCallback(async () => {
    try {
      setAuth(await api<AuthStatus>("/hub/api/auth/status"));
    } catch {
      setAuth({ signedIn: false, user: null, version: "", setupNeeded: false });
    }
  }, []);

  const refreshModels = useCallback(async () => {
    try {
      setModels(await api<ModelsData>("/hub/api/models"));
    } catch {
      /* the models page shows its own error */
    }
  }, []);

  const listSeq = useRef(0);
  const refreshLists = useCallback(async () => {
    const n = ++listSeq.current;
    try {
      const [p, c] = await Promise.all([
        api<Project[]>("/hub/api/projects"),
        api<ChatListItem[]>("/hub/api/chats", { query: { all: true, limit: 300 } }),
      ]);
      if (n !== listSeq.current) return;      // a newer refresh started: an older answer must not win
      setProjects(p);
      setChats(c);
    } catch {
      /* signed out or offline: the next refresh catches up */
    }
  }, []);

  const toast = useCallback((text: string, kind: Toast["kind"] = "info") => {
    const id = ++toastId.current;
    setToasts((t) => [...t.slice(-3), { id, text, kind }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "fault" ? 9000 : 4500);
  }, []);
  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    try {
      localStorage.setItem("hub.theme", t);
    } catch {
      /* private mode */
    }
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "auto") delete root.dataset.theme;
    else root.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    refreshAuth();
    const out = () => setAuth((a) => (a ? { ...a, signedIn: false, user: null } : a));
    window.addEventListener("hub:signed-out", out);
    return () => window.removeEventListener("hub:signed-out", out);
  }, [refreshAuth]);

  const signedIn = !!auth?.signedIn;
  const member = auth?.user?.role === "member";          // a person: no projects or chats on the Hub
  useEffect(() => {
    if (!signedIn) return;
    refreshModels();
    if (!member) refreshLists();
    // The GPU queue, live. EventSource reconnects by itself.
    const es = new EventSource("/hub/api/events");
    es.addEventListener("queue", (e) => {
      setQueueLive(true);
      try {
        setQueue(JSON.parse((e as MessageEvent).data));
      } catch {
        /* a broken frame: the next one fixes it */
      }
    });
    es.onerror = () => setQueueLive(false);
    const t = member ? 0 : window.setInterval(refreshLists, 60000);
    return () => {
      es.close();
      if (t) window.clearInterval(t);
    };
  }, [signedIn, member, refreshModels, refreshLists]);

  // The loaded model changes what the model list shows as loaded.
  const current = queue?.current;
  useEffect(() => {
    if (signedIn && current !== undefined) refreshModels();
  }, [current, signedIn, refreshModels]);

  const modelNames = useMemo(() => {
    const out: Record<string, string> = {};
    for (const m of models?.llm ?? []) out[m.id] = m.name;
    return out;
  }, [models]);

  const value: AppState = { auth, refreshAuth, queue, queueLive, models, refreshModels, modelNames, projects, chats,
    refreshLists, toast, toasts, dismiss, theme, setTheme, sidebarOpen, setSidebarOpen };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
