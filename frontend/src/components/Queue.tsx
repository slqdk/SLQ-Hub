// The GPU plate in the sidebar, and the queue it opens.
import { useState } from "react";
import { api, type QueueJob, type QueueSnapshot } from "../lib/api";
import { residencyName, seconds } from "../lib/format";
import { navigate } from "../lib/router";
import { useApp } from "../state";
import { Lamp, Modal, type LampState } from "./ui";

function slotsFor(snap: QueueSnapshot | null, slotsByModel: Record<string, number>): number {
  const cur = snap?.current ?? "";
  if (cur.startsWith("llm:")) return slotsByModel[cur.slice(4)] ?? 1;
  return cur ? 1 : 0;
}

export function plateState(q: QueueSnapshot | null): { lamp: LampState; text: string } {
  if (!q) return { lamp: "off", text: "Connecting" };
  if (q.switching) return { lamp: "wait", text: "Switching the card" };
  const run = q.running.length;
  const wait = q.pending.length;
  if (run && wait) return { lamp: "run", text: `${run} working, ${wait} waiting` };
  if (run) {
    const j = q.running[0];
    return { lamp: "run", text: run > 1 ? `${run} working` : `Working: ${ownerName(j.owner)}` };
  }
  if (wait) return { lamp: "wait", text: `${wait} waiting` };
  if (q.last_error) return { lamp: "fault", text: "Last load failed" };
  return q.current && !q.current.startsWith("idle") ? { lamp: "run", text: "Ready" } : { lamp: "off", text: "Idle" };
}

export function ownerName(owner: string): string {
  if (owner === "web") return "web chat";
  if (owner === "system") return "the Hub";
  if (owner === "library") return "library indexing";
  return owner;
}

export function GpuPlate() {
  const { queue, modelNames, models } = useApp();
  const [open, setOpen] = useState(false);
  const slotsByModel: Record<string, number> = {};
  for (const m of models?.llm ?? []) slotsByModel[m.id] = Number((m.server as { parallel?: number } | undefined)?.parallel ?? 1);
  const slots = slotsFor(queue, slotsByModel);
  const st = plateState(queue);
  const busy = queue?.running.length ?? 0;
  return (
    <>
      <button className="plate" onClick={() => setOpen(true)} aria-label="The GPU queue">
        <Lamp state={st.lamp} size="lg" pulse={busy > 0} />
        <span className="plate-name">{residencyName(queue?.switching ?? queue?.current, modelNames)}</span>
        <span className="plate-slots" aria-label={`${busy} of ${slots} slots in use`}>
          {Array.from({ length: Math.max(slots, 0) }, (_, i) => (
            <Lamp key={i} size="sm" state={i < busy ? "run" : "off"} />
          ))}
        </span>
        <span className="plate-state">{st.text}</span>
      </button>
      {open && <QueueModal onClose={() => setOpen(false)} />}
    </>
  );
}

function JobRow({ job, onCancel }: { job: QueueJob; onCancel: (id: number) => void }) {
  const { modelNames } = useApp();
  const running = job.state === "running";
  const what = residencyName(job.switching_to ?? job.need, modelNames);
  return (
    <div className="job">
      <Lamp state={running ? "run" : "wait"} pulse={running} />
      <div className="job-label">{job.label || ownerName(job.owner)}</div>
      <button className="btn quiet small" onClick={() => onCancel(job.id)}>{running ? "Stop" : "Remove"}</button>
      <div className="job-meta">
        {ownerName(job.owner)}, {job.priority}, {what}
        {job.lease ? ", holds the card for the whole task" : ""}
        {running ? `, running ${seconds(job.running_s)}` : `, waited ${seconds(job.waited_s)}`}
        {job.position ? `, number ${job.position} in line` : ""}
      </div>
    </div>
  );
}

export function QueueModal({ onClose }: { onClose: () => void }) {
  const { queue, modelNames, toast } = useApp();
  const cancel = async (id: number) => {
    try {
      await api(`/hub/api/queue/${id}/cancel`, { method: "POST" });
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <Modal title="The GPU queue" onClose={onClose} footer={
      <>
        <button className="btn" onClick={() => { onClose(); navigate("/settings/models"); }}>Models</button>
        <button className="btn primary" onClick={onClose}>Close</button>
      </>}>
      <p className="muted" style={{ margin: 0 }}>
        One card serves everything. On it now: <b>{residencyName(queue?.current, modelNames)}</b>
        {queue?.switching ? `, switching to ${residencyName(queue.switching, modelNames)}` : ""}.
        Requests for the loaded model share its slots; anything else waits until the card switches.
      </p>
      {queue?.last_error && <div className="callout fault">The last load failed: {queue.last_error}</div>}
      <div>
        <div className="label" style={{ marginBottom: 6 }}>Working</div>
        <div className="queue-list">
          {queue?.running.length ? queue.running.map((j) => <JobRow key={j.id} job={j} onCancel={cancel} />)
            : <div className="muted small">Nothing is running.</div>}
        </div>
      </div>
      <div>
        <div className="label" style={{ marginBottom: 6 }}>Waiting</div>
        <div className="queue-list">
          {queue?.pending.length ? queue.pending.map((j) => <JobRow key={j.id} job={j} onCancel={cancel} />)
            : <div className="muted small">Nobody is waiting.</div>}
        </div>
      </div>
    </Modal>
  );
}
