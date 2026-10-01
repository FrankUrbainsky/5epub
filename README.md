# 5etools EPUB Generator

A Python tool that converts 5e.tools adventure JSON files into fully offline, e-reader-ready **EPUB 3** eBooks.

Tested and optimized for e-ink readers like the **Kobo Clara BW**, desktop readers like **FBReader**, **Calibre**, and **Apple Books**.

---

## Features

- **Offline Images**: Automatically downloads and embeds all adventure artwork and battlemaps from the 5e.tools CDN.
- **Local Disk Cache**: Image downloads are cached in `./image-cache/` by default so repeated builds take only ~3 seconds instead of re-downloading.
- **D&D 5e Headline Font**: Embeds *Nodesto Caps Condensed* (Solbera's open-source clone of D&D's *Modesto Bold Condensed*) for all headings with `@font-face`.
- **E-Reader Cover Support**: Proper EPUB 2/3 cover metadata and a dedicated full-screen cover XHTML page for Kobo sleep screens and library thumbnails.
- **Cross-Reference Links**: Converts internal tags like `{@area ...}` into working EPUB hyperlinks pointing directly to the target chapter and section anchor.
- **Rich 5e.tools Formatting**: Renders read-aloud boxed text, insets/sidebars, tables, flowcharts, blockquotes, lists, and inline tags (`{@dice}`, `{@creature}`, `{@item}`, `{@spell}`, etc.).
- **E-Reader Compatible CSS**: Heading resets preventing unintended first-line indentation on strict or limited reader engines (like FBReader and Kobo).
- **Optional WebP to JPEG Conversion**: For older e-ink readers that do not support modern WebP images.

---

## Installation & Requirements

### 1. Prerequisites
- Python 3.10+
- `pip`

### 2. Install Python Dependencies

```bash
pip install ebooklib requests
```

*(Optional)* If you need to convert images to JPEG for older e-readers with the `--jpeg` option:
```bash
pip install Pillow
```

### 3. Clone or Download 5e.tools Adventure Source Data

Clone the 5etools source repository (or shallow clone for minimal disk usage):

```bash
git clone --depth 1 https://github.com/5etools-mirror-3/5etools-src/
```

Adventure files will be located under:
```
5etools-src/data/adventure/
```

### 4. Download D&D Headline Fonts (Optional but Recommended)

To display authentic D&D headings, place the font files in the project root (or point to them with `--font-dir`):

```bash
curl -L -o "NodestoCapsCondensed.otf" \
  "https://raw.githubusercontent.com/jonathonf/solbera-dnd-fonts/master/Nodesto%20Caps%20Condensed/Nodesto%20Caps%20Condensed.otf"

curl -L -o "NodestoCapsCondensed-Bold.otf" \
  "https://raw.githubusercontent.com/jonathonf/solbera-dnd-fonts/master/Nodesto%20Caps%20Condensed/NodestoCapsCondensed-Bold.otf"
```

*(If the font files are omitted, the EPUB will gracefully fall back to standard serif fonts).*

---

## Quick Start

### 1. Interactive Book Search & Creator (Recommended)

Search all official adventures from `adventures.json` by name, code, or storyline, see cover images and build status, and select books to generate:

```bash
python3 search_books.py
```

Search directly from the CLI:
```bash
python3 search_books.py "curse of strahd"
python3 search_books.py --create CoS
```

### 2. Direct Build

Generate an EPUB from *Waterdeep: Dragon Heist*:

```bash
python3 make_epub.py 5etools-src/data/adventure/adventure-wdh.json
```

Output file: `wdh.epub` (or specified via `--out`).

---

## Usage & Command-Line Options

```
python3 make_epub.py [OPTIONS] ADVENTURE_JSON
```

### Positional Arguments
- `ADVENTURE_JSON`: Path to the adventure JSON file (e.g., `5etools-src/data/adventure/adventure-wdh.json`).

### Options
| Option | Description |
|---|---|
| `-o`, `--out OUTPUT` | Custom path/filename for the generated EPUB (defaults to `<adventure_id>.epub`). |
| `--no-images` | Skip all image downloading and build a text-only EPUB (useful for quick previews). |
| `--no-maps` | Include illustrations and story art, but skip high-res map files to reduce EPUB size. |
| `--jpeg` | Convert all WebP images to JPEG during build (requires `Pillow`). |
| `--workers N` | Number of parallel worker threads for downloading images (default: `8`). |
| `--image-cache DIR` | Path to directory for cached images (default: `./image-cache/`). |
| `--font-dir DIR` | Directory containing `NodestoCapsCondensed.otf` (default: current directory). |
| `--adventures-index PATH` | Path to `adventures.json` for book metadata (auto-detected by default). |
| `-h`, `--help` | Show the help message and exit. |

---

## Examples

### 1. Standard build with custom output name
```bash
python3 make_epub.py 5etools-src/data/adventure/adventure-wdh.json -o "Waterdeep Dragon Heist.epub"
```

### 2. Fast text-only build for testing
```bash
python3 make_epub.py 5etools-src/data/adventure/adventure-wdh.json --no-images -o test.epub
```

### 3. Accelerated download for large adventures
```bash
python3 make_epub.py 5etools-src/data/adventure/adventure-rot.json --workers 16
```

### 4. Build for older e-readers (JPEG images, no heavy maps)
```bash
python3 make_epub.py 5etools-src/data/adventure/adventure-cos.json --jpeg --no-maps -o strahd-compact.epub
```

---

## Project Structure

```text
.
├── make_epub.py                    # Main generator script
├── setup.sh                        # Setup script for data and dependencies
├── NodestoCapsCondensed.otf        # Regular headline font
├── NodestoCapsCondensed-Bold.otf   # Bold headline font
├── image-cache/                    # Downloaded image cache (auto-created)
├── 5etools-src/                    # 5etools data repository
└── README.md                       # Documentation
```

---

## License & Credits

- D&D content structure and tags based on [5e.tools](https://5e.tools/).
- *Nodesto Caps Condensed* font created by Solbera under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
- EPUB generation powered by [`ebooklib`](https://github.com/aerkalov/ebooklib).
