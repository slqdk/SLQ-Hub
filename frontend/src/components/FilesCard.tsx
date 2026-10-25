// The files an answer made or changed, under the answer: each ready to play or
// look at, open, download, or put in the next message. The same card for the
// owner's chats (files in the working folder) and a person's (files on the device).
import { useEffect, useState, type ReactNode } from "react";
import { Check, Download, ExternalLink, File as FileIcon, FileArchive, FileAudio, FileCode, FileImage, FileSpreadsheet,
  FileText, FileVideo, FolderOpen, MessageSquarePlus, Play } from "lucide-react";
import type { ProducedFile, RunRecord } from "../lib/api";
import { bytes } from "../lib/format";
import type { Unfinished } from "../lib/turns";
import { Lamp } from "./ui";

const SHOWN = 6;                            // rows before "Show all"

export interface FileHandlers {
  src: (f: ProducedFile) => string | null | Promise<string | null>;   // for the player, the picture, Open in a tab
  download: (f: ProducedFile) => void;
  open?: (f: ProducedFile) => void;                             // in the side panel
  add?: (f: ProducedFile) => void;                              // put it in the next message
  added?: (f: ProducedFile) => boolean;
}

/** The files a run made or changed (its "files" steps). */
export function producedFiles(run: RunRecord | undefined): ProducedFile[] {
  return (run?.steps ?? []).flatMap((s) => (s.type === "files" ? s.files ?? [] : []));
}

type Kind = "audio" | "video" | "image" | "pdf" | "text" | "code" | "sheet" | "archive" | "other";

function kindOf(f: ProducedFile): Kind {
  const m = (f.mime || "").toLowerCase();
  const ext = f.path.split(".").pop()?.toLowerCase() ?? "";
  if (m.startsWith("audio/")) return "audio";
  if (m.startsWith("video/")) return "video";
  if (m.startsWith("image/") && ext !== "svg") return "image";
  if (m === "application/pdf") return "pdf";
  if (["csv", "tsv", "xlsx", "xls", "ods"].includes(ext)) return "sheet";
  if (["zip", "tar", "gz", "tgz", "7z", "rar", "xz", "bz2"].includes(ext)) return "archive";
  if (["py", "js", "ts", "tsx", "jsx", "html", "css", "json", "sh", "c", "cpp", "h", "cs", "java", "go", "rs", "st",
    "xml", "yaml", "yml", "toml", "sql", "svg"].includes(ext)) return "code";
  if (m.startsWith("text/") || ["md", "txt", "log", "rtf", "docx", "doc", "odt"].includes(ext)) return "text";
  return "other";
}

const ICON: Record<Kind, ReactNode> = {
  audio: <FileAudio size={18} />, video: <FileVideo size={18} />, image: <FileImage size={18} />,
  pdf: <FileText size={18} />, text: <FileText size={18} />, code: <FileCode size={18} />,
  sheet: <FileSpreadsheet size={18} />, archive: <FileArchive size={18} />, other: <FileIcon size={18} />,
};

/** A turn that is not done (0.22.0): why, and Continue - which goes on from there in a new turn. */
export function UnfinishedLine({ why, onContinue }: { why: Unfinished; onContinue?: () => void }) {
  return (
    <div className="unfinished-line" role="status">
      <Lamp state="wait" />
      <span className="grow">{why.reason === "limit"
        ? <><b>Not finished:</b> the turn stopped after {why.steps} steps, the most one turn may take.</>
        : <><b>Not finished:</b> it was stopped before it was done.</>}</span>
      {onContinue && <button className="btn small primary" onClick={onContinue}><Play size={14} /> Continue</button>}
    </div>
  );
}

/** The same for an answer that made no files. */
export function UnfinishedNote({ why, onContinue }: { why: Unfinished; onContinue?: () => void }) {
  return <div className="unfinished-note"><UnfinishedLine why={why} onContinue={onContinue} /></div>;
}

export function FilesCard({ files, handlers, unfinished, onContinue }: { files: ProducedFile[]; handlers: FileHandlers;
  unfinished?: Unfinished | null; onContinue?: () => void }) {
  const [all, setAll] = useState(false);
  if (!files.length) return null;
  const shown = all || files.length <= SHOWN ? files : files.slice(0, SHOWN - 1);
  return (
    <section className={`files-card${unfinished ? " unfinished" : ""}`} aria-label="Files from this answer">
      {unfinished && <UnfinishedLine why={unfinished} onContinue={onContinue} />}
      <div className="files-card-head">
        {unfinished ? (files.length === 1 ? "The file so far" : `${files.length} files so far`)
          : files.length === 1 ? "File from this answer" : `${files.length} files from this answer`}
      </div>
      {shown.map((f) => <FileRow key={f.path} file={f} handlers={handlers} />)}
      {shown.length < files.length && (
        <button className="files-card-more" onClick={() => setAll(true)}>Show all {files.length} files</button>
      )}
    </section>
  );
}

function FileRow({ file, handlers }: { file: ProducedFile; handlers: FileHandlers }) {
  const kind = kindOf(file);
  const media = !file.skipped && (kind === "audio" || kind === "video" || kind === "image");
  const [src, setSrc] = useState<string | null>(null);
  const [gone, setGone] = useState(false);
  useEffect(() => {
    let alive = true;
    if (!media) return;
    Promise.resolve(handlers.src(file)).then((u) => {
      if (!alive) return;
      if (u) setSrc(u);
      else setGone(true);
    }, () => alive && setGone(true));
    return () => {
      alive = false;
    };
  }, [file.path, file.size, media, handlers.src]); // eslint-disable-line react-hooks/exhaustive-deps
  const cut = file.path.lastIndexOf("/");
  const name = file.path.slice(cut + 1);
  const added = handlers.added?.(file) ?? false;
  const openTab = async () => {
    const u = await Promise.resolve(handlers.src(file));
    if (u) window.open(u, "_blank", "noopener");
    else setGone(true);
  };
  return (
    <div className="files-card-row">
      <span className="kind" aria-hidden>{ICON[kind]}</span>
      <div className="files-card-main">
        <div className="files-card-name" title={file.path}>
          <span className="ellipsis">{name}</span>
          {cut > 0 && <span className="faint ellipsis dir">{file.path.slice(0, cut)}</span>}
        </div>
        <div className="small faint num">
          {bytes(file.size)} · {file.skipped ? file.skipped : file.new === false ? "changed" : "new"}
          {gone && " · not there any more"}
        </div>
        {media && src && !gone && (
          kind === "audio" ? <audio className="files-card-audio" controls preload="metadata" src={src}
            onError={() => setGone(true)} aria-label={`Play ${name}`} />
          : kind === "video" ? <video className="files-card-video" controls preload="metadata" src={src}
            onError={() => setGone(true)} aria-label={`Play ${name}`} />
          : <button className="files-card-pic" onClick={() => (handlers.open ? handlers.open(file) : openTab())}
              aria-label={`Show ${name}`}>
              <img src={src} alt={name} loading="lazy" onError={() => setGone(true)} /></button>
        )}
      </div>
      {!file.skipped && !gone && (
        <div className="files-card-actions">
          {kind === "pdf" ? (
            <button className="btn small" onClick={openTab}><ExternalLink size={14} /> Open</button>
          ) : handlers.open && (
            <button className="btn small" onClick={() => handlers.open?.(file)}><FolderOpen size={14} /> Open</button>
          )}
          <button className="btn small" onClick={() => handlers.download(file)} aria-label={`Download ${name}`}>
            <Download size={14} /> Download</button>
          {handlers.add && (
            <button className={`btn small ${added ? "picked" : ""}`} aria-pressed={added} onClick={() => handlers.add?.(file)}
              title={added ? "In your next message" : "Put this file in your next message"}>
              {added ? <><Check size={14} /> In the message</> : <><MessageSquarePlus size={14} /> Add to chat</>}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
