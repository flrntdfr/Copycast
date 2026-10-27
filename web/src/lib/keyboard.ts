/**
 * Global single-key shortcuts must leave a key alone when the focused element already
 * does something with it: Space activates buttons and checkboxes, arrows move carets,
 * sliders and roving focus, letters type or jump in a list (typeahead).
 */

/** `<input>` types that hold no text; every other type is a text field. */
const NON_TEXT_INPUTS = new Set([
  "button",
  "checkbox",
  "color",
  "file",
  "image",
  "radio",
  "range",
  "reset",
  "submit",
]);

const TEXT_ROLES = new Set(["textbox", "searchbox", "combobox"]);

/** Roles Space activates or toggles (WAI-ARIA). */
const SPACE_ROLES = new Set([
  "button",
  "checkbox",
  "combobox",
  "link",
  "menuitem",
  "menuitemcheckbox",
  "menuitemradio",
  "option",
  "radio",
  "slider",
  "switch",
  "tab",
]);

/** Roles that move or adjust with the arrow keys themselves. */
const ARROW_ROLES = new Set([
  "combobox",
  "menuitem",
  "menuitemcheckbox",
  "menuitemradio",
  "option",
  "radio",
  "scrollbar",
  "slider",
  "spinbutton",
  "tab",
  "treeitem",
]);

/** Composite widgets with roving focus (arrows) or typeahead (letters). */
const COMPOSITE =
  "[role=grid],[role=listbox],[role=menu],[role=menubar],[role=radiogroup],[role=tablist],[role=toolbar],[role=tree],[role=treegrid]";

function roleOf(element: HTMLElement): string {
  return (element.getAttribute("role") ?? "").trim().split(/\s+/)[0] ?? "";
}

/** A field where every printable key is input. */
export function isTextEntry(element: HTMLElement): boolean {
  if (element.isContentEditable) return true;
  const tag = element.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") return !NON_TEXT_INPUTS.has((element as HTMLInputElement).type);
  return TEXT_ROLES.has(roleOf(element));
}

function activatesOnSpace(element: HTMLElement): boolean {
  const tag = element.tagName;
  if (tag === "BUTTON" || tag === "SUMMARY" || tag === "INPUT") return true;
  if (tag === "A" && element.hasAttribute("href")) return true;
  return isTextEntry(element) || SPACE_ROLES.has(roleOf(element));
}

function usesArrowKeys(element: HTMLElement): boolean {
  const tag = element.tagName;
  if (tag === "INPUT" || isTextEntry(element)) return true;
  return ARROW_ROLES.has(roleOf(element)) || element.closest(COMPOSITE) !== null;
}

/**
 * Whether the focused element (the keydown target) handles `key` itself, in which case a
 * global shortcut on that key must not fire.
 */
export function ownsKey(target: EventTarget | null, key: string): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (key === " " || key === "Spacebar") return activatesOnSpace(target);
  if (key.startsWith("Arrow")) return usesArrowKeys(target);
  if (key.length === 1) return isTextEntry(target) || target.closest(COMPOSITE) !== null;
  return isTextEntry(target);
}
