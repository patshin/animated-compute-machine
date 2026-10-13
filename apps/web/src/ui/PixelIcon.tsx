import type { ReactNode } from "react";

export type PixelIconName =
  | "home"
  | "lab"
  | "scenarios"
  | "runs"
  | "evals"
  | "protocols"
  | "builder"
  | "glossary"
  | "settings"
  | "more"
  | "command"
  | "help"
  | "close";

interface PixelIconProps {
  name: PixelIconName;
  size?: number;
  className?: string;
}

export function PixelIcon({ name, size = 20, className }: PixelIconProps) {
  const common = {
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "square" as const,
    strokeLinejoin: "miter" as const
  };

  const glyph = {
    home: <><path {...common} d="M4 11 12 4l8 7" /><path {...common} d="M6 10v10h12V10M10 20v-6h4v6" /></>,
    lab: <><path {...common} d="M12 3 21 12 12 21 3 12Z" /><rect {...common} x="9" y="9" width="6" height="6" /></>,
    scenarios: <><rect {...common} x="5" y="4" width="14" height="12" /><path {...common} d="M8 8h8M8 12h5M3 8v12h14" /></>,
    runs: <><path {...common} d="M3 12h4l2-6 4 12 2-6h6" /><path {...common} d="M4 21h16" /></>,
    evals: <><path {...common} d="M5 19V9h3v10M11 19V4h3v15M17 19v-7h3v7" /><path {...common} d="M3 19h19" /></>,
    protocols: <><rect {...common} x="3" y="4" width="6" height="6" /><rect {...common} x="15" y="14" width="6" height="6" /><path {...common} d="M9 7h6l3 3v4M15 17H9l-3-3v-4" /></>,
    builder: <><path {...common} d="M12 3v18M3 12h18" /><rect {...common} x="4" y="4" width="5" height="5" /><rect {...common} x="15" y="15" width="5" height="5" /></>,
    glossary: <><path {...common} d="M4 4h7a3 3 0 0 1 3 3v13a3 3 0 0 0-3-3H4ZM20 4h-4a2 2 0 0 0-2 2" /><path {...common} d="M7 8h4M7 12h4" /></>,
    settings: <><rect {...common} x="8" y="8" width="8" height="8" /><path {...common} d="M10 3h4l1 3 3-1 2 3-2 2 3 2-1 4-3-1-1 3h-4l-1-3-3 1-2-3 2-2-3-2 1-4 3 1Z" /></>,
    more: <><rect {...common} x="3" y="10" width="4" height="4" /><rect {...common} x="10" y="10" width="4" height="4" /><rect {...common} x="17" y="10" width="4" height="4" /></>,
    command: <><path {...common} d="m5 7 5 5-5 5M12 17h7" /><rect {...common} x="2" y="3" width="20" height="18" /></>,
    help: <><path {...common} d="M8 8a4 4 0 1 1 5 4v3M13 19h.01" /><rect {...common} x="3" y="3" width="18" height="18" /></>,
    close: <><path {...common} d="M5 5 19 19M19 5 5 19" /></>
  } satisfies Record<PixelIconName, ReactNode>;

  return (
    <svg
      aria-hidden="true"
      className={className}
      height={size}
      shapeRendering="crispEdges"
      viewBox="0 0 24 24"
      width={size}
    >
      {glyph[name]}
    </svg>
  );
}
