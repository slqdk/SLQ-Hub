import { memo, useLayoutEffect, useMemo, useRef, type TouchEvent, type UIEvent, type WheelEvent } from "react";

// While a model thinks, its text follows the end like a terminal: new lines appear at the bottom and nothing
// already on screen moves. Until 0.28.2 it showed the last 1500 characters from their start - on a phone that is
// taller than the box, so the words crept backwards as the front was cut off, and the newest ones were out of sight
// below. Each line is its own block, so a new token lays out only the last line; the oldest lines leave the top
// whole (never part of a line, which would reflow what is on screen).
const LIVE_LINES = 400;

const Line = memo(function Line({ text }: { text: string }) {
  return <div>{text || " "}</div>;
});

export function ThinkingText({ text, live }: { text: string; live?: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const follow = useRef(true);            // until the reader scrolls up to read, and again once back at the end
  const touchY = useRef(0);
  const lines = useMemo(() => {
    if (!live) return null;
    const all = text.split("\n");
    const first = Math.max(0, all.length - LIVE_LINES);
    return { first, lines: all.slice(first) };
  }, [text, live]);
  useLayoutEffect(() => {
    const el = box.current;
    if (el && live && follow.current) el.scrollTop = el.scrollHeight;
  }, [text, live]);
  // Scrolling up stops the following at once - a token arriving before the scroll event must not pull it back.
  const events = live ? {
    onScroll: (e: UIEvent<HTMLDivElement>) => {
      const el = e.currentTarget;
      follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    },
    onWheel: (e: WheelEvent) => { if (e.deltaY < 0) follow.current = false; },
    onTouchStart: (e: TouchEvent) => { touchY.current = e.touches[0]?.clientY ?? 0; },
    onTouchMove: (e: TouchEvent) => {
      if ((e.touches[0]?.clientY ?? 0) > touchY.current + 4) follow.current = false;   // a finger moving down
    },
  } : {};
  return (
    <div className="thinking-body" ref={box} {...events}>
      {lines ? lines.lines.map((l, i) => <Line key={lines.first + i} text={l} />) : text}
    </div>
  );
}
