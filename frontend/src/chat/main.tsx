// The phone chat page (/chat): its own small entry, sharing the Hub's look.
import { createRoot } from "react-dom/client";
import "@fontsource/barlow/400.css";
import "@fontsource/barlow/400-italic.css";
import "@fontsource/barlow/500.css";
import "@fontsource/barlow/600.css";
import "@fontsource/barlow-semi-condensed/500.css";
import "@fontsource/barlow-semi-condensed/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "katex/dist/katex.min.css";
import "../styles.css";
import "./chat.css";
import { PhoneChat } from "./PhoneChat";

createRoot(document.getElementById("root")!).render(<PhoneChat />);
