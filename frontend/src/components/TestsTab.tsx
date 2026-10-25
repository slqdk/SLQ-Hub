// Settings -> Tests (0.21.0): hard tasks for the assistant in every area, run as real chats and checked; a run's
// results with each check, and its export for a closer look.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, ChevronDown, ChevronUp, Download, ExternalLink, Info, Play, Square, X } from "lucide-react";
import { api } from "../lib/api";
import { download } from "../lib/download";
import { seconds } from "../lib/format";
import { NEEDS, type Need } from "../lib/help";
import { linkProps } from "../lib/router";
import { useApp } from "../state";
import { ConfirmButton, Lamp, useInterval, type LampState } from "./ui";

type Status = "pass" | "partial" | "fail" | "error" | "skipped" | "stopped";

interface TestCase {
  id: string; area: string; title: string; what: string; prompt: string; expect: string; needs: string[];
  internet: boolean; review: string; ready: boolean; missing: string[]; last_seconds: number | null;
}
interface TestCheck { name: string; ok: boolean | null; level: "must" | "should" | "info"; detail: string }
interface TestResult {
  id: string; area: string; title: string; status: Status; why: string; checks: TestCheck[]; review?: string;
  chat_id?: number; seconds?: number; queue_seconds?: number; model?: string; answer?: string; score?: [number, number];
  tools?: { name: string; ok: boolean }[]; files?: string[];
  approvals?: { tool: string; what: string; asked: string; answer: string; why: string }[];
  handed_over?: { to: string; from: string; reason: string }[];
  load_seconds?: number; model_seconds?: number; tool_seconds?: number; model_steps?: number;
  tps?: number | null;
}
interface Live { run: string; index: number; case: string | null; chat_id: number | null; started: number | null; activity: string }
interface RunSummary {
  id: string; created_at: string; ended_at: string | null; status: string; model: string; model_name: string;
  thinking: boolean | null; total: number; done: number; counts: Record<Status, number>; seconds: number;
}
interface RunRecord {
  id: string; created_at: string; ended_at: string | null; status: string; model: string; cases: string[];
  options: { model_name?: string; thinking?: boolean | null; version?: string }; results: TestResult[]; live: Live | null;
}
interface Overview {
  areas: { id: string; title: string }[]; cases: TestCase[]; models: { id: string; name: string }[];
  default_model: string; thinking_in_chats: boolean; internet_asks: boolean; running: string | null; live: Live | null;
  runs: RunSummary[];
}

const LAMP: Record<Status, LampState> = { pass: "run", partial: "wait", fail: "fault", error: "fault", skipped: "off",
  stopped: "off" };
const WORD: Record<Status, string> = { pass: "Passed", partial: "In part", fail: "Failed", error: "Error",
  skipped: "Skipped", stopped: "Stopped" };

function missingText(needs: string[]): string {
  const first = needs[0] as Need | undefined;
  return first && NEEDS[first] ? NEEDS[first].missing : `Needs ${needs.join(", ")}`;
}

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function TestsTab() {
  const { toast } = useApp();
  const [data, setData] = useState<Overview | null>(null);
  const [picked, setPicked] = useState<Set<string> | null>(null);
  const [model, setModel] = useState("");
  const [thinking, setThinking] = useState("default");
  const [open, setOpen] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const load = useCallback(async () => {
    try {
      const o = await api<Overview>("/hub/api/tests");
      setData(o);
      setPicked((p) => p ?? new Set(o.cases.filter((c) => c.ready).map((c) => c.id)));
      setModel((m) => m || o.default_model);
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [toast]);
  useEffect(() => {
    load();
  }, [load]);
  useInterval(load, data?.running ? 2000 : null);

  const byArea = useMemo(() => (data?.areas ?? []).map((a) => ({ ...a, cases: (data?.cases ?? []).filter((c) => c.area === a.id) })),
    [data]);
  const chosen = (data?.cases ?? []).filter((c) => c.ready && picked?.has(c.id));
  const toggle = (ids: string[], on: boolean) => setPicked((p) => {
    const next = new Set(p ?? []);
    ids.forEach((id) => (on ? next.add(id) : next.delete(id)));
    return next;
  });
  const run = async () => {
    setStarting(true);
    try {
      const rec = await api<RunRecord>("/hub/api/tests/runs", { body: { cases: chosen.map((c) => c.id), model,
        thinking: thinking === "default" ? null : thinking === "on" } });
      setOpen(rec.id);
      await load();
      window.requestAnimationFrame(() => document.querySelector(".tests")?.scrollIntoView({ block: "start" }));
    } catch (e) {
      toast((e as Error).message, "fault");
    } finally {
      setStarting(false);
    }
  };
  const stop = async (id: string) => {
    try {
      await api(`/hub/api/tests/runs/${id}/stop`, { method: "POST" });
      toast("Stopping after this step", "run");
      await load();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const remove = async (id: string) => {
    try {
      await api(`/hub/api/tests/runs/${id}`, { method: "DELETE" });
      if (open === id) setOpen(null);
      await load();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };

  if (!data) return <div className="faint">Loading</div>;
  const live = data.live;
  const liveCase = live?.case ? data.cases.find((c) => c.id === live.case) : null;
  const liveRun = data.runs.find((r) => r.id === data.running);
  const areaTitle = (id: string) => data.areas.find((a) => a.id === id)?.title ?? id;
  const caseTitle = (id: string) => {
    const c = data.cases.find((x) => x.id === id);
    return c ? `${areaTitle(c.area)} - ${c.title}` : id;
  };
  return (
    <div className="tests">
      <p className="lead">Hard tasks for the assistant in every area, each run as a real chat on this Hub - the same
        models, queue, tools and sandbox as your own chats, at a lower priority so your chats go first. What a machine
        can check is checked; <b>Export</b> gives a ZIP with the answers, the files and each chat's troubleshooting
        export, to send to Claude for a closer look.</p>

      {data.running && live && liveRun && (
        <div className="callout tests-live" role="status">
          <div className="row wrap">
            <Lamp state="act" />
            <b className="grow">Test {live.index + 1} of {liveRun.total}{liveCase ? `: ${areaTitle(liveCase.area)} - ${liveCase.title}` : ""}</b>
            {live.chat_id && <a className="btn small" {...linkProps({ name: "chat", id: live.chat_id })}>
              <ExternalLink size={14} /> Open the chat</a>}
            <button className="btn small" onClick={() => stop(data.running!)}><Square size={13} /> Stop</button>
          </div>
          <div className="meta">{live.activity}{live.started ? `, ${seconds(Date.now() / 1000 - live.started)}` : ""}
            {liveCase?.last_seconds ? ` (it took ${seconds(liveCase.last_seconds)} last time)` : ""}</div>
          <div className="bar" style={{ marginTop: 8 }}><span style={{ width: `${(liveRun.done / Math.max(1, liveRun.total)) * 100}%` }} /></div>
        </div>
      )}

      {data.runs.length > 0 && <h3>Results</h3>}
      {data.runs.length > 0 && (
        <div className="list">
          {data.runs.map((r) => (
            <RunRow key={r.id} run={r} open={open === r.id} going={data.running === r.id}
              onToggle={() => setOpen(open === r.id ? null : r.id)} onRemove={() => remove(r.id)} areaTitle={areaTitle}
              caseTitle={caseTitle} />
          ))}
        </div>
      )}
      <h3>Run tests</h3>
      <div className="form-grid" style={{ marginBottom: 14 }}>
        <label className="field"><span>Chat model</span>
          <select className="select" value={model} onChange={(e) => setModel(e.target.value)}>
            {data.models.map((m) => <option key={m.id} value={m.id}>{m.name}{m.id === data.default_model ? " (default)" : ""}</option>)}
          </select>
          <small>Pictures, sound and video use the models chosen under Assistant.</small></label>
        <label className="field"><span>Thinking</span>
          <select className="select" value={thinking} onChange={(e) => setThinking(e.target.value)}>
            <option value="default">As in a chat ({data.thinking_in_chats ? "on" : "off"}, or the model's own)</option>
            <option value="on">On</option><option value="off">Off</option>
          </select></label>
      </div>
      {data.internet_asks && <p className="muted small" style={{ maxWidth: "62ch" }}>The assistant asks before it goes
        online. A test answers Allow once only for Research on the web; everything else it asks is answered No.</p>}
      {byArea.map((a) => {
        const ready = a.cases.filter((c) => c.ready);
        const all = ready.length > 0 && ready.every((c) => picked?.has(c.id));
        return (
          <fieldset key={a.id} className="tests-area">
            <legend>
              <label className="check"><input type="checkbox" checked={all} disabled={!ready.length}
                onChange={(e) => toggle(ready.map((c) => c.id), e.target.checked)} aria-label={`All of ${a.title}`} />
                <b>{a.title}</b></label>
            </legend>
            {a.cases.map((c) => (
              <label key={c.id} className={`check tests-case${c.ready ? "" : " off"}`}>
                <input type="checkbox" disabled={!c.ready} checked={c.ready && !!picked?.has(c.id)}
                  onChange={(e) => toggle([c.id], e.target.checked)} />
                <span className="grow">
                  <b>{c.title}</b> <span className="muted">{c.what}</span>
                  <span className="tests-case-meta">
                    {!c.ready && <span className="tag"><Lamp state="off" size="sm" /> {missingText(c.missing)}</span>}
                    {c.ready && c.last_seconds ? <span className="faint small">{seconds(c.last_seconds)} last time</span> : null}
                  </span>
                </span>
              </label>
            ))}
          </fieldset>
        );
      })}
      <div className="row wrap" style={{ marginTop: 12 }}>
        <button className="btn primary" disabled={!chosen.length || !!data.running || starting} onClick={run}>
          <Play size={15} /> Run {chosen.length} test{chosen.length === 1 ? "" : "s"}</button>
        <button className="btn quiet" onClick={() => setPicked(new Set(data.cases.filter((c) => c.ready).map((c) => c.id)))}>All that can run</button>
        <button className="btn quiet" onClick={() => setPicked(new Set())}>None</button>
        {data.running && <span className="faint small">A run is going.</span>}
      </div>

    </div>
  );
}

function outcome(r: RunSummary): LampState {
  if (r.status === "running") return "act";
  if (r.counts.fail || r.counts.error) return "fault";
  if (r.counts.partial) return "wait";
  return r.counts.pass ? "run" : "off";
}

function RunRow({ run, open, going, onToggle, onRemove, areaTitle, caseTitle }: { run: RunSummary; open: boolean;
  going: boolean; onToggle: () => void; onRemove: () => void; areaTitle: (id: string) => string;
  caseTitle: (id: string) => string }) {
  const c = run.counts;
  const parts = [c.pass && `${c.pass} passed`, c.partial && `${c.partial} in part`, c.fail && `${c.fail} failed`,
    c.error && `${c.error} errors`, c.skipped && `${c.skipped} skipped`, c.stopped && `${c.stopped} stopped`].filter(Boolean);
  const state = run.status === "running" ? `running, ${run.done} of ${run.total}` : run.status === "done" ? "" : run.status;
  return (
    <div className="tests-run">
      <div className="list-row">
        <Lamp state={outcome(run)} pulse={going} />
        <span className="grow">
          <b>{when(run.created_at)}</b> <span className="muted">{run.model_name}{run.thinking === true ? ", thinking on" :
            run.thinking === false ? ", thinking off" : ""}</span>
          <div className="meta">{[state, parts.join(", ") || (going ? "" : "no results"), run.done ? seconds(run.seconds) : ""]
            .filter(Boolean).join(" · ")}</div>
        </span>
        <div className="row tests-run-actions">
          <button className="btn small" onClick={onToggle} aria-expanded={open}>
            {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />} Results</button>
          <button className="btn small" disabled={!run.done} onClick={() => download(`/hub/api/tests/runs/${run.id}/export`)}
            title="A ZIP to send to Claude"><Download size={14} /> Export</button>
          {!going && <ConfirmButton onConfirm={onRemove} className="btn small danger" label="Delete the run and its chats"
            confirmText="Delete?"><X size={14} /></ConfirmButton>}
        </div>
      </div>
      {open && <RunDetails id={run.id} going={going} areaTitle={areaTitle} caseTitle={caseTitle} />}
    </div>
  );
}

function RunDetails({ id, going, areaTitle, caseTitle }: { id: string; going: boolean; areaTitle: (id: string) => string;
  caseTitle: (id: string) => string }) {
  const { toast } = useApp();
  const [rec, setRec] = useState<RunRecord | null>(null);
  const [shown, setShown] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setRec(await api<RunRecord>(`/hub/api/tests/runs/${id}`));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [id, toast]);
  useEffect(() => {
    load();
  }, [load, going]);                    // once more when the run ends: its last result
  useInterval(load, going ? 2500 : null);
  if (!rec) return <div className="tests-details faint">Loading</div>;
  const waiting = rec.cases.slice(rec.results.length + (rec.live ? 1 : 0));
  return (
    <div className="tests-details">
      {rec.results.length === 0 && !rec.live && <div className="faint">No results.</div>}
      {rec.results.map((r) => {
        const bad = r.checks.filter((c) => c.ok === false);
        return (
          <div key={r.id} className="tests-result">
            <button className="tests-result-head" onClick={() => setShown(shown === r.id ? null : r.id)} aria-expanded={shown === r.id}>
              <Lamp state={LAMP[r.status]} />
              <span className="grow">
                <b>{areaTitle(r.area)} - {r.title}</b> <span className="tag">{WORD[r.status]}</span>
                <span className="meta">{[r.score ? `${r.score[0]} of ${r.score[1]} checks` : "",
                  r.seconds != null ? seconds(r.seconds) : "", bad.map((c) => c.name).join("; ") || r.why].filter(Boolean).join(" · ")}</span>
              </span>
              {shown === r.id ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
            </button>
            {shown === r.id && <ResultView r={r} />}
          </div>
        );
      })}
      {rec.live && <div className="tests-result"><div className="tests-result-head"><Lamp state="act" />
        <span className="grow"><b>{rec.live.case ? caseTitle(rec.live.case) : "Starting"}</b>
          <span className="meta">{rec.live.activity}</span></span></div></div>}
      {waiting.length > 0 && <div className="faint small" style={{ padding: "6px 2px" }}>Next: {waiting.map(caseTitle).join("; ")}</div>}
    </div>
  );
}

function timing(r: TestResult): string {
  if (r.seconds == null) return "";
  const parts = [`waiting ${seconds(r.queue_seconds ?? 0)}`];
  if (r.load_seconds) parts.push(`loading ${seconds(r.load_seconds)}`);
  parts.push(`the model ${seconds(r.model_seconds ?? 0)} in ${r.model_steps ?? 0} step${r.model_steps === 1 ? "" : "s"}`
    + (r.tps ? ` at ${r.tps} tokens/s` : ""));
  if (r.tool_seconds) parts.push(`tools ${seconds(r.tool_seconds)}`);
  return `${seconds(r.seconds)}: ${parts.join(", ")}`;
}

function ResultView({ r }: { r: TestResult }) {
  return (
    <div className="tests-result-body">
      {r.chat_id && <a className="btn small" {...linkProps({ name: "chat", id: r.chat_id })}><ExternalLink size={14} /> Open the chat</a>}
      {r.seconds != null && <div className="small muted">{r.model ? `${r.model}, ` : ""}{timing(r)}</div>}
      {r.checks.length > 0 && (
        <ul className="tests-checks">
          {r.checks.map((c, i) => (
            <li key={i} data-ok={c.ok === null ? "info" : String(c.ok)}>
              {c.ok === null ? <Info size={14} aria-label="measured" /> : c.ok ? <Check size={14} aria-label="held" /> : <X size={14} aria-label="did not hold" />}
              <span><b>{c.name}</b>{c.level === "should" && <span className="faint"> (should)</span>}
                {c.detail && <span className="tests-check-detail">{c.detail}</span>}</span>
            </li>
          ))}
        </ul>
      )}
      {(r.approvals ?? []).length > 0 && <div className="small muted">{r.approvals!.map((a, i) => <div key={i}>It asked
        ({a.tool}): {a.asked.slice(0, 120)} - the test answered <b>{a.answer}</b>.</div>)}</div>}
      {(r.handed_over ?? []).map((h, i) => <div key={i} className="small muted">Handed over from {h.from} to {h.to}: {h.reason}</div>)}
      {(r.files ?? []).length > 0 && <div className="small muted">Files: {r.files!.join(", ")}</div>}
      {r.review && <div className="small"><b>Look at it yourself:</b> {r.review}</div>}
      {r.answer && <pre className="tests-answer">{r.answer}</pre>}
    </div>
  );
}
