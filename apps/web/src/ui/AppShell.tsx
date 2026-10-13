import { type KeyboardEvent, type MouseEvent, type ReactNode, useState } from "react";

import { navigationItems, routeMatches, type NavigationItem } from "../app-data";
import { Badge, Drawer, IconButton, Select } from "./primitives";
import { PixelIcon } from "./PixelIcon";

interface AppShellProps {
  path: string;
  children: ReactNode;
  runtimeState: "checking" | "ready" | "error";
  displayMode: "LOCAL WORKSTATION" | "PUBLIC PREVIEW / SAFE LAB MODE";
  onNavigate: (path: string) => void;
  onOpenCommand: () => void;
  onOpenHelp: () => void;
}

function isCurrent(path: string, item: NavigationItem) {
  const routeRoot = item.path === "/" ? "/" : `/${item.path.split("/")[1]}`;
  return routeMatches(path, routeRoot);
}

export function AppShell({ path, children, runtimeState, displayMode, onNavigate, onOpenCommand, onOpenHelp }: AppShellProps) {
  const [moreOpen, setMoreOpen] = useState(false);
  const follow = (event: MouseEvent<HTMLAnchorElement>, target: string) => {
    event.preventDefault();
    setMoreOpen(false);
    onNavigate(target);
  };
  const moveRailFocus = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const links = Array.from(event.currentTarget.querySelectorAll<HTMLAnchorElement>("[data-nav-link]"));
    const currentIndex = links.indexOf(document.activeElement as HTMLAnchorElement);
    if (currentIndex < 0) return;
    event.preventDefault();
    const direction = event.key === "ArrowDown" ? 1 : -1;
    links[(currentIndex + direction + links.length) % links.length]?.focus();
  };
  const primaryMobile = navigationItems.filter(({ path: itemPath }) => ["/", "/lab", "/runs"].includes(itemPath));
  const secondaryMobile = navigationItems.filter(({ path: itemPath }) => !["/", "/lab", "/runs"].includes(itemPath));

  return (
    <div className="app-shell">
      <header className="topbar">
        <a aria-label="AGENT//TRACE home" className="brand" href="/" onClick={(event) => follow(event, "/")}>
          <span className="brand__mark" aria-hidden="true">A//T</span>
          <span className="brand__copy"><strong>AGENT//TRACE</strong><small>VISUAL SYSTEMS STUDIO</small></span>
        </a>
        <div className="topbar__context">
          <span className="machine-label topbar__mode">
            <span className="topbar__mode-full">{displayMode}</span>
            <span className="topbar__mode-short">
              {displayMode === "PUBLIC PREVIEW / SAFE LAB MODE" ? "SAFE LAB" : "LOCAL"}
            </span>
          </span>
          <Select
            aria-label="Provider, fixed to Deterministic Mock in Batch A"
            disabled
            label="Provider"
            title="Batch A uses the deterministic offline provider"
            value="mock"
          >
            <option value="mock">Deterministic Mock</option>
          </Select>
          <Badge tone={runtimeState === "ready" ? "success" : runtimeState === "error" ? "error" : "warning"}>
            <span className="status-pixel" aria-hidden="true" />
            {runtimeState === "ready" ? "SYSTEM READY" : runtimeState === "error" ? "API OFFLINE" : "CHECKING"}
          </Badge>
        </div>
        <div className="topbar__actions">
          <IconButton icon="command" label="Open command palette" onClick={onOpenCommand} />
          <IconButton icon="help" label="Open keyboard help" onClick={onOpenHelp} />
        </div>
      </header>

      <aside className="desktop-rail">
        <nav aria-label="Primary" onKeyDown={moveRailFocus}>
          {navigationItems.map((item) => (
            <a
              aria-current={isCurrent(path, item) ? "page" : undefined}
              className="rail-link"
              data-nav-link
              href={item.path}
              key={item.path}
              onClick={(event) => follow(event, item.path)}
              title={item.description}
            >
              <PixelIcon name={item.icon} />
              <span className="rail-link__copy"><strong>{item.label}</strong><small>{item.description}</small></span>
              <span className="rail-link__short">{item.shortLabel}</span>
            </a>
          ))}
        </nav>
        <div className="rail-meta">
          <span>v0.1.0-rc.2</span>
          <span>{displayMode === "PUBLIC PREVIEW / SAFE LAB MODE" ? "SAFE LAB" : "LOCAL ONLY"}</span>
          {import.meta.env.DEV && <a href="/__components" onClick={(event) => follow(event, "/__components")}>UI CATALOG</a>}
        </div>
      </aside>

      <main className="app-main" id="main-content">{children}</main>

      <nav className="mobile-nav" aria-label="Mobile primary navigation">
        {primaryMobile.map((item) => (
          <a aria-current={isCurrent(path, item) ? "page" : undefined} href={item.path} key={item.path} onClick={(event) => follow(event, item.path)}>
            <PixelIcon name={item.icon} /><span>{item.label}</span>
          </a>
        ))}
        <button aria-expanded={moreOpen} aria-haspopup="dialog" onClick={() => setMoreOpen(true)} type="button">
          <PixelIcon name="more" /><span>More</span>
        </button>
      </nav>

      <Drawer open={moreOpen} onClose={() => setMoreOpen(false)} title="More workspaces">
        <nav className="drawer-nav" aria-label="Additional navigation">
          {secondaryMobile.map((item) => (
            <a aria-current={isCurrent(path, item) ? "page" : undefined} href={item.path} key={item.path} onClick={(event) => follow(event, item.path)}>
              <PixelIcon name={item.icon} />
              <span><strong>{item.label}</strong><small>{item.description}</small></span>
            </a>
          ))}
        </nav>
      </Drawer>
    </div>
  );
}
