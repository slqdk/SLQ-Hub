// Move a chat into a project (0.31.0): a new project, or one there is. The chat's files go into the project's folder.
import { useState } from "react";
import { FolderInput } from "lucide-react";
import { api, type Project } from "../lib/api";
import { useApp } from "../state";
import { Modal } from "./ui";

interface Moved { chat: { id: number; project_id: number | null }; project: Project; copied: number; same: number }

/** The chat's title without the "(2)" a continued chat gets: the new project's first name. */
function projectName(title: string): string {
  return title.replace(/\s*\(\d+\)\s*$/, "").trim() || title;
}

export function MoveToProject({ chatId, title, onMoved, onClose }: { chatId: number; title: string;
  onMoved: (r: Moved) => void; onClose: () => void }) {
  const { projects } = useApp();
  const open = projects.filter((p) => !p.archived);
  const [choice, setChoice] = useState<"new" | number>("new");
  const [name, setName] = useState(projectName(title));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const ready = choice !== "new" || name.trim().length > 0;

  const move = async () => {
    setBusy(true);
    setError("");
    try {
      const body = choice === "new" ? { new_project: name.trim() } : { project_id: choice };
      onMoved(await api<Moved>(`/hub/api/chats/${chatId}/move`, { body }));
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <Modal title="Move to a project" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!ready || busy} onClick={move}>
        <FolderInput size={15} /> {busy ? "Moving..." : "Move"}</button>
    </>}>
      <p className="muted small" style={{ marginTop: 0 }}>The chat's files go into the project's folder, and from its next
        answer on it works there - with the project's instructions, files, skills and sync. Nothing in the project is
        overwritten.</p>
      <div className="list" role="radiogroup" aria-label="Project" style={{ maxHeight: 320, overflow: "auto" }}>
        <label className="list-row choice-row">
          <input type="radio" name="move-to" checked={choice === "new"} onChange={() => setChoice("new")} />
          <span className="choice-main"><b>A new project</b></span>
        </label>
        {choice === "new" && (
          <div className="list-row" style={{ paddingLeft: 36 }}>
            <input className="input" aria-label="The new project's name" value={name} autoFocus
              onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && ready && !busy) move(); }} />
          </div>
        )}
        {open.map((p) => (
          <label key={p.id} className="list-row choice-row">
            <input type="radio" name="move-to" checked={choice === p.id} onChange={() => setChoice(p.id)} />
            <span className="choice-main">{p.name}</span>
          </label>
        ))}
      </div>
      {error && <p className="small" role="alert" style={{ color: "var(--fault)" }}>{error}</p>}
    </Modal>
  );
}
