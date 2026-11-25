// The chat's 3D button (0.87.0): a new design, or one of the folder's designs and meshes to open in the 3D editor.
// Light on purpose - the editor itself (three.js, manifold-3d) loads only when it opens.
import { useEffect, useState } from "react";
import { Box, FilePlus } from "lucide-react";
import { api, type TreeData } from "../lib/api";
import type { Folder } from "../components/Files";
import { DESIGN_FILE, OPENS_IN_3D } from "./files";

export function DesignMenu({ folder, onNew, onOpen }: { folder: Folder; onNew: () => void; onOpen: (path: string) => void }) {
  const [files, setFiles] = useState<string[] | null>(null);
  useEffect(() => {
    api<TreeData>(`/hub/api/folders/${folder.kind}/${folder.id}/tree`).then((t) => {
      const list = t.entries.filter((e) => !e.dir && OPENS_IN_3D.test(e.path) && !e.path.startsWith(".hub/"))
        .sort((a, b) => Number(DESIGN_FILE.test(b.path)) - Number(DESIGN_FILE.test(a.path)) || b.modified - a.modified);
      setFiles(list.slice(0, 30).map((e) => e.path));
    }, () => setFiles([]));
  }, [folder.kind, folder.id]);
  return (
    <>
      <button className="menu-item" onClick={onNew}><FilePlus size={15} />
        <span>New 3D design<small>Shapes, holes and text on the printer's plate - the assistant can work on it too</small></span></button>
      {files === null && <div className="menu-item faint">Looking in the folder</div>}
      {files && files.length > 0 && <div className="menu-sep" />}
      {files?.map((f) => (
        <button key={f} className="menu-item" onClick={() => onOpen(f)} title={f}>
          <Box size={15} /><span className="ellipsis mono">{f}</span></button>
      ))}
    </>
  );
}
