import { useCallback, useEffect, useState } from "react";
import { X } from "lucide-react";
import { ArtifactPanel } from "./components/ArtifactPanel";
import { Files, type Folder } from "./components/Files";
import { Sidebar } from "./components/Sidebar";
import { Toasts } from "./components/ui";
import { useRoute } from "./lib/router";
import { useApp } from "./state";
import { ChatView } from "./views/ChatView";
import { LibrariesView } from "./views/LibrariesView";
import { Login } from "./views/Login";
import { ProjectView } from "./views/ProjectView";
import { SettingsView } from "./views/SettingsView";

export type PanelState =
  | { kind: "artifact"; chatId: number; id: string; version?: number }
  | { kind: "files"; folder: Folder };

export function App() {
  const { auth } = useApp();
  const route = useRoute();
  const [panel, setPanel] = useState<PanelState | null>(null);
  const openPanel = useCallback((p: PanelState) => setPanel(p), []);

  // Leaving a chat closes its panel.
  const chatKey = route.name === "chat" ? route.id : route.name;
  useEffect(() => setPanel(null), [chatKey]);

  if (!auth) return null;
  if (!auth.signedIn) return <><Login setup={auth.setupNeeded} /><Toasts /></>;

  let main;
  switch (route.name) {
    case "chat":
      main = <ChatView key={`c${route.id}`} chatId={route.id} openPanel={openPanel} />;
      break;
    case "project-new-chat":
      main = <ChatView key={`pn${route.id}`} projectId={route.id} openPanel={openPanel} />;
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
    default:
      main = <ChatView key="home" openPanel={openPanel} />;
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
            <button className="icon-btn" onClick={() => setPanel(null)} aria-label="Close"><X size={18} /></button>
          </div>
          <div className="panel-body" style={{ overflow: "hidden" }}>
            <Files folder={panel.folder} projectId={panel.folder.kind === "project" ? panel.folder.id : null} />
          </div>
        </aside>
      )}
      <Toasts />
    </div>
  );
}
