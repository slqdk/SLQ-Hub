// Archived chats (0.69.0): out of the chat lists, kept as they are - on the Archived page and in the search, and
// back in the lists when unarchived or worked in again (a new message).
import { api, type Chat } from "./api";

type ToastFn = (text: string, kind?: "info" | "fault" | "run", action?: { label: string; run: () => void }) => void;

/** Archive or unarchive; an open chat view hears of it ("hub:archived") and shows it. */
export async function setArchived(id: number, archived: boolean): Promise<Chat> {
  const chat = await api<Chat>(`/hub/api/chats/${id}`, { method: "PATCH", body: { archived } });
  window.dispatchEvent(new CustomEvent("hub:archived", { detail: { id, archived } }));
  return chat;
}

/** Archive a chat, refresh the lists, and say so with an Undo. */
export async function archiveWithUndo(id: number, title: string, refreshLists: () => Promise<void>,
  toast: ToastFn): Promise<void> {
  try {
    await setArchived(id, true);
    await refreshLists();
    toast(`Archived "${title}" - find it under Archived.`, "info", {
      label: "Undo",
      run: () => {
        setArchived(id, false).then(refreshLists, (e) => toast((e as Error).message, "fault"));
      },
    });
  } catch (e) {
    toast((e as Error).message, "fault");
  }
}

/** Unarchive: back in the lists, at its place by its last activity. */
export async function unarchive(id: number, refreshLists: () => Promise<void>, toast: ToastFn): Promise<boolean> {
  try {
    await setArchived(id, false);
    await refreshLists();
    return true;
  } catch (e) {
    toast((e as Error).message, "fault");
    return false;
  }
}
