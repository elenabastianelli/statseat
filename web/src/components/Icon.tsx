import { ICON_PATHS, type IconName } from "./icons.generated";

/** A Fluent UI System Icon (Microsoft, MIT licence), drawn in the current text colour. Decorative unless `label` is set. */
export function Icon({ name, size = 20, label, className }: {
  name: IconName; size?: number; label?: string; className?: string;
}) {
  return (
    <svg className={`icon ${className ?? ""}`} width={size} height={size} viewBox="0 0 24 24" fill="currentColor"
      role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      {ICON_PATHS[name].map((d, i) => <path key={i} d={d} />)}
    </svg>
  );
}

export type { IconName };
