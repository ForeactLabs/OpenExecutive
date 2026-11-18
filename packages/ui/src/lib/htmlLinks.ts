/** A reply's HTML links (`<a href="https://…">Open the draft</a>`, which
 * the model sometimes writes) as Markdown links, so the chat shows a link
 * instead of the raw tag. Only an http(s) address with plain words for its
 * label: anything else stays as written. */
const ANCHOR = /<a\s+[^>]*?href\s*=\s*["'](https?:\/\/[^"'\s<>]+)["'][^>]*>([^<>]*)<\/a>/gi;

export function htmlLinks(text: string): string {
  return text.replace(ANCHOR, (_all, url: string, label: string) => {
    const words = label.replace(/[[\]]/g, "").trim() || url;
    return `[${words}](${url.replace(/[()]/g, (c) => encodeURIComponent(c))})`;
  });
}
