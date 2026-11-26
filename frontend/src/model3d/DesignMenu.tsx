// The chat's 3D button (0.87.0): a new design, or one of the folder's designs and meshes to open in the 3D editor -
// the Hub's folder, or a person's chat's files on her device (0.93.0).
// Light on purpose - the editor itself (three.js, manifold-3d) loads only when it opens.
import { useEffect, useState } from "react";
import { Box, FilePlus } from "lucide-react";
import type { DesignFolder } from "./folder";
import { DESIGN_FILE, OPENS_IN_3D } from "./files";

export function DesignMenu({ store, onNew, onOpen }: { store: DesignFolder; onNew: () => void; onOpen: (path: string) => void }) {
  const [files, setFiles] = useState<string[] | null>(null);
  useEffect(() => {
    store.list().then((all) => {
      const list = all.filter((e) => OPENS_IN_3D.test(e.path) && !e.path.startsWith(".hub/"))
        .sort((a, b) => Number(DESIGN_FILE.test(b.path)) - Number(DESIGN_FILE.test(a.path)) || b.modified - a.modified);
      setFiles(list.slice(0, 30).map((e) => e.path));
    }, () => setFiles([]));
  }, [store]);
  return (
    <>
      <button className="menu-item" onClick={onNew}><FilePlus size={15} />
        <span>New 3D design<small>Shapes, holes and text on the printer's plate - the assistant can work on it too</small></span></button>
      {files === null && <div className="menu-item faint">Looking in {store.where}</div>}
      {files && files.length > 0 && <div className="menu-sep" />}
      {files?.map((f) => (
        <button key={f} className="menu-item" onClick={() => onOpen(f)} title={f}>
          <Box size={15} /><span className="ellipsis mono">{f}</span></button>
      ))}
    </>
  );
}
