import { afterEach, describe, expect, it } from "vitest";

import { ownsKey } from "./keyboard";

function mount(html: string): (id: string) => HTMLElement {
  document.body.innerHTML = html;
  return (id) => {
    const element = document.getElementById(id);
    if (!element) throw new Error(id);
    return element;
  };
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("ownsKey", () => {
  it("leaves Space to what Space activates", () => {
    const get = mount(`
      <button id="button"></button>
      <a id="link" href="#x">x</a>
      <a id="anchor">x</a>
      <div id="checkbox" role="checkbox" tabindex="0"></div>
      <div id="switch" role="switch" tabindex="0"></div>
      <input id="text" />
      <details><summary id="summary">More</summary></details>
      <div id="plain" tabindex="0"></div>
    `);
    for (const id of ["button", "link", "checkbox", "switch", "text", "summary"])
      expect(ownsKey(get(id), " "), id).toBe(true);
    expect(ownsKey(get("anchor"), " ")).toBe(false);
    expect(ownsKey(get("plain"), " ")).toBe(false);
    expect(ownsKey(document.body, " ")).toBe(false);
    expect(ownsKey(null, " ")).toBe(false);
  });

  it("leaves arrows to fields, sliders and composite widgets", () => {
    const get = mount(`
      <input id="range" type="range" />
      <button id="button"></button>
      <div role="menu"><div id="item" role="menuitem" tabindex="-1"></div></div>
      <div role="grid"><div id="cell" tabindex="0"></div></div>
      <div role="radiogroup"><button id="radio" role="radio"></button></div>
    `);
    for (const id of ["range", "item", "cell", "radio"])
      expect(ownsKey(get(id), "ArrowLeft"), id).toBe(true);
    expect(ownsKey(get("button"), "ArrowRight")).toBe(false);
    expect(ownsKey(get("button"), "ArrowUp")).toBe(false);
  });

  it("leaves letters to text fields and typeahead lists", () => {
    const get = mount(`
      <input id="search" type="search" />
      <input id="check" type="checkbox" />
      <div id="textbox" role="textbox" tabindex="0"></div>
      <textarea id="area"></textarea>
      <div role="listbox"><div id="option" role="option" tabindex="0"></div></div>
      <button id="button"></button>
    `);
    for (const id of ["search", "textbox", "area", "option"])
      expect(ownsKey(get(id), "m"), id).toBe(true);
    expect(ownsKey(get("check"), "m")).toBe(false);
    expect(ownsKey(get("button"), "/")).toBe(false);
  });
});
