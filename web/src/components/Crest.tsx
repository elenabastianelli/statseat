import { useState } from "react";
import { api } from "../api/client";
import type { TeamSheet } from "../api/types";

/** A club's crest from /api/crests/{id}; the placeholder shield (club colours, 3 letters) if the image does not load. */
export function Crest({ teamId, name, team, size = "md" }: { teamId: string; name: string; team?: TeamSheet; size?: "md" | "lg" }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <span className="crest" role="img" aria-label={name} style={{ background: team?.colours.primary, color: team?.colours.secondary }}>
        {(team?.short_name ?? name).slice(0, 3).toUpperCase()}
      </span>
    );
  }
  return <img className={`crest-img ${size}`} src={api.crestUrl(teamId)} alt={name} onError={() => setFailed(true)} />;
}
