// An assistant turn: thinking, text, the strip of tool calls, artifacts.
import { useEffect, useState, type ReactNode } from "react";
import { AppWindow, Brain, Check, ChevronDown, ChevronRight, Code2, Download, FileText, GitBranch, Image as ImageIcon, Shapes,
  Sparkles } from "lucide-react";
import { api, mediaUrl, type ArtifactRef, type Skill, type SkillCardData, type SkillDraft } from "../lib/api";
import { bytes, seconds } from "../lib/format";
import { toolAbout, toolLabel, toolOutcome, toolSummary, type Decision, type Item, type ReviewItem,
  type ToolView } from "../lib/turns";
import { allAnswered, QuestionList } from "./QuestionCard";
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
  /** Answer ask_user's card (0.43.0; a person's chat: its device turn); default: the run's answer. */
  answer?: (callId: string, answers: string[][]) => Promise<unknown>;
  /** A new chat with this skill chosen (the skill card, once the skill is added). */
  chatWithSkill?: (ref: string) => void;
  /** End the thinking of the step that thinks now (0.30.0); resolves to whether it ended. */
  stopThinking?: () => Promise<unknown>;
}

export function Items({ items, actions }: { items: Item[]; actions: TurnActions }) {
  const { auth } = useApp();
  const lastText = [...items].reverse().find((i) => i.kind === "text");
  return (
    <>
      {items.map((it, i) => {
        switch (it.kind) {
          case "thinking": {
            const live = actions.live && i === items.length - 1;
            return <Thinking key={i} text={it.text} live={live} onStop={live ? actions.stopThinking : undefined} />;
          }
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
          case "review":
            return <div className="strip" key={i}><ReviewRow item={it} actions={actions} /></div>;
        }
        return null;
      })}
    </>
  );
}

function Thinking({ text, live, onStop }: { text: string; live?: boolean; onStop?: () => Promise<unknown> }) {
  const [open, setOpen] = useState(false);
  const [stopping, setStopping] = useState<"" | "asked" | "missed">("");
  const words = text.trim().split(/\s+/).length;
  const ended = /\(That is enough thinking for this step[^)]*\)\s*$/.test(text);   // its budget, or Stop thinking
  // The model server says yes even when the stop came too late to change anything; if it still thinks a while
  // later, the button can be pressed again.
  useEffect(() => {
    if (stopping !== "asked") return;
    const t = window.setTimeout(() => setStopping((s) => (s === "asked" ? "missed" : s)), 8000);
    return () => window.clearTimeout(t);
  }, [stopping]);
  const stop = async () => {
    setStopping("asked");
    try {
      const r = (await onStop?.()) as { ok?: boolean } | undefined;
      if (r && r.ok === false) setStopping("missed");
    } catch {
      setStopping("missed");
    }
  };
  return (
    <div className="thinking">
      <div className="thinking-head">
        <button onClick={() => setOpen((o) => !o)} aria-expanded={open || !!live}>
          <Brain size={15} /> {live ? "Thinking" : `Thought for ${words} words${ended ? ", then stopped" : ""}`}
          {open || live ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        {live && onStop && (
          <button className="btn small thinking-stop" disabled={stopping === "asked"} onClick={stop}
            title="It stops thinking and goes on with what it has: a tool call, or its answer">
            {stopping === "asked" ? "Stopping..." : stopping === "missed" ? "Stop thinking (again)" : "Stop thinking"}
          </button>
        )}
      </div>
      {(open || live) && <ThinkingText text={text} live={live} />}
    </div>
  );
}

const REVIEW_SAID: Record<ReviewItem["state"], string> = { running: "checking the work", ok: "nothing to fix",
  problems: "found problems", failed: "did not finish" };
const REVIEW_LAMP: Record<ReviewItem["state"], LampState> = { running: "run", ok: "run", problems: "wait", failed: "fault" };
const LIST_ITEM = /^\s*([-*+]|\d+[.)])\s/;

/** A report's lines as written: a line after a list item that is not one itself (the reviewer's "Checked: ..." and
 * its verdict) starts a paragraph rather than running on inside the item. */
function reportMarkdown(text: string): string {
  const lines = text.split("\n");
  return lines.map((l, i) => (i > 0 && LIST_ITEM.test(lines[i - 1]) && l.trim() && !LIST_ITEM.test(l)
    && !/^\s/.test(l) ? "\n" + l : l)).join("\n");
}

/** Another model's check of the turn's work (0.30.0): its steps while it runs (its last answer is its report), the
 * report when the chat is opened again. */
function ReviewRow({ item, actions }: { item: ReviewItem; actions: TurnActions }) {
  const { auth } = useApp();
  const [open, setOpen] = useState(false);
  const busy = item.state === "running";
  const files = item.files?.length ?? 0, arts = item.artifacts?.length ?? 0;
  const what = files + arts === 1 ? (item.files?.[0] ?? item.artifacts?.[0] ?? "")
    : [files ? `${files} file${files > 1 ? "s" : ""}` : "", arts ? `${arts} artifact${arts > 1 ? "s" : ""}` : ""]
      .filter(Boolean).join(" and ");
  const steps = item.children && item.children.length > 0;
  const note = open && (item.error || item.changed?.length || (item.wrapped && item.limit));
  return (
    <div className="tool review">
      <button className="tool-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="row" style={{ gap: 8 }}>
          <Lamp state={REVIEW_LAMP[item.state]} size="sm" pulse={busy} />
          <span className="tool-name">Independent review</span>
        </span>
        <span className="tool-sum">{[REVIEW_SAID[item.state], item.model, what].filter(Boolean).join(" · ")}</span>
        <span className="tool-time">{item.seconds != null ? seconds(item.seconds) : busy ? "running" : ""}</span>
      </button>
      {steps && (open || busy) && (
        <div className="tool-body"><div className="sub-run"><Items items={item.children!} actions={{ ...actions }} /></div></div>
      )}
      {open && !steps && item.report && (
        <div className="tool-body review-report">
          <Markdown text={reportMarkdown(item.report)} remoteImages={!auth?.internetAsks} /></div>
      )}
      {note && (
        <div className="tool-body review-report">
          {item.error && <p className="small faint">It did not finish: {item.error}.</p>}
          {item.wrapped && item.limit && !item.error && <p className="small faint">It was asked to finish near its
            limit of {item.limit}, so it may not have checked everything.</p>}
          {item.changed?.length ? <p className="small faint">Changed in the folder while it ran (the reviewer may not
            change files): {item.changed.join(", ")}</p> : null}
        </div>
      )}
    </div>
  );
}

const STATE_LAMP: Record<string, LampState> = { streaming: "wait", running: "run", approval: "act", question: "act",
  done: "off", error: "fault" };

function ToolRow({ tool, actions }: { tool: ToolView; actions: TurnActions }) {
  const [open, setOpen] = useState(false);
  const busy = tool.state === "running" || tool.state === "streaming";
  const lamp = tool.state === "done" ? "off" : STATE_LAMP[tool.state];
  const given = toolSummary(tool);
  // (0.42.0) While it runs: what it was given. Finished: what came of it - red when it failed. What the model said
  // the call is for ("about") heads the row, with that line under it.
  const finished = (tool.state === "done" || tool.state === "error") && tool.result != null;
  const summary = finished ? toolOutcome(tool) : given || (tool.state === "streaming" ? "..." : "");
  const about = tool.subagent ? "" : toolAbout(tool);
  const sum = <span className={`tool-sum${tool.state === "error" ? " bad" : ""}`}>{tool.subagent ? "" : summary}</span>;
  return (
    <div className="tool">
      <button className={`tool-head${about ? " two" : ""}`} onClick={() => setOpen((o) => !o)} aria-expanded={open}
        title={given ? `${toolLabel(tool.name)}: ${given}` : undefined}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="row" style={{ gap: 8 }}>
          <Lamp state={tool.state === "done" ? "run" : lamp} size="sm" pulse={busy} />
          {!about && <span className="tool-name">{tool.subagent ? `Subagent: ${tool.subagent.description}` : toolLabel(tool.name)}</span>}
        </span>
        {about ? <span className="tool-text"><span className="tool-name">{about}</span>{sum}</span> : sum}
        <span className="tool-time">{tool.state === "approval" || tool.state === "question" ? "waiting for you"
          : tool.seconds != null ? seconds(tool.seconds)
          : busy ? (tool.progress ? `${tool.progress.value}/${tool.progress.max}` : "running") : ""}</span>
      </button>
      {tool.state === "approval" && (actions.approve || actions.runId) && <Approval tool={tool}
        answer={actions.approve ?? ((callId, decision) => api(`/hub/api/runs/${actions.runId}/approve`,
          { body: { call_id: callId, decision } }))} />}
      {tool.state === "question" && tool.questions && (actions.answer || actions.runId) && <AskCard tool={tool}
        answer={actions.answer ?? ((callId, answers) => api(`/hub/api/runs/${actions.runId}/answer`,
          { body: { call_id: callId, answers } }))} />}
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

/** ask_user's card (0.43.0): the questions with their options as buttons, the user's own words allowed. */
function AskCard({ tool, answer }: { tool: ToolView; answer: (callId: string, answers: string[][]) => Promise<unknown> }) {
  const { toast } = useApp();
  const questions = (tool.questions ?? []).map((q) => ({ text: q.question, options: q.options, multi: q.multi }));
  const [answers, setAnswers] = useState<string[][]>(questions.map(() => []));
  const [busy, setBusy] = useState(false);
  const send = async () => {
    setBusy(true);
    try {
      await answer(tool.id, answers);
    } catch (e) {
      toast((e as Error).message, "fault");
      setBusy(false);
    }
  };
  return (
    <div className="tool-body">
      <div className="qcard ask" role="group" aria-label="The assistant asks">
        <QuestionList questions={questions} answers={answers} onChange={setAnswers} own disabled={busy} />
        <div className="q-foot">
          <button className="btn primary small" disabled={busy || !allAnswered(questions, answers)} onClick={send}>
            Send the answer</button>
          <span className="faint small">or write it in a message</span>
        </div>
      </div>
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
          {tool.sends && tool.sends.length > 0 && <p>The content of {tool.sends.length === 1 ? "this file" :
            `these ${tool.sends.length} files`} from the chat's folder goes with it: <span className="mono">
            {tool.sends.join(", ")}</span></p>}
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
