import { lazy, Suspense, useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { X } from "lucide-react";
import { ArtifactPanel } from "./components/ArtifactPanel";
import { Files, type Folder } from "./components/Files";
import { Sidebar } from "./components/Sidebar";
import { Toasts } from "./components/ui";
import { setDraft } from "./lib/draft";
import { navigate, useRoute } from "./lib/router";
import { useApp } from "./state";
import { hubFolder } from "./model3d/folder";
import { PersonApp } from "./person/PersonApp";
import { ChatView } from "./views/ChatView";
import { HelpView } from "./views/HelpView";
import { LibrariesView } from "./views/LibrariesView";
import { Login } from "./views/Login";
import { ProjectView } from "./views/ProjectView";
import { SettingsView } from "./views/SettingsView";
import { ArchivedView } from "./views/ArchivedView";
import { MoviesView } from "./views/MoviesView";
import { PersonasView } from "./views/PersonasView";
import { SkillsView } from "./views/SkillsView";

// The 3D editor (0.87.0) brings three.js and manifold-3d: loaded when it first opens.
const Editor3d = lazy(() => import("./model3d/Editor").then((m) => ({ default: m.Editor })));

export type PanelState =
  | { kind: "artifact"; chatId: number; id: string; version?: number }
  | { kind: "files"; folder: Folder; select?: string; at?: number }    // at: when `select` was asked for
  // 0.87.0: a design (or an STL, 3MF, OBJ) in the 3D editor; create: a new one, in `own` (hub/ in a synced project).
  // at: when it was opened - the editor keeps its state (Undo) while the same opening lasts.
  | { kind: "model3d"; folder: Folder; path: string; create?: boolean; own?: string; at: number };

const sameFolder = (a: Folder, b: Folder) => a.kind === b.kind && a.id === b.id;

export function App() {
  const { auth } = useApp();
  const route = useRoute();
  const [panel, setPanel] = useState<PanelState | null>(null);
  // The design open in the 3D editor stays as it is when it is asked for again (the assistant wrote it).
  const openPanel = useCallback((p: PanelState) => setPanel((cur) => (p.kind === "model3d" && cur?.kind === "model3d"
    && !p.create && sameFolder(cur.folder, p.folder) && cur.path === p.path ? cur : p)), []);
  const [max3d, setMax3d] = useState(false);
  // Files of the chat's folder that go with the next message. The composer and
  // the Files panel both change this list, so it lives here.
  const [refs, setRefs] = useState<string[]>([]);
  const toggleRef = useCallback((path: string) =>
    setRefs((r) => (r.includes(path) ? r.filter((x) => x !== path) : [...r, path])), []);
  // The design open in the 3D editor goes with every message while it is open (0.87.0): the assistant reads it as
  // it is now. Taken out of one message, it is back in the next.
  const pinned = useRef<string | null>(null);
  const [pin, setPin] = useState<string | null>(null);
  const pinDesign = useCallback((path: string | null) => {
    const old = pinned.current;
    if (old === path) return;
    pinned.current = path;
    setPin(path);
    setRefs((r) => {
      const rest = r.filter((x) => x !== old);
      return path && !rest.includes(path) ? [...rest, path] : rest;
    });
  }, []);
  const onRefs: Dispatch<SetStateAction<string[]>> = setRefs;
  useEffect(() => {                                    // another panel, or none: the design no longer goes along
    if (panel?.kind !== "model3d") {
      pinDesign(null);
      setMax3d(false);
    }
  }, [panel?.kind, pinDesign]);
  const carry = useRef(false);
  const keepPanel = useCallback(() => { carry.current = true; }, []);

  // Leaving a chat closes its panel and drops the files picked for it - except
  // when a new chat has just been saved and its page opens.
  const chatKey = route.name === "chat" ? route.id : route.name;
  useEffect(() => {
    if (carry.current) {
      carry.current = false;
      return;
    }
    setPanel(null);
    setRefs([]);
    pinned.current = null;
    setPin(null);
  }, [chatKey]);

  if (!auth) return null;
  if (!auth.signedIn) return <><Login setup={auth.setupNeeded} /><Toasts /></>;
  if (auth.user?.role === "member") return <PersonApp />;          // a person the owner added: chats on their device

  const chat = { openPanel, refs, onRefs, keepPanel, pinned: pin };
  let main;
  switch (route.name) {
    case "chat":
      main = <ChatView key={`c${route.id}`} chatId={route.id} {...chat} />;
      break;
    case "project-new-chat":
      main = <ChatView key={`pn${route.id}`} projectId={route.id} {...chat} />;
      break;
    case "project":
      main = <ProjectView key={`p${route.id}`} id={route.id} tab={route.tab} />;
      break;
    case "libraries":
      main = <LibrariesView id={route.id} />;
      break;
    case "skills":
      main = <SkillsView onNewChat={(text, skills) => { setDraft(text, skills); navigate("/"); }} />;
      break;
    case "archived":
      main = <ArchivedView />;
      break;
    case "movies":
      main = <MoviesView key={route.id ?? "list"} id={route.id} episode={route.episode} />;
      break;
    case "personas":
      main = <PersonasView key={route.id ?? "list"} id={route.id} />;
      break;
    case "settings":
      main = <SettingsView tab={route.tab} sub={route.sub} />;
      break;
    case "help":
      main = <HelpView section={route.section} />;
      break;
    default:
      main = <ChatView key="home" {...chat} />;
  }

  return (
    <div className={`app ${panel ? "with-panel" : ""}${panel?.kind === "model3d" ? ` with-3d${max3d ? " max-3d" : ""}` : ""}`}>
      <Sidebar route={route} />
      {main}
      {panel?.kind === "artifact" && (
        <ArtifactPanel chatId={panel.chatId} id={panel.id} version={panel.version} onClose={() => setPanel(null)} />
      )}
      {panel?.kind === "files" && (
        <aside className="panel" aria-label="Files">
          <div className="panel-head">
            <h2>{panel.folder.kind === "project" ? "Project files" : "This chat's files"}</h2>
            {refs.length > 0 && <span className="tag num">{refs.length} in the message</span>}
            <button className="icon-btn" onClick={() => setPanel(null)} aria-label="Close"><X size={18} /></button>
          </div>
          <div className="panel-body" style={{ overflow: "hidden" }}>
            <Files folder={panel.folder} projectId={panel.folder.kind === "project" ? panel.folder.id : null}
              select={panel.select} selectAt={panel.at} picked={refs} onPick={toggleRef}
              onOpen3d={(path) => openPanel({ kind: "model3d", folder: panel.folder, path, at: Date.now() })} />
          </div>
        </aside>
      )}
      {panel?.kind === "model3d" && (
        <Suspense fallback={<aside className="panel panel-3d" aria-label="3D editor"><div className="v3d-overlay">Loading the 3D editor</div></aside>}>
          <Editor3d key={panel.at} store={hubFolder(panel.folder)} path={panel.path} create={panel.create} own={panel.own}
            onPath={(path) => setPanel((p) => (p?.kind === "model3d" ? { ...p, path, create: false } : p))}
            onClose={() => { pinDesign(null); setPanel(null); setMax3d(false); }} max={max3d}
            onMax={() => setMax3d((m) => !m)} onPin={pinDesign} />
        </Suspense>
      )}
      <Toasts />
    </div>
  );
}
