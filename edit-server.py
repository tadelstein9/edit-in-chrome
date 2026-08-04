#!/usr/bin/env python3
"""edit-server.py — read a markdown file in Chrome, edit it there, save it back.

  ./edit-server.py FILE.md [--port 8765]

Then open the address it prints. Edit the page like a document. Ctrl+S writes
the file. The tab tells you what it saved.

WHY NOT AN EXTENSION. An extension that edits the rendered page and cannot write
back is a nicer-looking Substack — Tom's point, 2026-08-04. The write-back is the
whole feature, and once the page is served by our own program the editor can ship
inside the page. Nothing to install and nothing that breaks when Chrome changes
its extension API.

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


def pandoc(text, frm, to):
    out = subprocess.run(
        ["pandoc", "--from", frm, "--to", to, "--wrap=none"],
        input=text, capture_output=True, text=True)
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
 h1, h2, h3 {{ font-family: Helvetica, Arial, sans-serif; line-height: 1.25; }}
 code {{ font: 15px/1.4 ui-monospace, monospace; background: #f2f2f2; padding: 1px 3px; }}
 blockquote {{ margin-left: 0; padding-left: 1rem; border-left: 3px solid #ddd; color: #444; }}
 #bar {{ position: fixed; top: 0; left: 0; right: 0; padding: .5rem 1rem;
         font: 13px/1.4 Helvetica, Arial, sans-serif; background: #1a1a1a; color: #eee; }}
 #bar b {{ color: #ffd54a; }}
</style>
<div id="bar"><b>{name}</b> &middot; edit the page &middot; <b>Ctrl+S</b> saves &middot;
<span id="status">no changes</span></div>
<div style="height:2.5rem"></div>
{body}
<script>
const original = {originals};
const status = document.getElementById('status');

for (const el of document.querySelectorAll('[data-block]')) {{
  if (el.hasAttribute('data-locked')) continue;
  el.contentEditable = 'true';
  el.addEventListener('input', () => {{
    const i = el.getAttribute('data-block');
    el.classList.toggle('changed', el.innerHTML.trim() !== original[i]);
    const n = document.querySelectorAll('[data-block].changed').length;
    status.textContent = n ? n + ' block' + (n > 1 ? 's' : '') + ' changed' : 'no changes';
  }});
}}

document.addEventListener('keydown', async (e) => {{
  if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 's') return;
  e.preventDefault();
  const changed = {{}};
  for (const el of document.querySelectorAll('[data-block].changed'))
    changed[el.getAttribute('data-block')] = el.innerHTML;
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
}});
</script>
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

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
            changed = payload.get("blocks", {})

            text = self.target.read_text(encoding="utf-8")
            blocks = split_blocks(text)
            for idx, frag in changed.items():
                i = int(idx)
                if not 0 <= i < len(blocks):
                    raise ValueError(f"block {i} is not in the file any more")
                prefix = re.match(r"^(#{1,6} |> |[-*] |\d+\. )", blocks[i])
                md = pandoc(frag, "html", "markdown_strict-raw_html")
                # A heading or a list marker lives in the source, not in the
                # fragment the browser hands back. Put it back on.
                if prefix and not md.startswith(prefix.group(1)):
                    md = prefix.group(1) + md
                blocks[i] = md

            self.target.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
            self._send(200, json.dumps({"ok": True, "written": len(changed)}),
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
