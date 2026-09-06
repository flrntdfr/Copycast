import { toast } from "sonner";

/** Copy text to the clipboard with a legacy fallback; resolves to whether it worked. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the textarea trick.
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  } catch {
    return false;
  }
}

/** Copy and toast the outcome; `what` names the thing copied ("Feed URL"). */
export async function copyWithToast(text: string, what = "Text"): Promise<void> {
  const ok = await copyText(text);
  if (ok) toast.success(`${what} copied`);
  else toast.error(`Could not copy the ${what.toLowerCase()}`, { description: text });
}
