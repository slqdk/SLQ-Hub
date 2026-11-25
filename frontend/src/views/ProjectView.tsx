// A project: its chats, files, settings (with its repository, 0.84.0) and history.
import { LEVEL_OPTIONS } from "../lib/effort";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Menu, MessageSquarePlus, RefreshCw } from "lucide-react";
import { api, syncFolderOf, type ChatListItem, type Library, type Project, type RepoStatus, type RepoView, type Revision,
  type Skill } from "../lib/api";
import { byCategory, NO_CATEGORY, useFolders } from "../lib/skills";
import { ago, bytes, energy, money, tokens, worked } from "../lib/format";
import { linkProps, navigate } from "../lib/router";
import { useApp } from "../state";
import { Files } from "../components/Files";
import { ConfirmButton, Empty, Lamp, SwitchRow, Tabs, useInterval, type LampState } from "../components/ui";

type Tab = "chats" | "files" | "settings" | "history";

export function ProjectView({ id, tab }: { id: number; tab?: string }) {
  const { toast, setSidebarOpen, refreshLists } = useApp();
  const [project, setProject] = useState<Project | null>(null);
  const [missing, setMissing] = useState(false);
  const current = (["chats", "files", "settings", "history"].includes(tab ?? "") ? tab : "chats") as Tab;
  const [repo, setRepo] = useState<RepoView | null>(null);
  const loadRepo = useCallback(async () => {
    try {
      setRepo(await api<RepoView>(`/hub/api/projects/${id}/repo`));
    } catch { /* the settings say it where it matters */ }
  }, [id]);
  // Sync now: the Sync button (force - many files deleted - when the last sync waits for it), and the page opening.
  const syncRepo = useCallback(async (force = false, quiet = false) => {
    setRepo((r) => (r?.linked ? { ...r, status: { ...(r.status ?? {}), state: "syncing" } } : r));
    try {
      setRepo(await api<RepoView>(`/hub/api/projects/${id}/repo/sync${force ? "?force=true" : ""}`, { method: "POST" }));
    } catch (e) {
      if (!quiet) toast((e as Error).message, (e as { status?: number }).status === 409 ? "info" : "fault");
      loadRepo();
    }
  }, [id, loadRepo, toast]);
  useEffect(() => {                  // opened: the repository's news in, the folder's last changes out (0.84.0)
    let gone = false;
    api<RepoView>(`/hub/api/projects/${id}/repo`).then((v) => {
      if (gone) return;
      setRepo(v);
      if (v.linked) syncRepo(false, true);
    }, () => undefined);
    return () => { gone = true; };
  }, [id, syncRepo]);
  useInterval(loadRepo, repo?.linked ? 10000 : null);

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
        {repo?.linked && <RepoBar repo={repo} onSync={(force) => syncRepo(force)} />}
      </div>
      <div className="scroll" style={current === "files" ? { overflow: "hidden", display: "flex", flexDirection: "column" } : undefined}>
        <div className="page" style={current === "files" ? { maxWidth: "none", width: "100%", paddingBottom: 0, flex: 1, display: "flex", flexDirection: "column", minHeight: 0 } : undefined}>
          <Tabs<Tab> value={current} onChange={setTab} tabs={[
            { id: "chats", label: "Chats" }, { id: "files", label: "Files" }, { id: "settings", label: "Settings" },
            { id: "history", label: "History" }]} />
          {current === "chats" && <ProjectChats project={project} />}
          {current === "files" && (
            <div style={{ flex: 1, minHeight: 0, border: "1px solid var(--rule)", borderRadius: 8, background: "var(--sheet)", marginBottom: 16, overflow: "hidden" }}>
              <Files folder={{ kind: "project", id }} projectId={id} />
            </div>
          )}
          {current === "settings" && <ProjectSettings project={project} onSaved={(p) => { setProject(p); refreshLists(); }}
            repo={repo} onRepo={(v) => { setRepo(v); load(); }} onSync={(force) => syncRepo(force)} />}
          {current === "history" && <ProjectHistory id={id} />}
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

const REPO_SAID: Record<string, string> = { ok: "synced", syncing: "syncing", error: "sync failed",
  conflict: "conflict", waiting: "waits for you" };

/** The repository in the project's top bar (0.84.0): how the last sync went, and Sync. */
function RepoBar({ repo, onSync }: { repo: RepoView; onSync: (force: boolean) => void }) {
  const st = repo.status ?? {};
  const state = st.state ?? "ok";
  const lamp: LampState = state === "syncing" ? "act" : state === "ok" ? "run" : state === "error" ? "fault" : "wait";
  const said = state === "ok" && st.at ? `synced ${ago(st.at)}` : REPO_SAID[state] ?? state;
  return (
    <div className="row repo-bar" style={{ gap: 8, marginLeft: "auto", flexShrink: 0 }}>
      <Lamp state={lamp} pulse={state === "syncing"} title={st.message || said} />
      <span className="faint hide-narrow" title={st.message || undefined}>
        <a href={repo.web} target="_blank" rel="noreferrer">{repo.repo}</a> · {said}</span>
      <button className="btn small" disabled={state === "syncing"} onClick={() => onSync(state === "waiting")}
        title={state === "waiting" ? "Delete those files in the repository too" : "Pull the repository's changes and push the folder's"}>
        <RefreshCw size={14} /> {state === "waiting" ? "Sync anyway" : "Sync"}</button>
    </div>
  );
}

function RepoStatusText({ status }: { status?: RepoStatus }) {
  if (!status?.message && !status?.big?.length && !status?.left?.length) return null;
  const warn = status.state === "error" || status.state === "conflict" || status.state === "waiting";
  return (
    <div className="stack" style={{ gap: 2 }}>
      {status.message && <small className={warn ? "warn-text" : undefined}>{status.at ? `${ago(status.at)}: ` : ""}
        {status.message}</small>}
      {!!status.big?.length && <small className="faint">Too big for the repository, kept in the folder only: {status.big
        .map((b) => `${b.path} (${bytes(b.size)})`).join(", ")}</small>}
      {!!status.left?.length && <small className="faint">Left alone (a link, or changed while it synced - the next sync
        takes it): {status.left.join(", ")}</small>}
    </div>
  );
}

/** Settings -> Repository (0.84.0): link the project's files folder to a repository on the git server, or unlink. */
function RepoSection({ project, repo, onChanged, onSync }: { project: Project; repo: RepoView | null;
  onChanged: (v: RepoView) => void; onSync: (force: boolean) => void }) {
  const { toast } = useApp();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const folder = syncFolderOf(project);
  const link = async () => {
    setBusy(true);
    try {
      onChanged(await api<RepoView>(`/hub/api/projects/${project.id}/repo`, { method: "PUT", body: { repo: text.trim() } }));
      setText("");
      toast("Linked", "run");
    } catch (e) {
      toast((e as Error).message, "fault");
    } finally {
      setBusy(false);
    }
  };
  const unlink = async () => {
    try {
      onChanged(await api<RepoView>(`/hub/api/projects/${project.id}/repo`, { method: "DELETE" }));
      toast("Unlinked - the files stay, and so does the repository", "run");
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  if (!repo) return null;
  const where = folder ? <code>{folder}/</code> : "The project's folder";
  return (
    <div className="field"><span>Repository</span>
      {repo.linked ? (
        <>
          <div className="row wrap" style={{ gap: 8 }}>
            <span className="grow">{where} is a clone of <a href={repo.web} target="_blank" rel="noreferrer">
              <b>{repo.repo}</b></a> on {repo.server}, branch <code>{repo.branch}</code>.</span>
            <button className="btn small" disabled={repo.status?.state === "syncing"}
              onClick={() => onSync(repo.status?.state === "waiting")}><RefreshCw size={14} /> Sync now</button>
            <ConfirmButton onConfirm={unlink} confirmText="Unlink? The files stay">Unlink</ConfirmButton>
          </div>
          <RepoStatusText status={repo.status} />
          <small>On your PC: <code>git clone {repo.web}.git</code> - the Hub pulls when the project opens and before each
            answer, and commits and pushes what changes in the folder.</small>
        </>
      ) : !repo.ready ? (
        <small>Set up the git server first: <a {...linkProps("/settings/agent")}>Settings, Assistant, Git server</a>.</small>
      ) : (
        <>
          <div className="row" style={{ gap: 8 }}>
            <input className="input mono grow" value={text} onChange={(e) => setText(e.target.value)} autoCapitalize="off"
              placeholder="owner/name, or its address" onKeyDown={(e) => e.key === "Enter" && text.trim() && !busy && link()} />
            <button className="btn" disabled={busy || !text.trim()} onClick={link}>{busy ? "Linking" : "Link"}</button>
          </div>
          <small>{where} becomes a clone of the repository on {repo.server}: the Hub pulls when you open the project and
            before each answer, and commits and pushes what changes - after an answer, a change in Files, or with Sync.
            The folder must be empty, or the repository (then the folder's files are its first commit), or the folder a
            part of the repository. On your PC you clone the repository from the server.
            {repo.whole && " This project keeps everything in one folder (it is from before 0.40.0): all of it goes into "
              + "the repository, the assistant's notes and pictures too."}</small>
        </>
      )}
    </div>
  );
}

function ProjectSettings({ project, onSaved, repo, onRepo, onSync }: { project: Project; onSaved: (p: Project) => void;
  repo: RepoView | null; onRepo: (v: RepoView) => void; onSync: (force: boolean) => void }) {
  const { toast, models } = useApp();
  const [name, setName] = useState(project.name);
  const [instructions, setInstructions] = useState(project.instructions);
  const [s, setS] = useState(project.settings);
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const folders = useFolders("hub:skill-project-open");     // the skills' categories, closed until clicked (0.45.0)
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
      <RepoSection project={project} repo={repo} onChanged={onRepo} onSync={onSync} />
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
          {byCategory(skills).map(([cat, list]) => {
            const folder = cat !== NO_CATEGORY;
            const open = !folder || folders.isOpen(cat);
            const on = list.filter((k) => s.skills.includes(k.ref)).length;
            return (
              <div key={cat} className="skill-pick-group">
                {folder && <button type="button" className="menu-folder" aria-expanded={open} onClick={() => folders.click(cat)}>
                  {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}<span className="grow">{cat}</span>
                  <span className="faint">{on ? `${on} chosen · ` : ""}{list.length}</span></button>}
                {open && <div className="row wrap">{list.map((k) => (
                  <label key={k.ref} className="check" title={k.description}><input type="checkbox"
                    checked={s.skills.includes(k.ref)} onChange={() => setS({ ...s, skills: toggle(s.skills, k.ref) })} />
                    {" "}{k.title}{!k.mine && !k.builtin && <span className="faint"> ({k.by})</span>}</label>))}</div>}
              </div>
            );
          })}</>}</div>
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
