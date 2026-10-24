// The cabinet-grey rail: new chat, search, projects, recent chats, and the GPU plate.
import { useEffect, useMemo, useState } from "react";
import { BookOpen, CircleHelp, FolderClosed, MessageSquarePlus, Plus, Search, Settings, X } from "lucide-react";
import { api, type ChatListItem } from "../lib/api";
import { dateGroup } from "../lib/format";
import { linkProps, navigate, type Route } from "../lib/router";
import { useApp } from "../state";
import { GpuPlate } from "./Queue";
import { Lamp, Logo, Modal } from "./ui";

export function Sidebar({ route }: { route: Route }) {
  const { projects, chats, sidebarOpen, setSidebarOpen, refreshLists, toast } = useApp();
  const [q, setQ] = useState("");
  const [searching, setSearching] = useState(false);
  const [found, setFound] = useState<ChatListItem[] | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [newProject, setNewProject] = useState(false);

  useEffect(() => {
    if (!q.trim()) {
      setFound(null);
      return;
    }
    const ctl = new AbortController();
    const t = window.setTimeout(async () => {
      try {
        setFound(await api<ChatListItem[]>("/hub/api/chats", { query: { all: true, q: q.trim(), limit: 60 },
          signal: ctl.signal }));
      } catch {
        /* typing on */
      }
    }, 250);
    return () => {
      ctl.abort();
      window.clearTimeout(t);
    };
  }, [q]);

  const close = () => setSidebarOpen(false);
  const current = route.name === "chat" ? route.id : null;
  const list = found ?? chats;
  const shown = showAll || found ? list : list.slice(0, 40);
  const groups = useMemo(() => {
    const out: { label: string; items: ChatListItem[] }[] = [];
    for (const c of shown) {
      const g = dateGroup(c.updated_at);
      if (!out.length || out[out.length - 1].label !== g) out.push({ label: g, items: [] });
      out[out.length - 1].items.push(c);
    }
    return out;
  }, [shown]);

  return (
    <>
      {sidebarOpen && <div className="scrim" onClick={close} />}
      <nav className={`sidebar ${sidebarOpen ? "open" : ""}`} aria-label="Chats and projects">
        <div className="sidebar-head">
          <a className="brand" {...linkProps("/")} onClickCapture={close}><Logo /> SLQ Hub</a>
          <span className="grow" />
          <button className="icon-btn menu-close" onClick={close} aria-label="Close the menu"
            style={{ display: sidebarOpen ? undefined : "none" }}><X size={18} /></button>
        </div>
        <div className="sidebar-actions">
          <button className="btn" onClick={() => { close(); navigate("/"); }}>
            <MessageSquarePlus size={16} /> New chat
          </button>
          <button className="btn" aria-pressed={searching} onClick={() => { setSearching((s) => !s); setQ(""); }}
            aria-label="Search chats"><Search size={16} /></button>
        </div>
        {searching && (
          <div style={{ padding: "0 12px 8px" }}>
            <input className="input" autoFocus placeholder="Search titles and messages" value={q}
              onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Escape" && (setSearching(false), setQ(""))} />
          </div>
        )}
        <div className="sidebar-scroll">
          {!found && (
            <div className="side-group">
              <div className="side-group-head">
                Projects
                <button className="icon-btn small" onClick={() => setNewProject(true)} aria-label="New project">
                  <Plus size={15} />
                </button>
              </div>
              {projects.length === 0 && <div className="side-more" style={{ padding: "2px 8px" }}>
                A project keeps files, instructions and chats together.</div>}
              {projects.map((p) => (
                <a key={p.id} className="side-item" {...linkProps({ name: "project", id: p.id })} onClickCapture={close}
                  aria-current={(route.name === "project" || route.name === "project-new-chat") && route.id === p.id ? "page" : undefined}>
                  <FolderClosed size={15} /><span className="ellipsis">{p.name}</span>
                </a>
              ))}
            </div>
          )}
          {groups.map((g) => (
            <div className="side-group" key={g.label}>
              <div className="side-group-head">{found ? "Found" : g.label}</div>
              {g.items.map((c) => (
                <a key={c.id} className="side-item" {...linkProps({ name: "chat", id: c.id })} onClickCapture={close}
                  aria-current={current === c.id ? "page" : undefined} title={c.project_name ? `${c.title} (${c.project_name})` : c.title}>
                  <span className="ellipsis">{c.title}</span>
                  {c.running && <Lamp state="run" size="sm" pulse title="Working" />}
                </a>
              ))}
            </div>
          ))}
          {found && found.length === 0 && <div className="side-more" style={{ padding: 8 }}>No chat matches.</div>}
          {!found && list.length > 40 && (
            <button className="side-item side-more" onClick={() => setShowAll((s) => !s)}>
              {showAll ? "Show fewer" : `Show all ${list.length} chats`}
            </button>
          )}
        </div>
        <div className="side-foot">
          <div className="side-links">
            <a className="side-item" {...linkProps("/libraries")} onClickCapture={close}
              aria-current={route.name === "libraries" ? "page" : undefined}><BookOpen size={15} /> Libraries</a>
            <a className="side-item" {...linkProps("/settings/models")} onClickCapture={close}
              aria-current={route.name === "settings" ? "page" : undefined}><Settings size={15} /> Settings</a>
            <a className="side-item" {...linkProps("/help")} onClickCapture={close}
              aria-current={route.name === "help" ? "page" : undefined}><CircleHelp size={15} /> Help</a>
          </div>
          <GpuPlate />
        </div>
      </nav>
      {newProject && <NewProject onClose={() => setNewProject(false)} onCreated={async (id) => {
        setNewProject(false);
        await refreshLists();
        close();
        navigate({ name: "project", id });
      }} onError={(m) => toast(m, "fault")} />}
    </>
  );
}

function NewProject({ onClose, onCreated, onError }: { onClose: () => void; onCreated: (id: number) => void;
  onError: (m: string) => void }) {
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const p = await api<{ id: number }>("/hub/api/projects", { body: { name: name.trim(), instructions } });
      onCreated(p.id);
    } catch (e) {
      onError((e as Error).message);
      setBusy(false);
    }
  };
  return (
    <Modal title="New project" onClose={onClose} footer={
      <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!name.trim() || busy} onClick={create}>Create project</button>
      </>}>
      <label className="field"><span>Name</span>
        <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && create()} /></label>
      <label className="field"><span>Instructions for the assistant</span>
        <textarea className="textarea" rows={4} value={instructions} onChange={(e) => setInstructions(e.target.value)}
          placeholder="What this project is about, how answers should look, what to keep in mind." />
        <small>You can change these later. Files and sync devices are set up on the project page.</small></label>
    </Modal>
  );
}
