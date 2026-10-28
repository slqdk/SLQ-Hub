// Question cards (0.43.0): questions with their options as buttons - one choice, or several - and, where allowed,
// an answer in the user's own words. Used for a chosen skill's fixed questions in the message box, and for the
// assistant's ask_user in an answer.
import { useState } from "react";
import { Check } from "lucide-react";

export interface CardQuestion { text: string; options: string[]; multi?: boolean }

/** Whether every question has an answer. */
export const allAnswered = (questions: CardQuestion[], answers: string[][]) =>
  questions.every((_, i) => (answers[i] ?? []).length > 0);

export function QuestionList({ questions, answers, onChange, own = false, disabled = false }: {
  questions: CardQuestion[]; answers: string[][]; onChange: (answers: string[][]) => void;
  own?: boolean;                 // an answer in the user's own words may be written instead
  disabled?: boolean }) {
  const [typed, setTyped] = useState<string[]>(() => questions.map((q, i) =>
    (answers[i] ?? []).find((a) => !q.options.includes(a)) ?? ""));
  const set = (i: number, value: string[]) => onChange(questions.map((_, j) => (j === i ? value : answers[j] ?? [])));
  const pick = (i: number, option: string) => {
    const q = questions[i];
    const now = answers[i] ?? [];
    if (q.multi) set(i, now.includes(option) ? now.filter((a) => a !== option) : [...now, option]);
    else {
      set(i, now.includes(option) ? [] : [option]);
      setTyped((t) => t.map((x, j) => (j === i ? "" : x)));
    }
  };
  const write = (i: number, text: string) => {
    const q = questions[i];
    setTyped((t) => t.map((x, j) => (j === i ? text : x)));
    const chosen = (answers[i] ?? []).filter((a) => q.options.includes(a));
    set(i, q.multi ? [...chosen, ...(text.trim() ? [text.trim()] : [])] : text.trim() ? [text.trim()] : chosen);
  };
  return (
    <div className="qlist">
      {questions.map((q, i) => (
        <div key={i} className="q">
          <div className="q-text">{q.text}{q.multi && <span className="faint"> - choose one or more</span>}</div>
          <div className="q-options" role={q.multi ? "group" : "radiogroup"} aria-label={q.text}>
            {q.options.map((o) => {
              const on = (answers[i] ?? []).includes(o);
              return (
                <button key={o} type="button" className={`q-option${on ? " on" : ""}`} disabled={disabled}
                  role={q.multi ? "checkbox" : "radio"} aria-checked={on} onClick={() => pick(i, o)}>
                  {on && <Check size={13} />}{o}</button>
              );
            })}
            {own && <input className="input q-own" value={typed[i] ?? ""} disabled={disabled} placeholder="Or write your own"
              aria-label={`Your own answer to: ${q.text}`} onChange={(e) => write(i, e.target.value)} />}
          </div>
        </div>
      ))}
    </div>
  );
}
