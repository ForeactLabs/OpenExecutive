/** A reply's HTML links (`<a href="https://…">Open the draft</a>`, which
 * the model sometimes writes) as Markdown links, so the chat shows a link
 * instead of the raw tag. Only an http(s) address with plain words for its
 * label: anything else stays as written.
 *
 * Also drops an attribute list written right after a Markdown link or image
 * (`[Open the email](https://…){target="_blank"}`, kramdown style), which
 * Markdown here doesn't read and the chat would show as raw text. */
const ANCHOR = /<a\s+[^>]*?href\s*=\s*["'](https?:\/\/[^"'\s<>]+)["'][^>]*>([^<>]*)<\/a>/gi;

// `{target="_blank"}`, `{:target='_blank' rel=noopener}`, `{.cls #id}`:
// only `.class`, `#id` and `name=value` pairs, so ordinary braces in
// prose stay.
const ATTR = String.raw`(?:[.#][\w-]+|[\w-]+\s*=\s*(?:"[^"\n]*"|'[^'\n]*'|[^\s{}"']+))`;
const LINK_ATTRS = new RegExp(String.raw`(\]\((?:<[^<>\n]*>|[^\s()]*(?:\([^\s()]*\)[^\s()]*)*)(?:\s+(?:"[^"\n]*"|'[^'\n]*'))?\))\{:?\s*${ATTR}(?:\s+${ATTR})*\s*\}`, "g");

// Code (fenced blocks and `inline` spans) stays exactly as written.
const CODE = /(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`)/g;

export function htmlLinks(text: string): string {
  return text
    .split(CODE)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part
            .replace(ANCHOR, (_all, url: string, label: string) => {
              const words = label.replace(/[[\]]/g, "").trim() || url;
              return `[${words}](${url.replace(/[()]/g, (c) => encodeURIComponent(c))})`;
            })
            .replace(LINK_ATTRS, "$1"),
    )
    .join("");
}
