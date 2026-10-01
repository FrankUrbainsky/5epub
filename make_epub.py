#!/usr/bin/env python3
"""
make_epub.py  –  Convert a 5etools adventure JSON into an EPUB file.

Images are downloaded from the 5e.tools CDN and embedded in the EPUB.
A local disk cache (./image-cache/) avoids re-downloading on repeated runs.
Internal cross-reference tags ({@area}) become working chapter/anchor links.

Usage:
    python3 make_epub.py [OPTIONS] <adventure-json>

Examples:
    python3 make_epub.py 5etools-src/data/adventure/adventure-wdh.json
    python3 make_epub.py --out waterdeep.epub 5etools-src/data/adventure/adventure-wdh.json
    python3 make_epub.py --no-images 5etools-src/data/adventure/adventure-wdh.json
    python3 make_epub.py --workers 16 5etools-src/data/adventure/adventure-wdh.json

Requires:  ebooklib, requests   (`pip install ebooklib requests`)
Optional:  Pillow               (`pip install Pillow`)  – needed only for --jpeg
"""

import argparse
import hashlib
import io
import json
import re
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from textwrap import dedent
from urllib.parse import quote as url_quote

try:
    from ebooklib import epub
except ImportError:
    sys.exit(
        "ebooklib is not installed.  Run:  pip install ebooklib\n"
        "  (or:  pip install --break-system-packages ebooklib)"
    )

try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

try:
    from PIL import Image as PilImage
    _PILLOW_AVAILABLE = True
except ImportError:
    _PILLOW_AVAILABLE = False


# ---------------------------------------------------------------------------
# Image cache / downloader
# ---------------------------------------------------------------------------

CDN_BASE = "https://5e.tools/img/"
_DL_HEADERS = {"User-Agent": "Mozilla/5.0 5etools-epub/1.0"}


class ImageCache:
    """Download 5etools CDN images with parallel fetching and a local disk cache."""

    def __init__(
        self,
        cache_dir: Path,
        convert_jpeg: bool = False,
        no_images: bool = False,
        no_maps: bool = False,
        workers: int = 8,
    ):
        self.convert_jpeg = convert_jpeg and _PILLOW_AVAILABLE
        self.no_images = no_images
        self.no_maps = no_maps
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._mem: dict[str, bytes | None] = {}   # cdn_path -> raw bytes | None
        self._workers = workers

    # ---- disk cache ----------------------------------------------------------

    def _disk_path(self, cdn_path: str) -> Path:
        safe = re.sub(r"[^\w._-]", "_", cdn_path)
        return self._cache_dir / safe

    def _read_disk(self, cdn_path: str) -> bytes | None:
        p = self._disk_path(cdn_path)
        return p.read_bytes() if p.exists() else None

    def _write_disk(self, cdn_path: str, data: bytes) -> None:
        self._disk_path(cdn_path).write_bytes(data)

    # ---- network fetch -------------------------------------------------------

    def _fetch_one(self, cdn_path: str) -> bytes | None:
        cached = self._read_disk(cdn_path)
        if cached is not None:
            return cached

        if not _REQUESTS_AVAILABLE:
            print(f"  [WARN] requests not installed; skipping {cdn_path}", file=sys.stderr)
            return None

        url = CDN_BASE + url_quote(cdn_path, safe="/")
        try:
            r = _requests.get(url, headers=_DL_HEADERS, timeout=20)
            r.raise_for_status()
            data = r.content
            self._write_disk(cdn_path, data)
            return data
        except Exception as exc:
            print(f"  [WARN] Download failed for {cdn_path}: {exc}", file=sys.stderr)
            return None

    # ---- public API ----------------------------------------------------------

    def prefetch(self, paths: list[str]) -> None:
        """Download all paths in parallel (skips already-cached ones)."""
        # Filter to only what isn't already in memory
        needed = [p for p in paths if p not in self._mem]
        # Further filter: skip disk-cached ones so threads only hit network
        to_download = [p for p in needed if not self._disk_path(p).exists()]

        # Load disk-cached ones immediately
        for p in needed:
            if self._disk_path(p).exists():
                self._mem[p] = self._read_disk(p)

        if not to_download:
            if needed:
                print(f"All {len(needed)} image(s) already cached on disk.")
            return

        print(f"Downloading {len(to_download)} image(s) "
              f"({len(needed) - len(to_download)} already cached) …")
        done = 0
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            futures = {pool.submit(self._fetch_one, p): p for p in to_download}
            for fut in as_completed(futures):
                cdn_path = futures[fut]
                data = fut.result()
                with self._lock:
                    self._mem[cdn_path] = data
                done += 1
                if done % 10 == 0 or done == len(to_download):
                    print(f"  {done}/{len(to_download)} downloaded", flush=True)

    def get(self, cdn_path: str, image_type: str = "art") -> bytes | None:
        """Return image bytes for the given path, or None if skipped/unavailable."""
        if self.no_images:
            return None
        if self.no_maps and image_type in ("map", "mapPlayer"):
            return None

        with self._lock:
            if cdn_path in self._mem:
                data = self._mem[cdn_path]
            else:
                data = self._fetch_one(cdn_path)
                self._mem[cdn_path] = data

        if data is None:
            return None

        if self.convert_jpeg:
            try:
                img = PilImage.open(io.BytesIO(data))
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGB")
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=85, optimize=True)
                return out.getvalue()
            except Exception:
                return data   # fallback to original on conversion error

        return data

    def media_type(self) -> str:
        return "image/jpeg" if self.convert_jpeg else "image/webp"

    def extension(self) -> str:
        return "jpg" if self.convert_jpeg else "webp"


def collect_image_paths(chapters: list[dict]) -> list[str]:
    """Return a de-duplicated, ordered list of all internal image cdn_paths."""
    seen: set[str] = set()
    order: list[str] = []

    def _walk(obj):
        if isinstance(obj, list):
            for x in obj:
                _walk(x)
        elif isinstance(obj, dict):
            if obj.get("type") == "image":
                href = obj.get("href", {})
                if href.get("type") == "internal":
                    p = href.get("path", "")
                    if p and p not in seen:
                        seen.add(p)
                        order.append(p)
            for v in obj.values():
                _walk(v)

    for ch in chapters:
        _walk(ch)
    return order


# ---------------------------------------------------------------------------
# Global ID → chapter map
# ---------------------------------------------------------------------------

def build_id_map(chapters: list[dict]) -> dict[str, tuple[int, str]]:
    """
    Walk all chapters and map every entry `id` to (chapter_index, html_anchor).

    Returns:  { id_str: (chapter_idx, "id-<id_str>") }
    """
    id_map: dict[str, tuple[int, str]] = {}

    def _walk(obj, chap_idx: int):
        if isinstance(obj, list):
            for x in obj:
                _walk(x, chap_idx)
        elif isinstance(obj, dict):
            eid = obj.get("id")
            if eid:
                id_map[eid] = (chap_idx, f"id-{eid}")
            for v in obj.values():
                _walk(v, chap_idx)

    for ci, ch in enumerate(chapters):
        _walk(ch, ci)

    return id_map


# ---------------------------------------------------------------------------
# Inline {@tag …} rendering
# ---------------------------------------------------------------------------

# Tags that emit the first pipe-separated token as plain (italicised) text
_PLAIN_TAG_RE = re.compile(
    r"\{@(?:creature|item|spell|condition|disease|status|hazard|reward|race|background"
    r"|quickref|book|adventure|filter|5etools|table|note)\s+([^}]+)\}"
)

# {@area <label>|<id>|<extra>}  — captures label and id separately
_AREA_TAG_RE = re.compile(
    r"\{@area\s+([^|}]+)(?:\|([^|}]*))?(?:\|[^}]*)?\}"
)

# {@dc <n>}
_DC_TAG_RE = re.compile(r"\{@dc\s+(\d+)\}")

# {@dice <expr>} / {@hit <n>} / {@chance <n>}
_DICE_TAG_RE = re.compile(r"\{@(?:dice|hit|chance)\s+([^}]+)\}")

# {@i text} / {@italic text}
_ITALIC_TAG_RE = re.compile(r"\{@(?:i|italic)\s+([^}]+)\}")

# {@b text} / {@bold text}
_BOLD_TAG_RE = re.compile(r"\{@(?:b|bold)\s+([^}]+)\}")

# catch-all — strip remaining {@…}
_CATCHALL_TAG_RE = re.compile(r"\{@\w+\s*([^}]*)\}")


def _first_segment(text: str) -> str:
    """Return text up to the first pipe, stripped."""
    return text.split("|")[0].strip()


# ---------------------------------------------------------------------------
# Entry → HTML renderer
# ---------------------------------------------------------------------------

class EntryRenderer:
    """Recursively render a 5etools entry tree to an HTML string."""

    def __init__(
        self,
        image_cache: ImageCache | None = None,
        id_map: dict[str, tuple[int, str]] | None = None,
    ):
        self._image_cache = image_cache
        self._id_map: dict[str, tuple[int, str]] = id_map or {}
        # Accumulates EpubImage objects created during rendering
        self._epub_images: dict[str, epub.EpubImage] = {}   # cdn_path -> EpubImage
        self._new_images: list[epub.EpubImage] = []          # flushed per chapter

    # ---- public helpers ------------------------------------------------------

    def flush_new_images(self) -> list[epub.EpubImage]:
        """Return (and clear) EpubImage items created since the last flush."""
        out = list(self._new_images)
        self._new_images.clear()
        return out

    def render_inline(self, text: str) -> str:
        """Convert 5etools inline {@tag} markup to HTML."""
        text = _PLAIN_TAG_RE.sub(lambda m: f"<em>{_first_segment(m.group(1))}</em>", text)
        text = _AREA_TAG_RE.sub(self._render_area_tag, text)
        text = _DC_TAG_RE.sub(lambda m: f"DC&nbsp;{m.group(1)}", text)
        text = _DICE_TAG_RE.sub(lambda m: m.group(1), text)
        text = _ITALIC_TAG_RE.sub(lambda m: f"<em>{m.group(1)}</em>", text)
        text = _BOLD_TAG_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
        text = _CATCHALL_TAG_RE.sub(lambda m: _first_segment(m.group(1)), text)
        return text

    def _render_area_tag(self, m: re.Match) -> str:
        label = m.group(1).strip()
        entry_id = (m.group(2) or "").strip()
        if not entry_id or entry_id not in self._id_map:
            return label   # no match — plain text fallback
        chap_idx, anchor = self._id_map[entry_id]
        href = f"../Text/chapter_{chap_idx:03d}.xhtml#{anchor}"
        return f'<a href="{href}">{label}</a>'

    # ---- dispatch ------------------------------------------------------------

    def render(self, entry, depth: int = 0) -> str:
        """Dispatch on entry type."""
        if isinstance(entry, str):
            return f"<p>{self.render_inline(entry)}</p>\n"
        if not isinstance(entry, dict):
            return ""
        t = entry.get("type", "entries")
        method = getattr(self, f"_render_{t}", self._render_entries)
        return method(entry, depth)

    # ---- compound containers ------------------------------------------------

    def _render_section(self, entry: dict, depth: int) -> str:
        """Render a named section heading then its child entries.
        Does NOT delegate to _render_entries — that would re-render the heading."""
        name = entry.get("name", "")
        entry_id = entry.get("id", "")
        id_attr = f' id="id-{entry_id}"' if entry_id else ""
        tag = f"h{min(depth + 2, 6)}"
        html = f"<{tag}{id_attr}>{self.render_inline(name)}</{tag}>\n"
        for sub in entry.get("entries", []):
            html += self.render(sub, depth + 1)
        return html

    def _render_entries(self, entry: dict, depth: int) -> str:
        name = entry.get("name", "")
        entry_id = entry.get("id", "")
        id_attr = f' id="id-{entry_id}"' if entry_id else ""
        html = ""
        if name:
            tag = f"h{min(depth + 2, 6)}"
            html += f"<{tag}{id_attr}>{self.render_inline(name)}</{tag}>\n"
        for sub in entry.get("entries", []):
            html += self.render(sub, depth + 1)
        return html

    # ---- leaf types ---------------------------------------------------------

    def _render_list(self, entry: dict, depth: int) -> str:
        items = entry.get("items", [])
        style = entry.get("style", "")
        tag = "ol" if "decimal" in style else "ul"
        html = f"<{tag}>\n"
        for item in items:
            if isinstance(item, str):
                html += f"  <li>{self.render_inline(item)}</li>\n"
            elif isinstance(item, dict):
                inner = ""
                if item.get("name"):
                    inner += f"<strong>{self.render_inline(item['name'])}</strong> "
                for sub in item.get("entries", [item.get("entry", "")]):
                    inner += self.render(sub, depth + 1)
                html += f"  <li>{inner}</li>\n"
        html += f"</{tag}>\n"
        return html

    def _render_table(self, entry: dict, depth: int) -> str:
        caption    = entry.get("caption", "")
        col_labels = entry.get("colLabels", [])
        rows       = entry.get("rows", [])
        intro      = entry.get("intro", [])
        outro      = entry.get("outro", [])

        html = '<table class="tbl">\n'
        if caption:
            html += f"  <caption>{self.render_inline(caption)}</caption>\n"
        if col_labels:
            html += "  <thead><tr>"
            for lbl in col_labels:
                if isinstance(lbl, dict):
                    lbl = lbl.get("label", "")
                html += f"<th>{self.render_inline(str(lbl))}</th>"
            html += "</tr></thead>\n"
        html += "  <tbody>\n"
        for row in rows:
            html += "    <tr>"
            if isinstance(row, list):
                for cell in row:
                    if isinstance(cell, dict):
                        cell = cell.get("exact", cell.get("min", str(cell)))
                    html += f"<td>{self.render_inline(str(cell))}</td>"
            html += "</tr>\n"
        html += "  </tbody>\n</table>\n"

        out = ""
        for p in intro:
            out += self.render(p, depth)
        out += html
        for p in outro:
            out += self.render(p, depth)
        return out

    def _render_inset(self, entry: dict, depth: int) -> str:
        name = entry.get("name", "")
        html = '<div class="inset">\n'
        if name:
            html += f'  <p class="inset-title">{self.render_inline(name)}</p>\n'
        for sub in entry.get("entries", []):
            html += self.render(sub, depth + 1)
        html += "</div>\n"
        return html

    def _render_insetReadaloud(self, entry: dict, depth: int) -> str:
        html = '<div class="readaloud">\n'
        for sub in entry.get("entries", []):
            html += self.render(sub, depth + 1)
        html += "</div>\n"
        return html

    def _render_quote(self, entry: dict, depth: int) -> str:
        by = entry.get("by", "")
        html = "<blockquote>\n"
        for sub in entry.get("entries", []):
            html += self.render(sub, depth + 1)
        if by:
            html += f"  <footer>— {self.render_inline(by)}</footer>\n"
        html += "</blockquote>\n"
        return html

    def _render_image(self, entry: dict, depth: int) -> str:
        href       = entry.get("href", {})
        title      = entry.get("title", "")
        cdn_path   = href.get("path", "")
        image_type = entry.get("imageType", "art")

        if not cdn_path:
            return ""

        cache = self._image_cache
        if cache is not None:
            data = cache.get(cdn_path, image_type)
            if data is None:
                return ""   # skipped (no-images / no-maps / download failure)

            if cdn_path not in self._epub_images:
                # Build a safe EPUB-internal filename
                safe = re.sub(r"[^\w._-]", "_", cdn_path)
                # Ensure correct extension
                ext = cache.extension()
                base = safe.rsplit(".", 1)[0] if "." in safe else safe
                file_name = f"Images/{base}.{ext}"
                uid = f"img_{hashlib.md5(cdn_path.encode()).hexdigest()[:8]}"
                img_item = epub.EpubItem(
                    uid=uid,
                    file_name=file_name,
                    media_type=cache.media_type(),
                    content=data,
                )
                self._epub_images[cdn_path] = img_item
                self._new_images.append(img_item)

            src = f"../{ self._epub_images[cdn_path].file_name}"
        else:
            # No cache configured — fall back to external CDN link
            src = CDN_BASE + cdn_path

        alt  = self.render_inline(title) if title else cdn_path.split("/")[-1]
        html = f'<figure>\n  <img src="{src}" alt="{alt}"/>\n'
        if title:
            html += f"  <figcaption>{self.render_inline(title)}</figcaption>\n"
        html += "</figure>\n"
        return html

    def _render_gallery(self, entry: dict, depth: int) -> str:
        html = '<div class="gallery">\n'
        for img in entry.get("images", []):
            html += self._render_image(img, depth)
        html += "</div>\n"
        return html

    def _render_flowchart(self, entry: dict, depth: int) -> str:
        html = '<div class="flowchart">\n'
        for block in entry.get("blocks", []):
            html += self._render_flowBlock(block, depth)
        html += "</div>\n"
        return html

    def _render_flowBlock(self, entry: dict, depth: int) -> str:
        name = entry.get("name", "")
        html = '<div class="flow-block">\n'
        if name:
            html += f'  <p class="flow-title">{self.render_inline(name)}</p>\n'
        for sub in entry.get("entries", []):
            html += self.render(sub, depth + 1)
        html += "</div>\n"
        return html

    def _render_statblock(self, entry: dict, depth: int) -> str:
        name   = entry.get("name", "")
        tag    = entry.get("tag", "creature")
        source = entry.get("source", "")
        # statblock content lives on 5e.tools — keep as external link
        link = (
            f"https://5e.tools/{tag}s.html"
            f"#{name.lower().replace(' ', '%20')}_{source}"
        )
        return (
            f'<div class="statblock-ref">'
            f'<p>📋 <strong>{self.render_inline(name)}</strong> '
            f'— see <a href="{link}">5e.tools</a></p>'
            f"</div>\n"
        )

    def _render_none(self, entry: dict, depth: int) -> str:
        html = ""
        for sub in entry.get("entries", []):
            html += self.render(sub, depth)
        return html

    # ---- unknown type fallback ----------------------------------------------

    def __getattr__(self, name: str):
        if name.startswith("_render_"):
            return self._render_entries
        raise AttributeError(name)


# ---------------------------------------------------------------------------
# Module-level shim (backward-compat)
# ---------------------------------------------------------------------------

def render_inline(text: str) -> str:
    """Module-level convenience wrapper around EntryRenderer.render_inline."""
    return EntryRenderer().render_inline(text)


# ---------------------------------------------------------------------------
# Chapter → EPUB XHTML builder
# ---------------------------------------------------------------------------

def build_chapter_html(chapter: dict, renderer: EntryRenderer, chapter_num: int) -> str:
    """Render one top-level section dict to a complete XHTML document string."""
    name    = chapter.get("name", f"Chapter {chapter_num}")
    chap_id = chapter.get("id", "")
    id_attr = f' id="id-{chap_id}"' if chap_id else ""

    parts = [f'<h1{id_attr}>{renderer.render_inline(name)}</h1>\n']
    for entry in chapter.get("entries", []):
        parts.append(renderer.render(entry, depth=0))

    body = "".join(parts)
    return dedent(f"""\
        <?xml version="1.0" encoding="utf-8"?>
        <!DOCTYPE html>
        <html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
        <head>
          <meta charset="utf-8"/>
          <title>{renderer.render_inline(name)}</title>
          <link rel="stylesheet" type="text/css" href="../Styles/style.css"/>
        </head>
        <body>
        {body}
        </body>
        </html>
    """)


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

EPUB_CSS = """\
/* 5etools EPUB stylesheet — written for broad reader compatibility (FBReader, Calibre, etc.) */
body {
  font-family: Georgia, "Times New Roman", serif;
  font-size: 1em;
  line-height: 1.6;
  margin: 0;
  padding: 0;
  color: #1a1a1a;
}

/* Headings must explicitly reset indent and left margin — many readers
   (FBReader, Calibre) apply their own paragraph text-indent to all blocks. */
h1, h2, h3, h4, h5, h6 {
  text-indent: 0;
  margin-left: 0;
  padding-left: 0;
}

h1 {
  font-size: 1.8em;
  font-weight: bold;
  margin: 2em 0 0.7em 0;
  padding-bottom: 0.25em;
  border-bottom: 2px solid #8b0000;
}
h2 {
  font-size: 1.4em;
  font-weight: bold;
  margin: 2.5em 0 0.6em 0;
  color: #8b0000;
}
h3 {
  font-size: 1.2em;
  font-weight: bold;
  margin: 2em 0 0.5em 0;
}
h4 {
  font-size: 1.05em;
  font-weight: bold;
  margin: 1.8em 0 0.4em 0;
}
h5, h6 {
  font-size: 1em;
  font-weight: bold;
  font-style: italic;
  margin: 1.5em 0 0.3em 0;
}

p { margin: 0.5em 0; }

/* Boxed read-aloud text */
.readaloud {
  background: #f0ece0;
  border-left: 4px solid #8b6914;
  padding: 0.6em 1em;
  margin: 1em 0;
  font-style: italic;
}

/* General inset / sidebar */
.inset {
  border: 1px solid #aaa;
  border-radius: 4px;
  background: #f9f7f0;
  padding: 0.6em 1em;
  margin: 1em 0;
}
.inset-title {
  font-weight: bold;
  font-size: 1.05em;
  margin-bottom: 0.4em;
}

/* Tables */
table.tbl {
  width: 100%;
  border-collapse: collapse;
  margin: 1em 0;
  font-size: 0.9em;
}
table.tbl caption {
  font-weight: bold;
  margin-bottom: 0.3em;
}
table.tbl th {
  background: #8b0000;
  color: #fff;
  padding: 0.4em 0.6em;
  text-align: left;
}
table.tbl td {
  padding: 0.35em 0.6em;
  border-bottom: 1px solid #ddd;
}
table.tbl tr:nth-child(even) td { background: #f5f2ea; }

/* Lists */
ul, ol { margin: 0.4em 0 0.4em 2em; }
li { margin: 0.2em 0; }

/* Blockquote / poem */
blockquote {
  border-left: 3px solid #aaa;
  padding: 0.4em 1em;
  margin: 0.8em 0;
  color: #444;
  font-style: italic;
}
blockquote footer { font-style: normal; color: #666; font-size: 0.9em; }

/* Figures / images */
figure {
  text-align: center;
  margin: 1em 0;
}
figure img {
  max-width: 100%;
}
figcaption {
  font-size: 0.85em;
  color: #555;
  margin-top: 0.3em;
}

/* Gallery */
.gallery { display: block; }

/* Flowchart */
.flowchart {
  border: 1px solid #ccc;
  padding: 0.5em;
  margin: 1em 0;
}
.flow-block {
  border: 1px solid #8b0000;
  border-radius: 4px;
  padding: 0.4em 0.8em;
  margin: 0.5em 0;
  background: #fff8f0;
}
.flow-title { font-weight: bold; margin: 0; }

/* Statblock cross-reference */
.statblock-ref {
  background: #f0f0f0;
  border: 1px dashed #999;
  padding: 0.3em 0.6em;
  margin: 0.6em 0;
  font-size: 0.9em;
}

/* Page reference (small, grey) */
.page-ref { font-size: 0.75em; color: #888; font-weight: normal; }

/* Hyperlinks */
a { color: #8b0000; text-decoration: none; }
a:hover { text-decoration: underline; }
"""


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------

def load_adventure_meta(adventures_index: Path | None, adventure_id: str) -> dict:
    """Load book-level metadata from adventures.json for the given ID."""
    if not adventures_index or not adventures_index.exists():
        return {}
    with open(adventures_index, encoding="utf-8") as f:
        idx = json.load(f)
    for adv in idx.get("adventure", []):
        if adv.get("id", "").upper() == adventure_id.upper():
            return adv
    return {}


def guess_adventure_id(json_path: Path) -> str:
    """Derive adventure ID from the filename (e.g. adventure-wdh.json → WDH)."""
    stem  = json_path.stem          # "adventure-wdh"
    parts = stem.split("-", 1)
    return parts[1].upper() if len(parts) == 2 else stem.upper()


def format_ordinal(ordinal: dict | None) -> str:
    if not ordinal:
        return ""
    kind  = ordinal.get("type", "")
    ident = ordinal.get("identifier", "")
    if kind == "chapter":
        return f"Chapter {ident}"
    if kind == "appendix":
        return f"Appendix {ident}"
    return str(ident)


def _find_cover_path(chapters: list[dict]) -> str:
    """Return the cdn_path of the first internal image in the book (the cover)."""
    def _walk(obj):
        if isinstance(obj, list):
            for x in obj:
                r = _walk(x)
                if r: return r
        elif isinstance(obj, dict):
            if obj.get("type") == "image" and obj.get("href", {}).get("type") == "internal":
                p = obj["href"].get("path", "")
                if p: return p
            for v in obj.values():
                r = _walk(v)
                if r: return r
        return None
    for ch in chapters:
        r = _walk(ch)
        if r: return r
    return ""


def build_epub(
    adventure_json: Path,
    output_path: Path,
    adventures_index: Path | None = None,
    convert_jpeg: bool = False,
    no_images: bool = False,
    no_maps: bool = False,
    workers: int = 8,
    image_cache_dir: Path | None = None,
    font_dir: Path | None = None,
) -> None:
    print(f"Loading {adventure_json} …")
    with open(adventure_json, encoding="utf-8") as f:
        raw = json.load(f)

    chapters     = raw.get("data", [])
    adventure_id = guess_adventure_id(adventure_json)
    meta         = load_adventure_meta(adventures_index, adventure_id)

    title      = meta.get("name", adventure_id)
    author     = meta.get("author", "Wizards of the Coast")
    published  = meta.get("published", "")
    language   = "en"
    identifier = str(uuid.uuid4())

    print(f"Building EPUB: {title!r}  ({len(chapters)} sections)")

    # ---- Build ID map -------------------------------------------------------
    id_map = build_id_map(chapters)
    print(f"  Named anchors: {len(id_map)}")

    # ---- Image cache & prefetch ---------------------------------------------
    cache_dir = image_cache_dir or (Path.cwd() / "image-cache")
    image_cache: ImageCache | None = None

    # Determine cover image: prefer explicit cover from adventures.json metadata
    cover_meta = meta.get("cover")
    cover_cdn_path = ""
    if isinstance(cover_meta, dict):
        cover_cdn_path = cover_meta.get("path", "")
    elif isinstance(cover_meta, str):
        cover_cdn_path = cover_meta

    if not cover_cdn_path:
        cover_cdn_path = _find_cover_path(chapters)

    if not no_images:
        image_cache = ImageCache(
            cache_dir=cache_dir,
            convert_jpeg=convert_jpeg,
            no_images=False,
            no_maps=no_maps,
            workers=workers,
        )
        all_paths = collect_image_paths(chapters)
        if cover_cdn_path and cover_cdn_path not in all_paths:
            all_paths.insert(0, cover_cdn_path)
        print(f"  Images: {len(all_paths)} unique path(s)  "
              f"[cache: {cache_dir}]")
        image_cache.prefetch(all_paths)
    else:
        print("  Images: skipped (--no-images)")

    # ---- Set up renderer ----------------------------------------------------
    renderer = EntryRenderer(image_cache=image_cache, id_map=id_map)

    # ---- Build EPUB structure -----------------------------------------------
    book = epub.EpubBook()
    book.set_identifier(identifier)
    book.set_title(title)
    book.set_language(language)
    book.add_author(author)
    if published:
        book.add_metadata("DC", "date", published)
    book.add_metadata("DC", "description", meta.get("storyline", ""))

    # ---- Embed fonts (Nodesto Caps Condensed) --------------------------------
    resolved_font_dir = font_dir or Path.cwd()
    font_files = [
        ("NodestoCapsCondensed.otf",      "Nodesto Caps Condensed", "normal"),
        ("NodestoCapsCondensed-Bold.otf", "Nodesto Caps Condensed", "bold"),
    ]
    font_face_rules = []
    for fname, family, weight in font_files:
        font_path = resolved_font_dir / fname
        if font_path.exists():
            font_data = font_path.read_bytes()
            font_item = epub.EpubItem(
                uid=f"font_{fname.replace('.', '_')}",
                file_name=f"Fonts/{fname}",
                media_type="font/otf",
                content=font_data,
            )
            book.add_item(font_item)
            font_face_rules.append(
                f'@font-face {{\n'
                f'  font-family: "{family}";\n'
                f'  font-weight: {weight};\n'
                f'  font-style: normal;\n'
                f'  src: url("../Fonts/{fname}") format("opentype");\n'
                f'}}'
            )
            print(f"  Font: embedded {fname} ({len(font_data)//1024} KB)")
        else:
            print(f"  Font: {fname} not found at {font_path} — skipping", file=sys.stderr)

    font_css_block = ""
    heading_font_css = ""
    if font_face_rules:
        font_css_block = "\n".join(font_face_rules) + "\n\n"
        heading_font_css = (
            'h1, h2, h3, h4, h5, h6 {\n'
            '  font-family: "Nodesto Caps Condensed", Georgia, serif;\n'
            '  letter-spacing: 0.03em;\n'
            '}\n\n'
        )

    css_item = epub.EpubItem(
        uid="style",
        file_name="Styles/style.css",
        media_type="text/css",
        content=(font_css_block + heading_font_css + EPUB_CSS).encode("utf-8"),
    )
    book.add_item(css_item)

    # ---- Cover image + cover page -------------------------------------------
    cover_file_name: str = ""
    spine = ["nav"]
    toc   = []

    if cover_cdn_path and image_cache:
        cover_data = image_cache.get(cover_cdn_path, "art")
        if cover_data:
            ext = image_cache.extension()
            cover_file_name = f"Images/cover.{ext}"
            cover_img_item = epub.EpubItem(
                uid="cover-image",
                file_name=cover_file_name,
                media_type=image_cache.media_type(),
                content=cover_data,
            )
            book.add_item(cover_img_item)
            # Standard EPUB cover metadata (recognised by Kobo, Calibre, etc.)
            book.add_metadata(None, "meta", "", {"name": "cover", "content": "cover-image"})

            cover_src = f"../Images/cover.{ext}"
            cover_xhtml = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<!DOCTYPE html>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">\n'
                '<head><meta charset="utf-8"/><title>Cover</title>\n'
                '<style type="text/css">\n'
                'html,body{margin:0;padding:0;}\n'
                'div.cover{text-align:center;}\n'
                'img.cover{max-width:100%;max-height:100vh;display:block;margin:0 auto;}\n'
                '</style></head>\n'
                '<body><div class="cover">\n'
                f'<img class="cover" src="{cover_src}" alt="Cover"/>\n'
                '</div></body></html>\n'
            )
            cover_page = epub.EpubHtml(
                uid="cover-page",
                title="Cover",
                file_name="Text/cover.xhtml",
                lang=language,
            )
            cover_page.set_content(cover_xhtml.encode("utf-8"))
            book.add_item(cover_page)
            spine.insert(0, cover_page)
            print(f"  Cover: {cover_cdn_path}")

    # ---- Chapter pages ------------------------------------------------------
    for idx, chapter in enumerate(chapters):
        chap_name = chapter.get("name", f"Section {idx + 1}")
        ordinal   = chapter.get("ordinal")
        ord_label = format_ordinal(ordinal)
        display   = f"{ord_label}: {chap_name}" if ord_label else chap_name

        file_name = f"Text/chapter_{idx:03d}.xhtml"
        uid       = f"chapter_{idx:03d}"

        html_content = build_chapter_html(chapter, renderer, idx + 1)

        # Add images discovered while rendering this chapter.
        # Skip the cover image — it was already added above as a dedicated item.
        for img_item in renderer.flush_new_images():
            if img_item.file_name != cover_file_name:
                book.add_item(img_item)

        epub_chapter = epub.EpubHtml(
            title=display,
            file_name=file_name,
            lang=language,
        )
        epub_chapter.set_content(html_content.encode("utf-8"))
        epub_chapter.add_item(css_item)

        book.add_item(epub_chapter)
        spine.append(epub_chapter)
        toc.append(epub.Link(file_name, display, uid))

    # ---- Navigation ---------------------------------------------------------
    book.toc   = toc
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine

    print(f"Writing {output_path} …")
    epub.write_epub(str(output_path), book)
    size_kb = output_path.stat().st_size // 1024
    n_images = len(renderer._epub_images)
    print(f"Done!  {output_path}  ({size_kb} KB,  {len(chapters)} chapters,  {n_images} images)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a 5etools adventure JSON file to EPUB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "adventure_json",
        metavar="ADVENTURE_JSON",
        help="Path to the adventure JSON (e.g. 5etools-src/data/adventure/adventure-wdh.json)",
    )
    parser.add_argument(
        "--out", "-o",
        metavar="OUTPUT",
        default=None,
        help="Output EPUB path (default: <adventure-id>.epub in current directory)",
    )
    parser.add_argument(
        "--adventures-index",
        metavar="INDEX_JSON",
        default=None,
        help="Path to adventures.json for metadata (auto-detected by default)",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        default=False,
        help="Skip all image downloading (text-only EPUB)",
    )
    parser.add_argument(
        "--no-maps",
        action="store_true",
        default=False,
        help="Include art images but skip map/player-map images",
    )
    parser.add_argument(
        "--jpeg",
        action="store_true",
        default=False,
        help="Convert WebP images to JPEG for maximum reader compatibility (requires Pillow)",
    )
    parser.add_argument(
        "--workers",
        metavar="N",
        type=int,
        default=8,
        help="Parallel download threads (default: 8)",
    )
    parser.add_argument(
        "--image-cache",
        metavar="DIR",
        default=None,
        help="Image cache directory (default: ./image-cache/)",
    )
    parser.add_argument(
        "--font-dir",
        metavar="DIR",
        default=None,
        help="Directory containing NodestoCapsCondensed.otf and NodestoCapsCondensed-Bold.otf "
             "(default: current working directory)",
    )
    args = parser.parse_args()

    adventure_json = Path(args.adventure_json).resolve()
    if not adventure_json.exists():
        sys.exit(f"Error: file not found: {adventure_json}")

    adventure_id = guess_adventure_id(adventure_json)

    # Auto-detect adventures.json
    if args.adventures_index:
        adventures_index = Path(args.adventures_index).resolve()
    else:
        adventures_index = adventure_json.parent.parent / "adventures.json"
        if not adventures_index.exists():
            adventures_index = None

    if adventures_index and adventures_index.exists():
        print(f"Using metadata index: {adventures_index}")
    else:
        print("No adventures.json found; using defaults for metadata.")

    if args.jpeg and not _PILLOW_AVAILABLE:
        print("[WARN] --jpeg requested but Pillow is not installed; images will stay as WebP.",
              file=sys.stderr)

    output_path     = Path(args.out).resolve() if args.out else Path(f"{adventure_id.lower()}.epub").resolve()
    image_cache_dir = Path(args.image_cache).resolve() if args.image_cache else None
    font_dir        = Path(args.font_dir).resolve() if args.font_dir else None

    build_epub(
        adventure_json  = adventure_json,
        output_path     = output_path,
        adventures_index= adventures_index,
        convert_jpeg    = args.jpeg,
        no_images       = args.no_images,
        no_maps         = args.no_maps,
        workers         = args.workers,
        image_cache_dir = image_cache_dir,
        font_dir        = font_dir,
    )


if __name__ == "__main__":
    main()
