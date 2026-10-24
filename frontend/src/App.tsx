import { useCallback, useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { ArtifactPanel } from "./components/ArtifactPanel";
import { Files, type Folder } from "./components/Files";
import { Sidebar } from "./components/Sidebar";
import { Toasts } from "./components/ui";
import { useRoute } from "./lib/router";
import { useApp } from "./state";
import { PersonApp } from "./person/PersonApp";
import { ChatView } from "./views/ChatView";
import { HelpView } from "./views/HelpView";
import { LibrariesView } from "./views/LibrariesView";
import { Login } from "./views/Login";
import { ProjectView } from "./views/ProjectView";
import { SettingsView } from "./views/SettingsView";

export type PanelState =
  | { kind: "artifact"; chatId: number; id: string; version?: number }
  | { kind: "files"; folder: Folder; select?: string; at?: number };   // at: when `select` was asked for

export function App() {
  const { auth } = useApp();
  const route = useRoute();
  const [panel, setPanel] = useState<PanelState | null>(null);
  const openPanel = useCallback((p: PanelState) => setPanel(p), []);
  // Files of the chat's folder that go with the next message. The composer and
  // the Files panel both change this list, so it lives here.
  const [refs, setRefs] = useState<string[]>([]);
  const toggleRef = useCallback((path: string) =>
    setRefs((r) => (r.includes(path) ? r.filter((x) => x !== path) : [...r, path])), []);
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
  }, [chatKey]);

  if (!auth) return null;
  if (!auth.signedIn) return <><Login setup={auth.setupNeeded} /><Toasts /></>;
  if (auth.user?.role === "member") return <PersonApp />;          // a person the owner added: chats on their device

  const chat = { openPanel, refs, onRefs: setRefs, keepPanel };
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
    case "settings":
      main = <SettingsView tab={route.tab} />;
      break;
    case "help":
      main = <HelpView section={route.section} />;
      break;
    default:
      main = <ChatView key="home" {...chat} />;
  }

  return (
    <div className={`app ${panel ? "with-panel" : ""}`}>
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
              select={panel.select} selectAt={panel.at} picked={refs} onPick={toggleRef} />
          </div>
        </aside>
      )}
      <Toasts />
    </div>
  );
}
