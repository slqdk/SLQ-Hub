// Help (0.20.0): what you can ask the assistant for, with examples - for the owner at /help/<section>.
import { Menu } from "lucide-react";
import { HelpContent } from "../components/Help";
import { setDraft } from "../lib/draft";
import type { Example } from "../lib/help";
import { navigate } from "../lib/router";
import { useApp } from "../state";

export function HelpView({ section }: { section?: string }) {
  const { setSidebarOpen, toast } = useApp();
  const tryIt = (e: Example) => {
    setDraft(e.text, e.skills);
    navigate("/");
    if (e.attach) toast(`Attach ${e.attach} with the + under the message box, then send.`, "run");
  };
  return (
    <div className="main">
      <div className="topbar">
        <button className="icon-btn menu-btn" onClick={() => setSidebarOpen(true)} aria-label="Open the menu"><Menu size={18} /></button>
        <h1>Help</h1>
      </div>
      <div className="scroll">
        <div className="page">
          <HelpContent section={section ?? "chat"} onSection={(id) => navigate({ name: "help", section: id }, true)}
            onTry={tryIt} onSettings={(tab) => navigate({ name: "settings", tab })} />
        </div>
      </div>
    </div>
  );
}
