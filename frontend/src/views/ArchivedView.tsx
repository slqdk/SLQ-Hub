// Archived chats (0.69.0): out of the chat lists, kept as they are. Open one, or put it back in the list; and the
// chats a newer chat continues ("Continue in a new chat") can be archived in one go.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Archive, ArchiveRestore, Menu } from "lucide-react";
import { api, type ChatListItem } from "../lib/api";
import { unarchive } from "../lib/archive";
import { ago } from "../lib/format";
import { linkProps } from "../lib/router";
import { useApp } from "../state";
import { Empty } from "../components/ui";

export function ArchivedView() {
  const { setSidebarOpen, refreshLists, toast } = useApp();
  const [chats, setChats] = useState<ChatListItem[] | null>(null);
  const [continued, setContinued] = useState(0);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [list, more] = await Promise.all([
        api<ChatListItem[]>("/hub/api/chats", { query: { all: true, archived: true, limit: 1000 } }),
        api<{ count: number }>("/hub/api/chats/continued"),
      ]);
      setChats(list);
      setContinued(more.count);
    } catch (e) {
      setChats([]);
      toast((e as Error).message, "fault");
    }
  }, [toast]);
  useEffect(() => {
    load();
  }, [load]);

  const shown = useMemo(() => {
    const words = q.trim().toLowerCase();
    if (!chats || !words) return chats;
    return chats.filter((c) => `${c.title} ${c.project_name ?? ""}`.toLowerCase().includes(words));
  }, [chats, q]);

  const restore = async (c: ChatListItem) => {
    if (await unarchive(c.id, refreshLists, toast)) {
      setChats((list) => list?.filter((x) => x.id !== c.id) ?? null);
      toast(`"${c.title}" is back in the list.`);
    }
  };
  const tidy = async () => {
    setBusy(true);
    try {
      const r = await api<{ archived: number }>("/hub/api/chats/continued/archive", { body: {} });
      await Promise.all([refreshLists(), load()]);
      toast(`Archived ${r.archived} chat${r.archived === 1 ? "" : "s"} continued in a newer one.`);
    } catch (e) {
      toast((e as Error).message, "fault");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="main">
      <div className="topbar">
        <button className="icon-btn menu-btn" onClick={() => setSidebarOpen(true)} aria-label="Open the menu"><Menu size={18} /></button>
        <h1>Archived chats</h1>
      </div>
      <div className="scroll"><div className="page stack">
        <p className="lead">Out of the chat list, kept as they are. Open one to read it or go on with it - a new message
          puts it back in the list, and so does <ArchiveRestore size={14} style={{ verticalAlign: "-2px" }} /> here.
          Archive a chat with <Archive size={14} style={{ verticalAlign: "-2px" }} /> beside it in the list, or from its
          ⋯ menu; the search in the list finds archived chats too.</p>
        {continued > 0 && (
          <div className="callout row">
            <span className="grow">{continued} chat{continued === 1 ? " is" : "s are"} still in the list though a newer
              chat continues {continued === 1 ? "it" : "them"} (Continue in a new chat). New ones are archived by
              themselves.</span>
            <button className="btn small" disabled={busy} onClick={tidy}><Archive size={15} /> Archive {continued === 1 ? "it" : "them"}</button>
          </div>
        )}
        {chats && chats.length > 8 && <input className="input" placeholder="Filter by title or project" value={q}
          onChange={(e) => setQ(e.target.value)} />}
        <div className="list">
          {shown === null ? <Empty>Loading</Empty> : shown.length === 0 ? (
            <Empty>{chats && chats.length ? "No archived chat matches." : "No archived chats."}</Empty>
          ) : shown.map((c) => (
            <div key={c.id} className="list-row">
              <a className="grow ellipsis" style={{ color: "inherit", textDecoration: "none" }} {...linkProps({ name: "chat", id: c.id })}
                title={c.title}><b>{c.title}</b></a>
              {c.project_name && <a className="tag" {...linkProps({ name: "project", id: c.project_id! })}>{c.project_name}</a>}
              <span className="meta" title={`Started ${new Date(c.created_at).toLocaleString(undefined,
                { dateStyle: "medium", timeStyle: "short" })}`}>{ago(c.updated_at)}</span>
              <button className="btn small" onClick={() => restore(c)} title="Back in the chat list">
                <ArchiveRestore size={15} /> Unarchive</button>
            </div>
          ))}
        </div>
      </div></div>
    </div>
  );
}
