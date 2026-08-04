# edit-in-chrome

Read a markdown file in your browser, edit it there, and save it back — with only
the paragraphs you actually touched rewritten.

```
./edit-server.py FILE.md
```

Open the address it prints. Type into the page. **Ctrl+S** writes the file.

## The problem this solves

You want to read a draft the way a reader will see it, and fix what you find while
you are looking at it. A browser renders it correctly and won't let you type into
it. A markdown editor lets you type and shows you source.

Converting the whole rendered page back to markdown solves neither. Pandoc re-wraps
every line and normalises every emphasis mark, so changing one word produces a diff
across the entire file, and you can no longer see what you changed.

## What it does instead

The file is split into blocks on blank lines. Each block is rendered on its own and
carries its index in the page. When you save, only the blocks whose text changed are
converted back to markdown. Everything you did not touch is written out exactly as
you wrote it.

One edited paragraph produces one changed paragraph in the file. The rest of the
diff is empty.

## Using it

A block you are editing turns yellow. A block you have changed keeps a gold bar down
its left edge, and the bar at the top counts them. Ctrl+S saves and reports how many
blocks it wrote.

Empty a block and it disappears from the page, and disappears from the file when
you save. Removing a block renumbers the ones after it, so the page reloads itself
from the file rather than trusting indices that just went stale.

Headings, blockquotes and list items keep their markers — those live in the source,
not in the fragment the browser hands back, and the program puts them on again.

Non-breaking spaces are stripped on the way back. A browser inserts them as you
type, especially either side of inline code, and they survive the round trip as
U+00A0 — invisible on the page, and a search for your own sentence never finds it.

A fenced code block is served read-only, greyed. Editing one in a browser and
converting it back is how indentation dies.

## Requirements

Python 3 and [pandoc](https://pandoc.org/). Nothing else. No extension, no
dependencies, no build step.

The server binds to `127.0.0.1` and serves one file, the one you named.

## Options

```
./edit-server.py FILE.md --port 8080
```

## Why not a Chrome extension

An extension that makes the rendered page editable and cannot write back leaves you
retyping the change in your editor afterward. Once the write-back is the point, the
page has to be served by a program that owns the file — and once a program is
serving the page, the editor ships inside it. Nothing to install and nothing that
breaks when Chrome changes its extension API.

## Who wrote this

Claude Code wrote this for me, in one session on 4 August 2026.

I described the problem. I wanted to read a draft the way a reader sees it and fix
what I found while I was looking at it, and I didn't want a round trip that rewrote
the whole file to change one word. Claude built the thing that does it, including
the block-level write-back that keeps the diff honest.

— Tom Adelstein

## License

MIT. See LICENSE.
