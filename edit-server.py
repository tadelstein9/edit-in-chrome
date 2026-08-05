#!/usr/bin/env python3
"""edit-server.py — read a markdown file in Chrome, edit it there, save it back.

  ./edit-server.py FILE.md [--port 8765]

Then open the address it prints. Edit the page like a document. Ctrl+S writes
the file. The tab tells you what it saved.

/plain serves the same file with no editing attributes on it, for copying into
somewhere else. Copying from the editable page carried every paragraph into
Substack as a blockquote — the editor's own padding and margins travel with the
selection, and the receiving editor reads them as indentation.

WHY NOT AN EXTENSION. An extension that reaches into a page someone else served
can make the text editable and still cannot write your file. The write-back is
the whole feature, and once the page is served by this program the editor ships
inside the page. Nothing to install and nothing that breaks when Chrome changes
its extension API.

⚠ This reasoning has an expiry date. A browser now lets a page open a file the
user picks and write back to it, which means an extension with its own page can
do the write-back after all. The objection above applies to the content-script
shape, not to every extension.

WHY BLOCKS. Converting a whole edited page back to markdown re-wraps every line
and normalises every emphasis mark, so changing one word produces a diff across
the entire file. Instead the file is split into blocks on blank lines, each block
is rendered separately, and only the blocks whose text actually changed get
converted back. Everything untouched is written out byte for byte as the author
wrote it.

⚠ A fenced code block is served read-only. Editing one in a browser and
converting it back is how indentation dies.
"""

import html
import http.server
import json
import re
import socketserver
import subprocess
import sys
from pathlib import Path

PORT = 8765


def split_blocks(text):
    """Markdown split on blank lines, keeping fences whole."""
    blocks, cur, in_fence = [], [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            cur.append(line)
            continue
        if not line.strip() and not in_fence:
            if cur:
                blocks.append("\n".join(cur))
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def pandoc(text, frm, to, columns=None):
    """columns wraps the output; the file keeps one wrap width throughout.

    Without it pandoc returns an edited block as a single long line while every
    untouched block stays wrapped, and the file reads ragged in an editor.
    """
    cmd = ["pandoc", "--from", frm, "--to", to]
    cmd += ["--columns", str(columns)] if columns else ["--wrap=none"]
    out = subprocess.run(cmd, input=text, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip())
    return out.stdout.strip()


PAGE = """<!doctype html>
<meta charset="utf-8">
<title>{title}</title>
<style>
 body {{ max-width: 40rem; margin: 3rem auto; padding: 0 1.5rem;
        font: 17px/1.6 Georgia, serif; color: #1a1a1a; }}
 [data-block] {{ outline: none; padding: 2px 4px; margin: 0 -4px; border-radius: 3px; }}
 [data-block]:focus {{ background: #fffbe6; }}
 [data-block].changed {{ border-left: 3px solid #c8a000; }}
 [data-block][data-locked] {{ background: #f4f4f4; }}
 [data-block].emptied {{ display: none; }}
 h1, h2, h3 {{ font-family: Helvetica, Arial, sans-serif; line-height: 1.25; }}
 code {{ font: 15px/1.4 ui-monospace, monospace; background: #f2f2f2; padding: 1px 3px; }}
 blockquote {{ margin-left: 0; padding-left: 1rem; border-left: 3px solid #ddd; color: #444; }}
 /* user-select:none keeps Ctrl+A off the bar. Without it a select-all on this
    page carries the filename and "Ctrl+S saves" into the clipboard along with
    the document — which is the mistake /plain exists to prevent. */
 #bar {{ position: fixed; top: 0; left: 0; right: 0; padding: .5rem 1rem;
         font: 13px/1.4 Helvetica, Arial, sans-serif; background: #1a1a1a; color: #eee;
         -webkit-user-select: none; user-select: none; }}
 #bar b {{ color: #ffd54a; }}
</style>
<div id="bar"><b>{name}</b> &middot; edit the page &middot; <b>Ctrl+S</b> saves &middot;
<span id="status">no changes</span>
<a href="/plain" target="_blank" style="float:right;color:#ffd54a">plain copy &rarr;</a></div>
<div style="height:2.5rem"></div>
<div id="doc">{body}</div>
<script>
const original = {originals};
const status = document.getElementById('status');

for (const el of document.querySelectorAll('[data-block]')) {{
  if (el.hasAttribute('data-locked')) continue;
  el.contentEditable = 'true';
  el.addEventListener('input', () => {{
    const i = el.getAttribute('data-block');
    const empty = el.innerText.trim() === '';
    el.classList.toggle('emptied', empty);
    el.classList.toggle('changed', !empty && el.innerHTML.trim() !== original[i]);
    const c = document.querySelectorAll('[data-block].changed').length;
    const d = document.querySelectorAll('[data-block].emptied').length;
    const parts = [];
    if (c) parts.push(c + ' changed');
    if (d) parts.push(d + ' deleted');
    status.textContent = parts.length ? parts.join(', ') : 'no changes';
  }});
}}

// Clean the clipboard on the way out, so copying from this page is safe and
// nobody has to remember a second one. The editable blocks carry
// contenteditable, data-block and their own padding, and a receiving editor
// reads that markup as formatting the author never applied — an accent bar
// down every paragraph, in the case that started this. Strip every attribute
// the editor added, keep the markup that is the document.
const KEEP = {{A: ['href'], IMG: ['src', 'alt'], TD: ['colspan', 'rowspan'],
               TH: ['colspan', 'rowspan']}};

function scrub(node) {{
  for (const el of node.querySelectorAll('*')) {{
    const keep = KEEP[el.tagName] || [];
    for (const attr of [...el.attributes])
      if (!keep.includes(attr.name)) el.removeAttribute(attr.name);
  }}
  return node;
}}

document.addEventListener('copy', (e) => {{
  const sel = window.getSelection();
  if (!sel.rangeCount || sel.isCollapsed) return;
  const holder = document.createElement('div');
  for (let i = 0; i < sel.rangeCount; i++)
    holder.appendChild(sel.getRangeAt(i).cloneContents());
  // The bar can only reach the clipboard through a drag-selection; drop it.
  for (const el of holder.querySelectorAll('#bar')) el.remove();
  scrub(holder);
  e.clipboardData.setData('text/html', holder.innerHTML);
  e.clipboardData.setData('text/plain', sel.toString());
  e.preventDefault();
}});

// Ctrl+A selects the document and not the bar above it. user-select:none on
// the bar stops a drag from reaching it but does not keep select-all out, so
// take the key and set the range ourselves. Inside a block being edited,
// leave Ctrl+A alone — there it means select this paragraph.
document.addEventListener('keydown', (e) => {{
  if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'a') return;
  const inBlock = document.activeElement &&
                  document.activeElement.hasAttribute &&
                  document.activeElement.hasAttribute('data-block');
  if (inBlock) return;
  e.preventDefault();
  const doc = document.getElementById('doc');
  const range = document.createRange();
  range.selectNodeContents(doc);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
}});

document.addEventListener('keydown', async (e) => {{
  if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 's') return;
  e.preventDefault();
  const changed = {{}};
  for (const el of document.querySelectorAll('[data-block].changed'))
    changed[el.getAttribute('data-block')] = el.innerHTML;
  for (const el of document.querySelectorAll('[data-block].emptied'))
    changed[el.getAttribute('data-block')] = '';
  if (!Object.keys(changed).length) {{ status.textContent = 'nothing to save'; return; }}
  status.textContent = 'saving…';
  const r = await fetch('/save', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{blocks: changed}})
  }});
  const j = await r.json();
  if (!j.ok) {{ status.textContent = 'FAILED: ' + j.error; return; }}
  status.textContent = 'saved ' + j.written + ' block' + (j.written > 1 ? 's' : '');
  for (const el of document.querySelectorAll('[data-block].changed')) {{
    original[el.getAttribute('data-block')] = el.innerHTML.trim();
    el.classList.remove('changed');
  }}
  // Removing blocks renumbers everything after them, so the page has to
  // come back from the file rather than trust its own indices.
  if (j.deleted) location.reload();
}});
</script>
"""


PLAIN = """<!doctype html>
<meta charset="utf-8">
<title>{title}</title>
<style>
 body {{ max-width: 40rem; margin: 3rem auto; padding: 0 1.5rem;
        font: 17px/1.6 Georgia, serif; color: #1a1a1a; }}
 h1, h2, h3 {{ font-family: Helvetica, Arial, sans-serif; line-height: 1.25; }}
 code {{ font: 15px/1.4 ui-monospace, monospace; }}
</style>
{body}
"""


class Handler(http.server.BaseHTTPRequestHandler):
    target = None          # Path to the markdown file

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/") == "/plain":
            return self.serve_plain()
        blocks = split_blocks(self.target.read_text(encoding="utf-8"))
        rendered, originals = [], {}
        for i, b in enumerate(blocks):
            locked = b.lstrip().startswith("```")
            frag = pandoc(b, "markdown", "html5")
            inner = re.sub(r"^<(p|h[1-6]|blockquote|ul|ol|pre)\b[^>]*>|</\1>$", "",
                           frag.strip())
            tag = re.match(r"^<(\w+)", frag.strip())
            tag = tag.group(1) if tag else "p"
            originals[str(i)] = inner.strip()
            rendered.append(
                f'<{tag} data-block="{i}"{" data-locked" if locked else ""}>'
                f"{inner}</{tag}>")
        self._send(200, PAGE.format(
            title=html.escape(self.target.stem),
            name=html.escape(self.target.name),
            body="\n".join(rendered),
            originals=json.dumps(originals)))

    def serve_plain(self):
        """The same file with nothing on it, for copying somewhere else.

        No contenteditable, no data-block, no padding or margins. What the
        editable page carries in its markup travels with a selection, and a
        receiving editor reads it as formatting the author never applied.
        """
        body = pandoc(self.target.read_text(encoding="utf-8"),
                      "markdown", "html5")
        self._send(200, PLAIN.format(
            title=html.escape(self.target.stem), body=body))

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
            changed = payload.get("blocks", {})

            text = self.target.read_text(encoding="utf-8")
            blocks = split_blocks(text)
            deleted = 0
            for idx, frag in changed.items():
                i = int(idx)
                if not 0 <= i < len(blocks):
                    raise ValueError(f"block {i} is not in the file any more")
                # An emptied block goes away. Leaving it as a blank entry puts
                # a marker in the page with nothing under it.
                if not frag.strip():
                    blocks[i] = None
                    deleted += 1
                    continue
                prefix = re.match(r"^(#{1,6} |> |[-*] |\d+\. )", blocks[i])
                # contenteditable inserts non-breaking spaces as you type,
                # especially either side of inline code. They survive the round
                # trip as U+00A0 in the markdown: invisible on the page, and a
                # search for the sentence never finds it.
                frag = frag.replace("\u00a0", " ").replace("&nbsp;", " ")
                md = pandoc(frag, "html", "markdown_strict-raw_html", columns=95)
                # A heading or a list marker lives in the source, not in the
                # fragment the browser hands back. Put it back on.
                if prefix and not md.startswith(prefix.group(1)):
                    md = prefix.group(1) + md
                blocks[i] = md

            blocks = [b for b in blocks if b is not None]
            self.target.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
            self._send(200, json.dumps({"ok": True, "written": len(changed),
                                        "deleted": deleted}),
                       "application/json")
        except Exception as e:
            self._send(200, json.dumps({"ok": False, "error": str(e)}),
                       "application/json")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit("usage: edit-server.py FILE.md [--port N]")
    target = Path(args[0]).resolve()
    if not target.is_file():
        sys.exit(f"edit-server: no such file — {target}")

    port = PORT
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])

    Handler.target = target
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
        print(f"editing {target}")
        print(f"open http://127.0.0.1:{port}/   —   Ctrl+S in the page saves")
        httpd.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
