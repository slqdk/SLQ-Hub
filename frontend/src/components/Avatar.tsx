// A persona's picture in a circle - or the first letter of its name while it has none (0.74.0).
import { personaFile } from "../lib/personas";

export function Avatar({ persona, size = 28 }: { persona: { id: string; name: string; picture: string };
  size?: number }) {
  const style = { width: size, height: size, minWidth: size };
  return persona.picture
    ? <img className="avatar" src={personaFile(persona.id, persona.picture)} alt="" style={style} loading="lazy" />
    : <span className="avatar avatar-letter" style={{ ...style, fontSize: Math.round(size * 0.46) }} aria-hidden>
      {(persona.name.trim() || "?").slice(0, 1).toUpperCase()}</span>;
}
