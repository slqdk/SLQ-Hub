// The help page's content (0.20.0): what you can ask for, section by section, with examples that start a new chat
// - and whether each thing works on this Hub now (GET /hub/api/help). The owner's page and a person's use it.
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Brain, Check, Clapperboard, Copy, FileText, Image as ImageIcon, MessageSquare, MessageSquarePlus, Music,
  Paperclip, Search, Server, Settings, SquareTerminal, X } from "lucide-react";
import { api } from "../lib/api";
import { NEEDS, SECTIONS, has, searchText, visible, type Example, type HelpCaps, type HelpItem, type HelpSection,
  type Need } from "../lib/help";
import { Lamp, copyText } from "./ui";

const ICONS: Record<string, ReactNode> = {
  chat: <MessageSquare size={15} />, documents: <FileText size={15} />, images: <ImageIcon size={15} />,
  sound: <Music size={15} />, video: <Clapperboard size={15} />, code: <SquareTerminal size={15} />,
  memory: <Brain size={15} />, hub: <Server size={15} />,
};

export function useHelpCaps(): HelpCaps | null {
  const [caps, setCaps] = useState<HelpCaps | null>(null);
  useEffect(() => {
    let alive = true;
    api<HelpCaps>("/hub/api/help").then((c) => alive && setCaps(c), () => undefined);
    return () => {
      alive = false;
    };
  }, []);
  return caps;
}

export function HelpContent({ section, onSection, onTry, onSettings }: {
  section: string;
  onSection: (id: string) => void;
  onTry: (example: Example) => void;
  onSettings?: (tab: string) => void;          // the owner: open Settings where the missing thing is got
}) {
  const caps = useHelpCaps();
  const [q, setQ] = useState("");
  const member = caps?.member ?? false;
  const sections = useMemo(() => SECTIONS.map((s) => ({ ...s, title: member && s.memberTitle ? s.memberTitle : s.title,
    items: s.items.filter((i) => visible(i, member)) })).filter((s) => s.items.length > 0), [member]);
  const current = sections.find((s) => s.id === section) ?? sections[0];
  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const found = words.length ? sections.map((s) => ({ ...s, items: s.items.filter((i) => words.every((w) => searchText(i).includes(w))) }))
    .filter((s) => s.items.length > 0) : null;

  return (
    <div className="help">
      <p className="lead">What you can ask the assistant for, with examples. <b>Try</b> puts an example in a new chat:
        change it if you like, attach what it needs, and send. Write in Danish or English.</p>
      <label className="help-search">
        <Search size={16} className="faint" aria-hidden />
        <input className="input" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search the help"
          placeholder="Search: picture, song, PDF, video, code..." onKeyDown={(e) => e.key === "Escape" && setQ("")} />
        {q && <button className="icon-btn small" onClick={() => setQ("")} aria-label="Clear the search"><X size={14} /></button>}
      </label>
      {found ? (
        found.length === 0 ? <p className="muted">Nothing in the help matches. Ask the assistant anyway - it may well
          manage.</p> : found.map((s) => (
          <div key={s.id}>
            <h2 className="help-section-title">{s.title}</h2>
            {s.items.map((i) => <Item key={i.id} item={i} caps={caps} onTry={onTry} onSettings={onSettings} />)}
          </div>
        ))
      ) : (
        <>
          <div className="help-sections" role="tablist" aria-label="Help sections">
            {sections.map((s) => <button key={s.id} className="help-chip" role="tab" aria-selected={s.id === current.id}
              onClick={() => onSection(s.id)}>{ICONS[s.id]}{s.title}</button>)}
          </div>
          <SectionView section={current} caps={caps} onTry={onTry} onSettings={onSettings} />
        </>
      )}
    </div>
  );
}

function SectionView({ section, caps, onTry, onSettings }: { section: HelpSection; caps: HelpCaps | null;
  onTry: (example: Example) => void; onSettings?: (tab: string) => void }) {
  return (
    <div role="tabpanel" aria-label={section.title}>
      <h2 className="help-section-title help-section-head">{ICONS[section.id]}{section.title}</h2>
      <p className="help-intro">{section.intro}</p>
      {section.items.map((i) => <Item key={i.id} item={i} caps={caps} onTry={onTry} onSettings={onSettings} />)}
    </div>
  );
}

function Item({ item, caps, onTry, onSettings }: { item: HelpItem; caps: HelpCaps | null;
  onTry: (example: Example) => void; onSettings?: (tab: string) => void }) {
  const needs = item.needs ?? [];
  const missing: Need[] = caps ? needs.filter((n) => !has(caps, n)) : [];
  const ready = !!caps && missing.length === 0;
  const first = missing[0] ? NEEDS[missing[0]] : null;
  return (
    <section className="help-item" aria-labelledby={`help-${item.id}`}>
      <div className="help-item-head">
        <h3 id={`help-${item.id}`}>{item.title}</h3>
        {needs.length > 0 && caps && (ready
          ? <span className="tag"><Lamp state="run" size="sm" /> Ready</span>
          : <span className="tag"><Lamp state="off" size="sm" /> {first?.missing}</span>)}
      </div>
      <p className="help-what">{item.what}</p>
      {first && caps && (
        <div className="callout help-missing">
          {caps.member ? <>{first.missing}. Ask the owner of the Hub.</> : first.get}
          {!caps.member && onSettings && first.tab && (
            <button className="btn small" onClick={() => onSettings(first.tab!)}><Settings size={14} /> Open Settings</button>
          )}
        </div>
      )}
      {item.how && <ul className="help-how">{item.how.map((h) => <li key={h}>{h}</li>)}</ul>}
      {item.examples && (
        <ul className="help-examples" aria-label={`Examples: ${item.title}`}>
          {item.examples.map((e) => <ExampleRow key={e.text} example={e} canTry={!needs.length || ready || !caps}
            why={first?.missing} onTry={onTry} />)}
        </ul>
      )}
      {item.tips && <ul className="help-tips">{item.tips.map((t) => <li key={t}>{t}</li>)}</ul>}
    </section>
  );
}

function ExampleRow({ example, canTry, why, onTry }: { example: Example; canTry: boolean; why?: string;
  onTry: (example: Example) => void }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(t);
  }, [copied]);
  return (
    <li className="help-example">
      <div className="help-example-text">
        <span className="help-prompt">{example.text}</span>
        {example.attach && <span className="help-attach"><Paperclip size={13} aria-hidden /> Attach {example.attach}</span>}
      </div>
      <div className="help-example-actions">
        <button className="btn small" disabled={!canTry} onClick={() => onTry(example)}
          title={canTry ? "Put it in a new chat" : why} aria-label={`Try: ${example.text}`}>
          <MessageSquarePlus size={14} /> Try</button>
        <button className="icon-btn small" aria-label={`Copy: ${example.text}`} title="Copy"
          onClick={async () => setCopied(await copyText(example.text))}>
          {copied ? <Check size={14} /> : <Copy size={14} />}</button>
      </div>
    </li>
  );
}
