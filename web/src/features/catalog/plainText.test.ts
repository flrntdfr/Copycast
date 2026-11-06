import { describe, expect, it } from "vitest";

import { plainText } from "./RowDetails";

describe("plainText", () => {
  it("is empty for nothing", () => {
    expect(plainText(null)).toBe("");
    expect(plainText(undefined)).toBe("");
    expect(plainText("")).toBe("");
  });

  it("turns line breaks and paragraphs into newlines and drops tags", () => {
    expect(plainText("<p>One<br/>two</p><p><b>Three</b></p>")).toBe("One\ntwo\n\nThree");
  });

  it("decodes each entity exactly once", () => {
    expect(plainText("Tom &amp; Jerry &lt;3 &quot;hi&quot; &#39;x&#39;&nbsp;y")).toBe(
      "Tom & Jerry <3 \"hi\" 'x' y",
    );
    // Regression: &amp;lt; is the text "&lt;", not "<".
    expect(plainText("a &amp;lt;b&amp;gt; c")).toBe("a &lt;b&gt; c");
  });

  it("keeps no script or style content, in any case", () => {
    expect(plainText("x<SCRIPT>alert(1)</SCRIPT><style>p{}</style>y")).toBe("xy");
    expect(plainText("<scr<script>ipt>a</script>b")).not.toContain("<script");
  });

  it("collapses runs of blank lines", () => {
    expect(plainText("<p>a</p><p></p><p></p><p>b</p>")).toBe("a\n\nb");
  });
});
