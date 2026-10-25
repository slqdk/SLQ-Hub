// A small history-API router: the app has a handful of routes.
import { useEffect, useState } from "react";

export type Route =
  | { name: "home" }
  | { name: "chat"; id: number }
  | { name: "project"; id: number; tab?: string }
  | { name: "project-new-chat"; id: number }
  | { name: "libraries"; id?: number }
  | { name: "skills" }
  | { name: "settings"; tab: string }
  | { name: "help"; section?: string };

export function parse(path: string): Route {
  let m = path.match(/^\/c\/(\d+)\/?$/);
  if (m) return { name: "chat", id: Number(m[1]) };
  m = path.match(/^\/p\/(\d+)\/new\/?$/);
  if (m) return { name: "project-new-chat", id: Number(m[1]) };
  m = path.match(/^\/p\/(\d+)(?:\/(\w+))?\/?$/);
  if (m) return { name: "project", id: Number(m[1]), tab: m[2] };
  m = path.match(/^\/libraries(?:\/(\d+))?\/?$/);
  if (m) return { name: "libraries", id: m[1] ? Number(m[1]) : undefined };
  if (/^\/(settings\/)?skills\/?$/.test(path)) return { name: "skills" };    // skills were under Settings before 0.23.0
  m = path.match(/^\/settings(?:\/([\w-]+))?\/?$/);
  if (m) return { name: "settings", tab: m[1] ?? "models" };
  m = path.match(/^\/help(?:\/([\w-]+))?\/?$/);
  if (m) return { name: "help", section: m[1] };
  return { name: "home" };
}

export function href(r: Route): string {
  switch (r.name) {
    case "home": return "/";
    case "chat": return `/c/${r.id}`;
    case "project": return r.tab ? `/p/${r.id}/${r.tab}` : `/p/${r.id}`;
    case "project-new-chat": return `/p/${r.id}/new`;
    case "libraries": return r.id ? `/libraries/${r.id}` : "/libraries";
    case "skills": return "/skills";
    case "settings": return `/settings/${r.tab}`;
    case "help": return r.section ? `/help/${r.section}` : "/help";
  }
}

export function navigate(to: string | Route, replace = false): void {
  const url = typeof to === "string" ? to : href(to);
  if (url === location.pathname) return;
  if (replace) history.replaceState(null, "", url);
  else history.pushState(null, "", url);
  window.dispatchEvent(new Event("hub:navigate"));
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(location.pathname));
  useEffect(() => {
    const on = () => setRoute(parse(location.pathname));
    window.addEventListener("popstate", on);
    window.addEventListener("hub:navigate", on);
    return () => {
      window.removeEventListener("popstate", on);
      window.removeEventListener("hub:navigate", on);
    };
  }, []);
  return route;
}

/** An <a> that navigates without reloading (and still opens in a new tab with ctrl/cmd). */
export function linkProps(to: string | Route) {
  const url = typeof to === "string" ? to : href(to);
  return {
    href: url,
    onClick: (e: React.MouseEvent) => {
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      navigate(url);
    },
  };
}
