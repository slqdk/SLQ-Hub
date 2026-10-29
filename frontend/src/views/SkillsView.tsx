// Skills (0.23.0): yours, the family's shared ones and the Hub's own. Make one with the assistant from your own files,
// import one from Claude, download one for Claude, share yours with the family. A chat uses a skill only when it is
// chosen for it (the Skills button by the message box, or / and its name). Since 0.41.0 listed by category - each
// owner puts their skills into categories, private and shared alike - with a search over them all.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Download, Menu, MessageSquarePlus, MoreHorizontal, Pencil, Search, Sparkles, Tag,
  Trash2, Upload as UploadIcon, Wand2 } from "lucide-react";
import { api, type Skill } from "../lib/api";
import { byCategory, categoryNames, HUB_CATEGORY, isInternal, NO_CATEGORY, skillMatches, useFolders } from "../lib/skills";
import { ago, bytes } from "../lib/format";
import { NEW_SKILL_TEXT, SKILL_MAKER } from "../lib/draft";
import { useApp } from "../state";
import { Empty, MenuButton, Modal, Switch } from "../components/ui";

/** The owner's Skills page (a person's app shows SkillsContent in its own frame). */
export function SkillsView({ onNewChat }: { onNewChat: (text: string, skills: string[]) => void }) {
  const { setSidebarOpen } = useApp();
  return (
    <div className="main">
      <div className="topbar">
        <button className="icon-btn menu-btn" onClick={() => setSidebarOpen(true)} aria-label="Open the menu"><Menu size={18} /></button>
        <h1>Skills</h1>
      </div>
      <div className="scroll"><div className="page"><SkillsContent onNewChat={onNewChat} /></div></div>
    </div>
  );
}

export function SkillsContent({ onNewChat }: { onNewChat: (text: string, skills: string[]) => void }) {
  const { toast, auth } = useApp();
  const person = auth?.user?.role === "member";          // internal is the admin's (0.47.0)
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [view, setView] = useState<{ skill: Skill; text: string; edit: boolean } | null>(null);
  const [query, setQuery] = useState("");
  const folders = useFolders("hub:skill-page-open");    // closed until clicked (0.45.0); "internal" on three clicks
  const [placing, setPlacing] = useState<{ skill: Skill; text: string } | null>(null);   // its category
  const [renaming, setRenaming] = useState<{ old: string; text: string } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const load = useCallback(async () => {
    try {
      setSkills(await api<Skill[]>("/hub/api/skills"));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [toast]);
  useEffect(() => {
    load();
    const on = () => load();
    window.addEventListener("hub:skills", on);
    return () => window.removeEventListener("hub:skills", on);
  }, [load]);
  const changed = () => {
    load();
    window.dispatchEvent(new Event("hub:skills"));
  };

  const importFile = async (f: File) => {
    const fd = new FormData();
    fd.append("file", f, f.name);
    try {
      const r = await api<{ ref: string; name: string; names?: string[] }>("/hub/api/skills", { form: fd });
      const n = r.names?.length ?? 1;
      toast(n > 1 ? `${n} skills are among your skills now: ${r.names!.join(", ")}` : `${r.name} is among your skills now`,
        "run");
      changed();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const open = async (skill: Skill, edit: boolean) => {
    try {
      const r = await api<{ text: string }>("/hub/api/skills/one", { query: { ref: skill.ref } });
      setView({ skill, text: r.text, edit });
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const save = async () => {
    if (!view) return;
    try {
      await api("/hub/api/skills/one", { method: "PUT", query: { ref: view.skill.ref }, body: { text: view.text } });
      setView(null);
      changed();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const share = async (skill: Skill, shared: boolean) => {
    try {
      await api("/hub/api/skills/one", { method: "PATCH", query: { ref: skill.ref }, body: { shared } });
      toast(shared ? `${skill.title} is shared with the family` : `${skill.title} is yours alone again`, "run");
      changed();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const place = async () => {
    if (!placing) return;
    try {
      const r = await api<{ category: string }>("/hub/api/skills/one", { method: "PATCH",
        query: { ref: placing.skill.ref }, body: { category: placing.text } });
      toast(r.category ? `${placing.skill.title} is under ${r.category}` : `${placing.skill.title} has no category now`, "run");
      setPlacing(null);
      changed();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const rename = async () => {
    if (!renaming) return;
    try {
      const r = await api<{ moved: number }>("/hub/api/skills/categories/rename",
        { body: { old: renaming.old, new: renaming.text } });
      toast(`${r.moved} skill${r.moved === 1 ? "" : "s"} moved`, "run");
      setRenaming(null);
      changed();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const remove = async (skill: Skill) => {
    try {
      await api("/hub/api/skills/one", { method: "DELETE", query: { ref: skill.ref } });
      changed();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };

  const groups = useMemo(() => byCategory((skills ?? []).filter((k) => skillMatches(k, query))), [skills, query]);
  const names = useMemo(() => categoryNames(skills ?? []), [skills]);
  const row = (k: Skill) => (
    <div className="list-row skill-row" key={k.ref}>
      <span className="kind"><Sparkles size={16} /></span>
      <div className="grow">
        <b>{k.title}</b>
        <div className="meta skill-desc">{k.description || "No description"}</div>
        <div className="meta">{k.mine ? (k.admin_only ? "Only you - internal is never shared"
          : k.shared ? "Shared with the family" : "Only you") : `By ${k.by}`}
          {` · ${k.files.length + 1} file${k.files.length ? "s" : ""}, ${bytes(k.size)}`}
          {!k.builtin && k.updated ? ` · changed ${ago(k.updated)}` : ""}</div>
      </div>
      {k.mine && !k.admin_only && <span className="skill-share"
        title={k.shared ? "Shared with the family" : "Share it with the family"}>
        <Switch checked={k.shared} label={`Share ${k.title} with the family`} onChange={(v) => share(k, v)} />
        <span className="small muted hide-narrow">Shared</span></span>}
      <button className="btn small" onClick={() => onNewChat("", [k.ref])} title="A new chat with this skill">
        <MessageSquarePlus size={14} /> <span className="hide-narrow">Use</span></button>
      <MenuButton right className="icon-btn" label={<MoreHorizontal size={18} />} title={`More for ${k.title}`}>
        {(close) => (
          <>
            {k.mine && <button className="menu-item" onClick={() => { close(); onNewChat(`Improve the skill ${k.title}: `,
              [SKILL_MAKER, k.ref]); }}><Wand2 size={15} />
              <span>Improve with the assistant<small>A chat with the skill maker and this skill</small></span></button>}
            {k.mine && <button className="menu-item" onClick={() => { close(); setPlacing({ skill: k, text: k.category ?? "" }); }}>
              <Tag size={15} /><span>Category<small>{k.category || "None yet"}</small></span></button>}
            <button className="menu-item" onClick={() => { close(); open(k, k.mine); }}>
              <Pencil size={15} /> {k.mine ? "Edit SKILL.md" : "Read SKILL.md"}</button>
            <a className="menu-item" href={`/hub/api/skills/zip?ref=${encodeURIComponent(k.ref)}`} download={`${k.name}.zip`}
              onClick={() => close()}><Download size={15} />
              <span>Download .zip<small>The format Claude takes, to use it there too</small></span></a>
            {k.mine && <>
              <div className="menu-sep" />
              <button className="menu-item danger" onClick={() => { close(); remove(k); }}><Trash2 size={15} /> Delete</button>
            </>}
          </>
        )}
      </MenuButton>
    </div>
  );

  return (
    <div>
      <p className="lead">A skill is a set of instructions, with the knowledge that goes with them, for one kind of task.
        A chat uses a skill only when you choose it there: the <b>Skills</b> button by the message box, or type <b>/</b> and
        its name. Skills are in the format Claude uses, so they move between the two. Put yours into categories from
        their <b>⋯</b> menu - the family sees your shared ones under the same. A category opens when you click its
        name.</p>
      <div className="row wrap" style={{ marginBottom: 16, gap: 8 }}>
        <button className="btn primary" onClick={() => onNewChat(NEW_SKILL_TEXT, [SKILL_MAKER])}><Wand2 size={15} /> New skill</button>
        <button className="btn" onClick={() => input.current?.click()}><UploadIcon size={15} /> Import</button>
        <span className="muted small">New skill: give the assistant your files and say what the skill should do. Import: a
          .zip or .skill (e.g. from Claude) - one skill or a set of them - or a SKILL.md.</span>
        <input ref={input} type="file" hidden accept=".zip,.skill,.md"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) importFile(f); e.target.value = ""; }} />
      </div>

      {skills !== null && skills.length > 4 && <label className="skill-search">
        <Search size={15} />
        <input className="input" type="search" placeholder="Find a skill - its name, description or category"
          value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Find a skill" /></label>}
      {skills === null ? <Empty>Loading</Empty> : !skills.some((k) => k.mine) && !query
        ? <Empty>No skills of your own yet. Make one with the assistant from your own files, or import one.</Empty> : null}
      {skills !== null && query && groups.length === 0 && <Empty>No skill matches "{query}".</Empty>}
      {groups.map(([cat, list]) => {
        if (cat === NO_CATEGORY) return <div key={cat} className="list" style={{ marginTop: 12 }}>{list.map(row)}</div>;
        const open = (!!query && !isInternal(cat)) || folders.isOpen(cat);   // a search opens all but "internal"
        const own = cat !== HUB_CATEGORY && list.some((k) => k.mine);
        return (
          <section key={cat} className="skill-cat">
            <h2 className="section-title">
              <button className="skill-cat-toggle" onClick={() => folders.click(cat)} aria-expanded={open}>
                {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />} {cat}
                <span className="muted small">{list.length}</span></button>
              {own && <button className="icon-btn small" title={`Rename the category ${cat}`}
                aria-label={`Rename the category ${cat}`} onClick={() => setRenaming({ old: cat, text: cat })}>
                <Pencil size={13} /></button>}
            </h2>
            {open && <div className="list">{list.map(row)}</div>}
          </section>
        );
      })}

      <datalist id="skill-categories">{names.map((c) => <option key={c} value={c} />)}</datalist>
      {placing && <Modal title={`Category for ${placing.skill.title}`} onClose={() => setPlacing(null)}
        footer={<><button className="btn" onClick={() => setPlacing(null)}>Cancel</button>
          <button className="btn primary" onClick={place}>Save</button></>}>
        <label className="field"><span>Category</span>
          <input className="input" list="skill-categories" autoFocus value={placing.text} maxLength={40}
            placeholder="e.g. TwinCAT, Pictures, Documents" onChange={(e) => setPlacing({ ...placing, text: e.target.value })}
            onKeyDown={(e) => e.key === "Enter" && place()} />
          <small>Pick one you have, or type a new one. Empty: no category.
            {isInternal(placing.text) ? (person ? " internal is for the admin only."
              : " Skills in internal are yours alone: never shared, never seen by the family.")
              : placing.skill.shared ? " The family sees it under the same." : ""}</small></label>
      </Modal>}
      {renaming && <Modal title={`Rename ${renaming.old}`} onClose={() => setRenaming(null)}
        footer={<><button className="btn" onClick={() => setRenaming(null)}>Cancel</button>
          <button className="btn primary" onClick={rename}>Rename</button></>}>
        <label className="field"><span>New name</span>
          <input className="input" list="skill-categories" autoFocus value={renaming.text} maxLength={40}
            onChange={(e) => setRenaming({ ...renaming, text: e.target.value })}
            onKeyDown={(e) => e.key === "Enter" && rename()} />
          <small>All your skills in it move. A name you have already joins the two; empty: they get no category.</small></label>
      </Modal>}

      {view && <Modal title={view.edit ? `Edit ${view.skill.title}` : view.skill.title} onClose={() => setView(null)} wide
        footer={view.edit ? <><button className="btn" onClick={() => setView(null)}>Cancel</button>
          <button className="btn primary" onClick={save}>Save</button></> : <button className="btn" onClick={() => setView(null)}>Close</button>}>
        {view.skill.files.length > 0 && <p className="small muted">Its other files: {view.skill.files.join(", ")}</p>}
        <textarea className="textarea mono" rows={22} value={view.text} readOnly={!view.edit} aria-label="SKILL.md"
          onChange={(e) => setView({ ...view, text: e.target.value })} />
      </Modal>}
    </div>
  );
}
