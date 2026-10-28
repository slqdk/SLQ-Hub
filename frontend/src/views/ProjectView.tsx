// A project: its chats, files, settings, history and sync devices.
import { LEVEL_OPTIONS } from "../lib/effort";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Download, Menu, MessageSquarePlus, Plus } from "lucide-react";
import { api, syncFolderOf, type ChatListItem, type Library, type Project, type Revision, type Skill } from "../lib/api";
import { ago, bytes, energy, money, tokens, worked } from "../lib/format";
import { linkProps, navigate } from "../lib/router";
import { useApp } from "../state";
import { Files } from "../components/Files";
import { ConfirmButton, Empty, Lamp, Modal, SwitchRow, Tabs, copyText } from "../components/ui";

type Tab = "chats" | "files" | "settings" | "history" | "sync";

export function ProjectView({ id, tab }: { id: number; tab?: string }) {
  const { toast, setSidebarOpen, refreshLists } = useApp();
  const [project, setProject] = useState<Project | null>(null);
  const [missing, setMissing] = useState(false);
  const current = (["chats", "files", "settings", "history", "sync"].includes(tab ?? "") ? tab : "chats") as Tab;

  const load = useCallback(async () => {
    try {
      setProject(await api<Project>(`/hub/api/projects/${id}`));
    } catch (e) {
      if ((e as { status?: number }).status === 404) setMissing(true);
      else toast((e as Error).message, "fault");
    }
  }, [id, toast]);
  useEffect(() => {
    load();
  }, [load]);

  if (missing) return <div className="main"><div className="topbar"><h1>Project not found</h1></div>
    <div className="page"><a className="btn" {...linkProps("/")}>Back to chats</a></div></div>;
  if (!project) return <div className="main"><div className="topbar"><h1>Loading</h1></div></div>;

  const setTab = (t: Tab) => navigate({ name: "project", id, tab: t === "chats" ? undefined : t }, true);

  return (
    <div className="main">
      <div className="topbar">
        <button className="icon-btn menu-btn" onClick={() => setSidebarOpen(true)} aria-label="Open the menu"><Menu size={18} /></button>
        <h1 className="ellipsis">{project.name}</h1>
      </div>
      <div className="scroll" style={current === "files" ? { overflow: "hidden", display: "flex", flexDirection: "column" } : undefined}>
        <div className="page" style={current === "files" ? { maxWidth: "none", width: "100%", paddingBottom: 0, flex: 1, display: "flex", flexDirection: "column", minHeight: 0 } : undefined}>
          <Tabs<Tab> value={current} onChange={setTab} tabs={[
            { id: "chats", label: "Chats" }, { id: "files", label: "Files" }, { id: "settings", label: "Settings" },
            { id: "history", label: "History" }, { id: "sync", label: "Sync to a PC" }]} />
          {current === "chats" && <ProjectChats project={project} />}
          {current === "files" && (
            <div style={{ flex: 1, minHeight: 0, border: "1px solid var(--rule)", borderRadius: 8, background: "var(--sheet)", marginBottom: 16, overflow: "hidden" }}>
              <Files folder={{ kind: "project", id }} projectId={id} />
            </div>
          )}
          {current === "settings" && <ProjectSettings project={project} onSaved={(p) => { setProject(p); refreshLists(); }} />}
          {current === "history" && <ProjectHistory id={id} />}
          {current === "sync" && <SyncDevices project={project} />}
        </div>
      </div>
    </div>
  );
}

const REVIEW_SAID: Record<string, string> = { ok: "review: nothing to fix", problems: "review: problems found",
  failed: "review did not finish" };

/** What a chat of the project has done (0.34.0): its answers, the models that worked, the time, the card's energy,
 * the tokens - and how its last turn ended, when that needs a look. */
function ChatFacts({ chat: c, names }: { chat: ChatListItem; names: Map<string, string> }) {
  const n = c.turns ?? 0;
  if (!n && !c.running) return <div className="meta chat-facts">No answers yet</div>;
  const facts = [
    n ? `${n} answer${n === 1 ? "" : "s"}` : "",
    (c.models ?? []).map((m) => names.get(m) ?? m).join(" → "),
    c.seconds ? `${worked(c.seconds)} working` : "",
    c.energy_wh ? energy(c.energy_wh) : "",
    c.tokens_in || c.tokens_out ? `${tokens(c.tokens_in)} tokens read, ${tokens(c.tokens_out)} written` : "",
  ].filter(Boolean);
  const last = c.running ? undefined : c.last;
  const flags = [
    last?.unfinished && <span key="u" className="chat-flag"><Lamp state="wait" size="sm" />
      {last.unfinished === "limit" ? "not finished: the step limit" : "stopped before it was done"}</span>,
    last?.status === "error" && <span key="e" className="chat-flag fault"><Lamp state="fault" size="sm" />the last answer failed</span>,
    last?.review && <span key="r" className="chat-flag">{REVIEW_SAID[last.review] ?? `review: ${last.review}`}</span>,
  ].filter(Boolean);
  return (
    <div className="meta chat-facts">
      {[...facts, ...flags].map((f, i) => <span key={i}>{i > 0 && " · "}{f}</span>)}
    </div>
  );
}

function ProjectChats({ project }: { project: Project }) {
  const { models } = useApp();
  const [chats, setChats] = useState<ChatListItem[] | null>(null);
  const names = useMemo(() => new Map((models?.llm ?? []).map((m) => [m.id, m.name])), [models]);
  useEffect(() => {
    api<ChatListItem[]>("/hub/api/chats", { query: { project_id: project.id } }).then(setChats, () => setChats([]));
  }, [project.id]);
  return (
    <div className="stack">
      {project.instructions.trim() && <div className="callout"><div className="label">Instructions</div>
        <div style={{ whiteSpace: "pre-wrap" }}>{project.instructions}</div></div>}
      <div className="row">
        <span className="grow muted small">{chats && chats.length > 0 ? `${chats.length} chat${chats.length === 1 ? "" : "s"}` : ""}
          {project.usage && project.usage.turns > 0 && <span className="project-spent" title={
            "The card's own power reading while it answered this project's chats, at the price in Settings, Usage. "
            + "Chats deleted since stay counted."}>
            {chats && chats.length > 0 ? " · " : ""}used {energy(project.usage.energy_wh)}, {money(project.usage.cost)} -{" "}
            {project.usage.turns} answer{project.usage.turns === 1 ? "" : "s"}, {tokens(project.usage.tokens_in)} tokens read,
            {" "}{tokens(project.usage.tokens_out)} written{project.usage.since ? ` since ${new Date(project.usage.since).toLocaleDateString(
              undefined, { day: "numeric", month: "short" })}` : ""}</span>}</span>
        <button className="btn primary small" onClick={() => navigate({ name: "project-new-chat", id: project.id })}>
          <MessageSquarePlus size={15} /> New chat</button>
      </div>
      <div className="list">
        {chats === null ? <Empty>Loading</Empty> : chats.length === 0 ? (
          <Empty>No chats yet. A chat here works in the project's files.</Empty>
        ) : chats.map((c) => (
          <a key={c.id} className="list-row chat-row" style={{ color: "inherit", textDecoration: "none" }} {...linkProps({ name: "chat", id: c.id })}>
            <span className="grow chat-row-main">
              <b className="ellipsis">{c.title}</b>
              <ChatFacts chat={c} names={names} />
            </span>
            {c.running && <Lamp state="run" pulse title="Working" />}
            {c.cost != null && <span className="meta num" title={`${energy(c.energy_wh)} on the card`}>{money(c.cost)}</span>}
            <span className="meta" title={`Started ${new Date(c.created_at).toLocaleString(undefined,
              { dateStyle: "medium", timeStyle: "short" })}`}>{ago(c.updated_at)}</span>
          </a>
        ))}
      </div>
    </div>
  );
}

function ProjectSettings({ project, onSaved }: { project: Project; onSaved: (p: Project) => void }) {
  const { toast, models } = useApp();
  const [name, setName] = useState(project.name);
  const [instructions, setInstructions] = useState(project.instructions);
  const [s, setS] = useState(project.settings);
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [cloudReady, setCloudReady] = useState<boolean | null>(null);
  useEffect(() => {
    api<{ libraries: Library[] }>("/hub/api/libraries").then((r) => setLibraries(r.libraries), () => undefined);
    api<Skill[]>("/hub/api/skills").then(setSkills, () => undefined);
    api<{ settings: Record<string, Record<string, unknown>> }>("/hub/api/settings").then((r) => {
      const c = r.settings.cloud ?? {};
      setCloudReady(!!c.api_key && (c.provider === "anthropic" || !!c.base_url));
    }, () => undefined);
  }, []);
  const save = async () => {
    try {
      onSaved(await api<Project>(`/hub/api/projects/${project.id}`, { method: "PATCH", body: { name, instructions, settings: s } }));
      toast("Saved", "run");
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const remove = async () => {
    try {
      await api(`/hub/api/projects/${project.id}`, { method: "DELETE" });
      toast(`Deleted ${project.name}; its files are in the Hub's trash folder`, "run");
      navigate("/", true);
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const llms = (models?.llm ?? []).filter((m) => m.installed);
  const pictures = (models?.comfy ?? []).filter((m) => m.installed && !(m.requires ?? []).length);
  return (
    <div className="stack" style={{ maxWidth: "44rem", gap: 18 }}>
      <label className="field"><span>Name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field"><span>Instructions for the assistant</span>
        <textarea className="textarea" rows={7} value={instructions} onChange={(e) => setInstructions(e.target.value)}
          placeholder="What the project is, who it is for, how answers should look." />
        <small>Every chat in the project gets these, after the Hub's own instructions.</small></label>
      <div className="form-grid">
        <label className="field"><span>Model</span>
          <select className="select" value={s.model ?? ""} onChange={(e) => setS({ ...s, model: e.target.value || null })}>
            <option value="">The default model</option>
            {llms.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select></label>
        <label className="field"><span>Effort</span>
          <select className="select" value={s.level ?? (s.thinking == null ? "" : s.thinking ? "think" : "quick")}
            onChange={(e) => setS({ ...s, level: e.target.value || null, thinking: null })}>
            <option value="">As in Settings, Assistant</option>
            {LEVEL_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <small>A chat can still pick another with the Effort button.</small></label>
        <label className="field"><span>Model for pictures</span>
          <select className="select" value={s.image_model ?? ""} onChange={(e) => setS({ ...s, image_model: e.target.value || null })}>
            <option value="">As in Settings, Assistant</option>
            {pictures.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
          <small>Chats in the project can still pick another with the picture button.</small></label>
      </div>
      <SwitchRow checked={s.cloud_escalation} onChange={(v) => setS({ ...s, cloud_escalation: v })}
        title="Let hard tasks go to the cloud model">
        When the local model gets stuck in a chat here, the turn is handed to the cloud model set in Settings, Cloud. You
        can also press Continue in the cloud under an answer, or pick the cloud model in a chat's model menu.
        {cloudReady === false && " No cloud model is set up yet."} Calls from other apps never leave the Hub.
      </SwitchRow>
      <div className="field"><span>Libraries the assistant can search here</span>
        {libraries.length === 0 ? <small>No libraries yet (Libraries in the sidebar).</small> :
          <div className="row wrap">{libraries.map((l) => (
            <label key={l.id} className="check"><input type="checkbox" checked={s.libraries.includes(l.id)}
              onChange={() => setS({ ...s, libraries: toggle(s.libraries, l.id) })} /> {l.name}</label>))}</div>}</div>
      <div className="field"><span>Skills in every chat of this project</span>
        {skills.length === 0 ? <small>No skills yet (Skills in the sidebar).</small> : <>
          <small>Chats here use these as well as the ones chosen in the chat itself.</small>
          <div className="row wrap">{skills.map((k) => (
            <label key={k.ref} className="check" title={k.description}><input type="checkbox" checked={s.skills.includes(k.ref)}
              onChange={() => setS({ ...s, skills: toggle(s.skills, k.ref) })} /> {k.title}
              {!k.mine && !k.builtin && <span className="faint"> ({k.by})</span>}</label>))}</div></>}</div>
      <div className="row"><button className="btn primary" onClick={save}>Save changes</button><span className="grow" />
        <ConfirmButton onConfirm={remove} confirmText="Delete the project and its chats?">Delete project</ConfirmButton></div>
    </div>
  );
}

function ProjectHistory({ id }: { id: number }) {
  const { toast } = useApp();
  const [data, setData] = useState<{ head: number; revisions: Revision[] } | null>(null);
  const load = useCallback(async (before?: number) => {
    try {
      const r = await api<{ head: number; revisions: Revision[] }>(`/hub/api/projects/${id}/revisions`, { query: { before, limit: 50 } });
      setData((d) => (before && d ? { head: r.head, revisions: [...d.revisions, ...r.revisions] } : r));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [id, toast]);
  useEffect(() => {
    load();
  }, [load]);
  if (!data) return <div className="faint">Loading</div>;
  const last = data.revisions[data.revisions.length - 1];
  return (
    <div className="stack">
      <p className="lead">Every change to the project's files is a revision: from the assistant, from you here, and from
        synced PCs. Restore a file from its history in Files.</p>
      <div className="list">
        {data.revisions.length === 0 ? <Empty>No revisions yet.</Empty> : data.revisions.map((r) => (
          <div className="list-row" key={r.rev} style={{ alignItems: "flex-start" }}>
            <span className="tag num">r{r.rev}</span>
            <div className="grow">
              <div><b>{r.message || `${r.fileCount} files`}</b></div>
              <div className="meta">{r.author} via {r.origin}, {ago(r.createdAt)}, {r.fileCount} file{r.fileCount === 1 ? "" : "s"}</div>
              <div className="small muted" style={{ marginTop: 4 }}>
                {r.files.slice(0, 6).map((f) => <div key={f.path} className="ellipsis">{f.deleted ? "deleted " : ""}{f.path}</div>)}
                {r.fileCount > 6 && <div className="faint">and {r.fileCount - 6} more</div>}
              </div>
            </div>
          </div>
        ))}
      </div>
      {last && last.rev > 1 && data.revisions.length % 50 === 0 && <button className="btn" onClick={() => load(last.rev)}>Older revisions</button>}
    </div>
  );
}

function SyncDevices({ project }: { project: Project }) {
  const { toast } = useApp();
  const [tokens, setTokens] = useState<{ id: number; name: string; created_at: string; last_used: string | null }[]>([]);
  const [client, setClient] = useState<{ exe: boolean; exeSize: number } | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [made, setMade] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setTokens(await api("/hub/api/sync-tokens"));
      setClient(await api("/hub/api/sync-client/info"));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [toast]);
  useEffect(() => {
    load();
  }, [load]);
  const create = async () => {
    try {
      const t = await api<{ token: string }>("/hub/api/sync-tokens", { body: { name } });
      setMade(t.token);
      setName("");
      load();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const revoke = async (tid: number) => {
    try {
      await api(`/hub/api/sync-tokens/${tid}`, { method: "DELETE" });
      load();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const server = location.origin;
  return (
    <div className="stack" style={{ maxWidth: "48rem" }}>
      <p className="lead">SLQ Sync keeps a folder on a Windows PC in step with {syncFolderOf(project)
        ? <>this project's <code>{syncFolderOf(project)}/</code> folder</> : "this project"}, both ways, with every
        revision kept here. It is the same client as before: point it at this Hub with a device token.</p>
      {syncFolderOf(project) && <p className="faint" style={{ margin: 0 }}>
        <code>hub/</code> beside it - the assistant's notes, working files and your attachments - stays on the Hub.</p>}
      <div className="callout">
        <div className="label">Set up a PC</div>
        <ol style={{ margin: "6px 0 0", paddingLeft: 20 }}>
          <li>{client?.exe ? <a href="/hub/api/sync-client/exe"><Download size={14} /> Download SLQSync.exe</a>
            : "Copy SLQSync.exe to the Hub's data/sync-client folder to offer it here, or use the one you have."}
            {client?.exe ? <span className="faint"> ({bytes(client.exeSize)})</span> : null}</li>
          <li>Make a device token below and run: <div className="code-line">SLQSync.exe setup --server {server} --token slqsync_...</div></li>
          <li>Link the folder: <div className="code-line">SLQSync.exe link {project.id} "C:\Path\To\{project.name}"</div></li>
          <li>Then <code>SLQSync.exe sync --watch 5</code>, or the tray app, keeps it in step.</li>
        </ol>
      </div>
      <div className="row"><h3 style={{ margin: 0 }} className="grow">Devices</h3>
        <button className="btn small" onClick={() => { setAdding(true); setMade(null); }}><Plus size={14} /> Add a device</button></div>
      <div className="list">
        {tokens.length === 0 ? <Empty>No devices yet.</Empty> : tokens.map((t) => (
          <div className="list-row" key={t.id}>
            <span className="grow"><b>{t.name}</b><div className="meta">added {ago(t.created_at)}, {t.last_used ? `last synced ${ago(t.last_used)}` : "never used"}</div></span>
            <ConfirmButton onConfirm={() => revoke(t.id)} confirmText="Remove access?">Remove</ConfirmButton>
          </div>
        ))}
      </div>
      {adding && (
        <Modal title="Add a device" onClose={() => setAdding(false)} footer={made
          ? <button className="btn primary" onClick={() => setAdding(false)}>Done</button>
          : <><button className="btn" onClick={() => setAdding(false)}>Cancel</button>
            <button className="btn primary" disabled={!name.trim()} onClick={create}>Make a token</button></>}>
          {made ? (
            <>
              <p style={{ margin: 0 }}>The token is shown only now. It reaches the sync API and nothing else.</p>
              <div className="code-line">{made}</div>
              <button className="btn small" onClick={async () => toast((await copyText(made)) ? "Copied" : "Copying failed", "run")}>Copy the token</button>
            </>
          ) : (
            <label className="field"><span>Name of the PC</span>
              <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Work laptop"
                onKeyDown={(e) => e.key === "Enter" && name.trim() && create()} /></label>
          )}
        </Modal>
      )}
    </div>
  );
}
