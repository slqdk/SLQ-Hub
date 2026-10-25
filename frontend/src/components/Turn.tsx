// An assistant turn: thinking, text, the strip of tool calls, artifacts.
import { useEffect, useState, type ReactNode } from "react";
import { AppWindow, Brain, Check, ChevronDown, ChevronRight, Code2, Download, FileText, GitBranch, Image as ImageIcon, Shapes,
  Sparkles } from "lucide-react";
import { api, mediaUrl, type ArtifactRef, type Skill, type SkillCardData, type SkillDraft } from "../lib/api";
import { bytes, seconds } from "../lib/format";
import { toolLabel, toolSummary, type Decision, type Item, type ToolView } from "../lib/turns";
import { useApp } from "../state";
import { Markdown } from "./Markdown";
import { ThinkingText } from "./ThinkingText";
import { Lamp, type LampState } from "./ui";

export interface TurnActions {
  runId?: string | null;                       // set while live: approvals go to this run
  openArtifact: (ref: ArtifactRef) => void;
  live?: boolean;
  /** Answer a call that waits for the user (a person's chat: its device turn); default: the run's approve. */
  approve?: (callId: string, decision: Decision) => Promise<unknown>;
  /** A new chat with this skill chosen (the skill card, once the skill is added). */
  chatWithSkill?: (ref: string) => void;
}

export function Items({ items, actions }: { items: Item[]; actions: TurnActions }) {
  const { auth } = useApp();
  const lastText = [...items].reverse().find((i) => i.kind === "text");
  return (
    <>
      {items.map((it, i) => {
        switch (it.kind) {
          case "thinking":
            return <Thinking key={i} text={it.text} live={actions.live && i === items.length - 1} />;
          case "text":
            return <Markdown key={i} text={it.text} live={actions.live && it === lastText && i === items.length - 1}
              remoteImages={!auth?.internetAsks} />;
          case "tools": {
            // The task list and finished artifacts show as their own blocks.
            const shown = it.tools.filter((t) => t.name !== "todo_write" && !(t.name === "artifact" && t.display?.artifact));
            if (!shown.length) return null;
            return <div className="strip" key={i}>{shown.map((t) => <ToolRow key={t.id} tool={t} actions={actions} />)}</div>;
          }
          case "artifact":
            return <ArtifactCard key={i} refx={it.ref} onOpen={() => actions.openArtifact(it.ref)} />;
          case "skill":
            return <SkillCard key={i} card={it.card} onUse={actions.chatWithSkill} />;
          case "user":
            return null;
          case "todos":
            return (
              <ul className="todos" key={i} aria-label="Task list">
                {it.items.map((t, j) => (
                  <li key={j} data-status={t.status}>
                    <Lamp size="sm" state={t.status === "completed" ? "run" : t.status === "in_progress" ? "wait" : "off"} />
                    <span>{t.content}</span>
                  </li>
                ))}
              </ul>
            );
          case "notice":
            return <div key={i} className={`notice ${it.tone === "fault" ? "fault" : ""}`}>
              <Lamp state={it.tone === "fault" ? "fault" : it.tone === "wait" ? "wait" : "off"} />{it.text}</div>;
        }
        return null;
      })}
    </>
  );
}

function Thinking({ text, live }: { text: string; live?: boolean }) {
  const [open, setOpen] = useState(false);
  const words = text.trim().split(/\s+/).length;
  return (
    <div className="thinking">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open || !!live}>
        <Brain size={15} /> {live ? "Thinking" : `Thought for ${words} words`}
        {open || live ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {(open || live) && <ThinkingText text={text} live={live} />}
    </div>
  );
}

const STATE_LAMP: Record<string, LampState> = { streaming: "wait", running: "run", approval: "act", done: "off", error: "fault" };

function ToolRow({ tool, actions }: { tool: ToolView; actions: TurnActions }) {
  const [open, setOpen] = useState(false);
  const busy = tool.state === "running" || tool.state === "streaming";
  const lamp = tool.state === "done" ? "off" : STATE_LAMP[tool.state];
  const summary = toolSummary(tool) || (tool.state === "streaming" ? "..." : "");
  return (
    <div className="tool">
      <button className="tool-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="row" style={{ gap: 8 }}>
          <Lamp state={tool.state === "done" ? "run" : lamp} size="sm" pulse={busy} />
          <span className="tool-name">{tool.subagent ? `Subagent: ${tool.subagent.description}` : toolLabel(tool.name)}</span>
        </span>
        <span className="tool-sum">{tool.subagent ? "" : summary}</span>
        <span className="tool-time">{tool.state === "approval" ? "waiting for you" : tool.seconds != null ? seconds(tool.seconds)
          : busy ? (tool.progress ? `${tool.progress.value}/${tool.progress.max}` : "running") : ""}</span>
      </button>
      {tool.state === "approval" && (actions.approve || actions.runId) && <Approval tool={tool}
        answer={actions.approve ?? ((callId, decision) => api(`/hub/api/runs/${actions.runId}/approve`,
          { body: { call_id: callId, decision } }))} />}
      {tool.children && tool.children.length > 0 && (open || busy) && (
        <div className="tool-body"><div className="sub-run"><Items items={tool.children} actions={{ ...actions }} /></div></div>
      )}
      {open && <ToolDetail tool={tool} />}
      {!open && tool.images && tool.images.length > 0 && (
        <div className="tool-body"><ToolImages images={tool.images} /></div>
      )}
    </div>
  );
}

function ToolImages({ images }: { images: { sha: string; caption?: string }[] }) {
  return (
    <div className="tool-images">
      {images.map((im) => (
        <a key={im.sha} href={mediaUrl(im.sha)} target="_blank" rel="noreferrer" title={im.caption}>
          <img src={mediaUrl(im.sha)} alt={im.caption ?? "image from the tool"} loading="lazy" />
        </a>
      ))}
    </div>
  );
}

function ToolDetail({ tool }: { tool: ToolView }) {
  const args = tool.args ? JSON.stringify(tool.args, null, 2) : tool.argsText;
  const hits = tool.display?.library_hits;
  return (
    <div className="tool-body">
      {args && args !== "{}" && <><div className="label">Input</div><pre>{args}</pre></>}
      {tool.result != null && <><div className="label">{tool.state === "error" ? "Error" : "Result"}</div>
        <pre>{tool.result.length > 20000 ? tool.result.slice(0, 20000) + "\n..." : tool.result}</pre></>}
      {hits && hits.length > 0 && <div className="small muted">
        {hits.map((h, i) => <div key={i}>[{i + 1}] {h.filename}{h.page ? `, page ${h.page}` : ""} <span className="faint">({h.via.join(" + ")})</span></div>)}
      </div>}
      {tool.images && tool.images.length > 0 && <ToolImages images={tool.images} />}
    </div>
  );
}

const ASK_TEXT: Record<string, string> = {
  search: "wants to search the web",
  site: "wants to read a page from",
  code: "wants to run code with internet access",
};

function Approval({ tool, answer }: { tool: ToolView;
  answer: (callId: string, decision: Decision) => Promise<unknown> }) {
  const { toast } = useApp();
  const [busy, setBusy] = useState(false);
  const ask = tool.ask;
  const reply = async (decision: Decision) => {
    setBusy(true);
    try {
      await answer(tool.id, decision);
    } catch (e) {
      toast((e as Error).message, "fault");
      setBusy(false);
    }
  };
  return (
    <div className="tool-body">
      <div className="approval" role="alert">
        {ask ? <>
          <p>The assistant {ASK_TEXT[ask.what] ?? "wants to use the internet"}{ask.what === "site" ? <> <b>{ask.host}</b></> : null}.
            {" "}You have it ask before it goes online{ask.unblockable ? " (code cannot be kept offline here, so every command asks)" : ""}.</p>
          {ask.detail && <pre>{ask.detail}</pre>}
        </> : <>
          <p><b>{toolLabel(tool.name)}</b> wants to run. It reaches outside the sandbox, so it asks first.</p>
          {tool.args && Object.keys(tool.args).length > 0 && <pre>{JSON.stringify(tool.args, null, 2)}</pre>}
        </>}
        <div className="row wrap">
          <button className="btn primary small" disabled={busy} onClick={() => reply("allow")}>Allow once</button>
          {tool.canChat && <button className="btn small" disabled={busy} onClick={() => reply("chat")}
            title="Until the chat is deleted, or you take it back in the chat's menu">Allow in this chat</button>}
          {(!ask || ask.can_always) && <button className="btn small" disabled={busy} onClick={() => reply("always")}>
            {ask ? ask.always : "Always allow this tool"}</button>}
          <button className="btn danger small" disabled={busy} onClick={() => reply("deny")}>Deny</button>
        </div>
      </div>
    </div>
  );
}

const KIND_ICON: Record<string, ReactNode> = {
  html: <AppWindow size={18} />, react: <AppWindow size={18} />, svg: <Shapes size={18} />,
  mermaid: <GitBranch size={18} />, markdown: <FileText size={18} />, code: <Code2 size={18} />,
};
const KIND_NAME: Record<string, string> = { html: "Web page", react: "React app", svg: "SVG drawing", mermaid: "Diagram",
  markdown: "Document", code: "Code" };

/** A skill the assistant made (skill_propose, 0.23.0): what it is, and Add skill - nothing is added until then. */
function SkillCard({ card, onUse }: { card: SkillCardData; onUse?: (ref: string) => void }) {
  const { toast } = useApp();
  const [state, setState] = useState<"ready" | "adding" | "added" | "gone">("ready");
  const [text, setText] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {                                   // an older card: added already, or gone after two weeks
    let stop = false;
    api<SkillDraft>(`/hub/api/skills/drafts/${card.draft}`).then(
      (d) => { if (!stop) { setText(d.text); if (d.added) setState("added"); } },
      () => { if (!stop) setState("gone"); });
    return () => { stop = true; };
  }, [card.draft]);
  const add = async () => {
    setState("adding");
    try {
      await api<{ ref: string; skill: Skill }>(`/hub/api/skills/drafts/${card.draft}/add`, { method: "POST" });
      setState("added");
      toast(`${card.title} is among your skills now`, "run");
      window.dispatchEvent(new Event("hub:skills"));     // the composer's list
    } catch (e) {
      setState("ready");
      toast((e as Error).message, "fault");
    }
  };
  const files = card.files.length + 1;
  return (
    <div className="skill-card" aria-label={`Skill ${card.title}`}>
      <div className="skill-card-head">
        <span className="kind"><Sparkles size={18} /></span>
        <span className="grow"><b>{card.title}</b>
          <span className="small muted">{card.replaces ? "A new version of your skill" : "A new skill"} · {files} file{files === 1 ? "" : "s"}
            , {bytes(card.size)}</span></span>
      </div>
      <p className="skill-card-desc">{card.description}</p>
      {open && (
        <div className="skill-card-files">
          <pre className="skill-card-text">{text ?? "Loading"}</pre>
          {card.files.length > 0 && <div className="small muted">Its other files: {card.files.join(", ")}</div>}
        </div>
      )}
      <div className="skill-card-actions">
        {state === "added" ? (
          <>
            <span className="tag ok"><Check size={13} /> Added to your skills</span>
            {onUse && <button className="btn small" onClick={() => onUse(card.ref)}>Use in a new chat</button>}
          </>
        ) : state === "gone" ? (
          <span className="small muted">This draft is gone - drafts are kept for two weeks. Ask for the skill again.</span>
        ) : (
          <button className="btn primary small" onClick={add} disabled={state === "adding"}>
            <Sparkles size={14} /> {card.replaces ? "Add skill (replaces the old one)" : "Add skill"}</button>
        )}
        {state !== "gone" && <>
          <button className="btn quiet small" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            {open ? "Hide" : "Show"} SKILL.md</button>
          <a className="btn quiet small" href={`/hub/api/skills/drafts/${card.draft}/zip`} download={`${card.name}.zip`}>
            <Download size={14} /> .zip</a>
        </>}
      </div>
    </div>
  );
}

function ArtifactCard({ refx, onOpen }: { refx: ArtifactRef; onOpen: () => void }) {
  return (
    <button className="artifact-card" onClick={onOpen}>
      <span className="kind">{KIND_ICON[refx.type] ?? <ImageIcon size={18} />}</span>
      <span><b>{refx.title}</b><span className="small muted">{KIND_NAME[refx.type] ?? refx.type}, version {refx.version}</span></span>
      <span className="btn small">Open</span>
    </button>
  );
}
