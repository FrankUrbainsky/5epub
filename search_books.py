#!/usr/bin/env python3
"""
search_books.py - Search 5e.tools adventures and select books to create as EPUB.

Uses 5etools-src/data/adventures.json to index all official adventures,
their explicit names, IDs, cover image paths/URLs, and target files.
Provides an interactive search/selection prompt or a CLI query tool to
find and build EPUB books using make_epub.py.

Usage:
    python3 search_books.py                   # Interactive search & create mode
    python3 search_books.py "curse of strahd" # Search for specific book
    python3 search_books.py --list            # List all adventures
    python3 search_books.py --create CoS      # Directly build EPUB for CoS
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Optional rich library for enhanced terminal UI
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    _RICH_AVAILABLE = True
    console = Console()
except ImportError:
    _RICH_AVAILABLE = False
    console = None

CDN_BASE_IMG = "https://5e.tools/img/"


@dataclass
class AdventureBook:
    id: str
    name: str
    cover_path: str
    cover_url: str
    storyline: str
    group: str
    level_str: str
    published: str
    author: str
    source_json: Path
    source_exists: bool
    epub_file: Optional[Path]

    @property
    def status_label(self) -> str:
        if self.epub_file and self.epub_file.exists():
            return f"Built ({self.epub_file.name})"
        if self.source_exists:
            return "Ready"
        return "Source Missing"


def sanitize_filename(name: str) -> str:
    """Sanitize book name for safe filenames while keeping spaces."""
    cleaned = re.sub(r'[:/\\?*"><|]', ' ', name)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def find_adventures_json(custom_path: Optional[str] = None) -> Path:
    """Locate adventures.json from known standard locations or custom argument."""
    if custom_path:
        p = Path(custom_path).resolve()
        if p.exists():
            return p
        raise FileNotFoundError(f"Specified adventures index not found: {custom_path}")

    base_dir = Path(__file__).resolve().parent
    candidates = [
        base_dir / "5etools-src" / "data" / "adventures.json",
        Path.cwd() / "5etools-src" / "data" / "adventures.json",
        Path("/home/frank/Documents/git/5eToolsEpub/5etools-src/data/adventures.json"),
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()

    raise FileNotFoundError("Could not find '5etools-src/data/adventures.json'.")


def find_existing_epub(book_name: str, book_id: str, search_dir: Path) -> Optional[Path]:
    """Check if an EPUB matching this book or ID already exists in the workspace."""
    clean_name = sanitize_filename(book_name)
    candidates = [
        search_dir / f"{clean_name}.epub",
        search_dir / f"{book_name}.epub",
        search_dir / f"{book_id.lower()}.epub",
        search_dir / f"{book_id.upper()}.epub",
        search_dir / f"{book_id}.epub",
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    return None


def format_level(level_dict: Optional[dict]) -> str:
    """Format level dictionary (e.g. {'start': 1, 'end': 5}) into a string."""
    if not level_dict or not isinstance(level_dict, dict):
        return "—"
    start = level_dict.get("start")
    end = level_dict.get("end")
    if start is not None and end is not None:
        if start == end:
            return str(start)
        return f"{start}–{end}"
    if start is not None:
        return f"{start}+"
    return "—"


def load_adventures(index_path: Path, workspace_dir: Path) -> list[AdventureBook]:
    """Load and parse all adventures from adventures.json."""
    with open(index_path, encoding="utf-8") as f:
        data = json.load(f)

    raw_adventures = data.get("adventure", [])
    data_dir = index_path.parent
    adv_dir = data_dir / "adventure"

    books: list[AdventureBook] = []
    for item in raw_adventures:
        book_id = item.get("id", "").strip()
        book_name = item.get("name", "").strip()
        cover_info = item.get("cover") or {}
        cover_path = cover_info.get("path", "")
        cover_url = CDN_BASE_IMG + cover_path if cover_path else ""

        storyline = item.get("storyline", "") or ""
        group = item.get("group", "") or ""
        level_str = format_level(item.get("level"))
        published = item.get("published", "") or "—"
        author = item.get("author", "") or "—"

        source_json = adv_dir / f"adventure-{book_id.lower()}.json"
        source_exists = source_json.exists()
        epub_file = find_existing_epub(book_name, book_id, workspace_dir)

        books.append(
            AdventureBook(
                id=book_id,
                name=book_name,
                cover_path=cover_path,
                cover_url=cover_url,
                storyline=storyline,
                group=group,
                level_str=level_str,
                published=published,
                author=author,
                source_json=source_json,
                source_exists=source_exists,
                epub_file=epub_file,
            )
        )
    return books


def match_score(book: AdventureBook, query: str) -> int:
    """Calculate match relevance score for sorting search results.
    Higher score means better match. Returns 0 if no match."""
    q = query.strip().lower()
    if not q:
        return 1

    bid = book.id.lower()
    bname = book.name.lower()
    bstory = book.storyline.lower()
    bgroup = book.group.lower()

    if bid == q:
        return 1000
    if bname == q:
        return 900
    if bid.startswith(q):
        return 800
    if bname.startswith(q):
        return 700
    if q in bname:
        return 600
    if q in bid:
        return 500
    if q in bstory:
        return 400
    if q in bgroup:
        return 350

    # Token match: all words in query appear across fields
    tokens = q.split()
    haystack = f"{bid} {bname} {bstory} {bgroup}"
    if all(tok in haystack for tok in tokens):
        return 200

    return 0


def filter_books(books: list[AdventureBook], query: str) -> list[AdventureBook]:
    """Filter and sort books matching query."""
    if not query.strip():
        return books

    scored: list[tuple[int, AdventureBook]] = []
    for b in books:
        score = match_score(b, query)
        if score > 0:
            scored.append((score, b))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in scored]


def display_books_table(books: list[AdventureBook], title: str = "Adventures List") -> None:
    """Render list of books as a formatted table."""
    if not books:
        if _RICH_AVAILABLE:
            console.print("[yellow]No adventures matched your search.[/yellow]")
        else:
            print("No adventures matched your search.")
        return

    if _RICH_AVAILABLE:
        table = Table(title=title, show_header=True, header_style="bold cyan")
        table.add_column("#", style="dim", justify="right")
        table.add_column("ID", style="bold green", no_wrap=True)
        table.add_column("Name", style="bold white")
        table.add_column("Level", style="magenta", justify="center", no_wrap=True)
        table.add_column("Storyline / Group", style="blue")
        table.add_column("Cover Image", style="cyan")
        table.add_column("Status")

        for idx, b in enumerate(books, 1):
            status_text = Text()
            if b.epub_file and b.epub_file.exists():
                status_text.append("✓ Built", style="bold green")
            elif b.source_exists:
                status_text.append("• Ready", style="bold yellow")
            else:
                status_text.append("✗ Missing", style="bold red")

            story_info = b.storyline if b.storyline else b.group

            table.add_row(
                str(idx),
                b.id,
                b.name,
                b.level_str,
                story_info or "—",
                b.cover_path or "—",
                status_text,
            )
        console.print(table)
    else:
        print(f"\n=== {title} ({len(books)}) ===")
        fmt = "{:<4} {:<9} {:<36} {:<7} {:<20} {:<24} {:<12}"
        print(fmt.format("#", "ID", "Name", "Level", "Storyline", "Cover Image", "Status"))
        print("-" * 115)
        for idx, b in enumerate(books, 1):
            story_info = b.storyline if b.storyline else b.group
            status_str = "Built" if b.epub_file else ("Ready" if b.source_exists else "Missing")
            name_disp = (b.name[:33] + "...") if len(b.name) > 36 else b.name
            cover_disp = (b.cover_path[:21] + "...") if len(b.cover_path) > 24 else b.cover_path
            print(fmt.format(str(idx), b.id, name_disp, b.level_str, (story_info or "—")[:19], cover_disp, status_str))
        print("-" * 115)


def display_book_details(book: AdventureBook) -> None:
    """Display in-depth information about a specific book."""
    if _RICH_AVAILABLE:
        content = (
            f"[bold yellow]Title:[/bold yellow]        {book.name}\n"
            f"[bold yellow]ID / Code:[/bold yellow]    {book.id}\n"
            f"[bold yellow]Levels:[/bold yellow]       {book.level_str}\n"
            f"[bold yellow]Storyline:[/bold yellow]    {book.storyline or '—'}\n"
            f"[bold yellow]Group:[/bold yellow]        {book.group or '—'}\n"
            f"[bold yellow]Author:[/bold yellow]       {book.author}\n"
            f"[bold yellow]Published:[/bold yellow]    {book.published}\n"
            f"[bold yellow]Cover Path:[/bold yellow]   {book.cover_path}\n"
            f"[bold yellow]Cover URL:[/bold yellow]    {book.cover_url}\n"
            f"[bold yellow]Source File:[/bold yellow]  {book.source_json}\n"
            f"[bold yellow]EPUB Status:[/bold yellow]  {book.status_label}"
        )
        console.print(Panel(content, title=f"[bold green]Book Details: {book.id}[/bold green]", expand=False))
    else:
        print(f"\n=======================================================")
        print(f" Book Details: {book.name} ({book.id})")
        print(f"=======================================================")
        print(f" Title:        {book.name}")
        print(f" ID / Code:    {book.id}")
        print(f" Levels:       {book.level_str}")
        print(f" Storyline:    {book.storyline or '—'}")
        print(f" Group:        {book.group or '—'}")
        print(f" Author:       {book.author}")
        print(f" Published:    {book.published}")
        print(f" Cover Path:   {book.cover_path}")
        print(f" Cover URL:    {book.cover_url}")
        print(f" Source File:  {book.source_json}")
        print(f" EPUB Status:  {book.status_label}")
        print(f"=======================================================\n")


def build_epub_for_book(
    book: AdventureBook,
    workspace_dir: Path,
    use_title_name: bool = True,
    extra_flags: Optional[list[str]] = None,
    adventures_index: Optional[Path] = None,
) -> bool:
    """Execute make_epub.py to generate the EPUB for this book."""
    make_epub_path = workspace_dir / "make_epub.py"
    if not make_epub_path.exists():
        print(f"Error: make_epub.py not found at {make_epub_path}", file=sys.stderr)
        return False

    if not book.source_exists:
        print(f"Error: Adventure source JSON not found: {book.source_json}", file=sys.stderr)
        return False

    cmd = [sys.executable, str(make_epub_path)]
    if adventures_index and adventures_index.exists():
        cmd.extend(["--adventures-index", str(adventures_index)])

    if use_title_name:
        clean_name = sanitize_filename(book.name)
        out_file = workspace_dir / f"{clean_name}.epub"
        cmd.extend(["--out", str(out_file)])

    if extra_flags:
        cmd.extend(extra_flags)

    cmd.append(str(book.source_json))

    cmd_str = " ".join(shlex.quote(c) for c in cmd)
    if _RICH_AVAILABLE:
        console.print(f"[bold cyan]Running command:[/bold cyan] [dim]{cmd_str}[/dim]")
    else:
        print(f"Running command: {cmd_str}")

    try:
        ret = subprocess.run(cmd, cwd=str(workspace_dir))
        return ret.returncode == 0
    except Exception as exc:
        print(f"Error executing build command: {exc}", file=sys.stderr)
        return False


def interactive_menu(books: list[AdventureBook], workspace_dir: Path, index_file: Path) -> None:
    """Main interactive search and build loop."""
    current_results = books
    last_query = ""

    header = """
[bold cyan]╔═══════════════════════════════════════════════════════════════╗
║          5eTools Adventure Book Search & EPUB Creator         ║
╚═══════════════════════════════════════════════════════════════╝[/bold cyan]
Type a book name, code, or storyline to search.
Type [bold green]#<number>[/bold green] or [bold green]<ID>[/bold green] to select a book to create.
Type [bold yellow]'all'[/bold yellow] to list all books, or [bold red]'q'[/bold red] to quit.
"""
    if _RICH_AVAILABLE:
        console.print(header)
    else:
        print("\n=== 5eTools Adventure Book Search & EPUB Creator ===")
        print("Type a book name or ID to search. Type '#<number>' or '<ID>' to select.")
        print("Type 'all' to list all books, or 'q' to quit.\n")

    # Initially display a preview of popular/notable adventures
    preview = filter_books(books, "")[:15]
    display_books_table(preview, title=f"Available Adventures ({len(preview)} of {len(books)} shown)")

    while True:
        try:
            prompt = "\nSearch (or #number/ID, 'all', 'q'): "
            user_input = input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

        if not user_input:
            continue

        lowered = user_input.lower()
        if lowered in ("q", "quit", "exit"):
            print("Goodbye!")
            break

        if lowered in ("all", "*", "list"):
            current_results = books
            display_books_table(current_results, title=f"All Adventures ({len(books)})")
            continue

        # Check if user entered an exact ID (e.g. "WDH" or "CoS")
        exact_id_match = next((b for b in books if b.id.upper() == user_input.upper()), None)

        # Check if user entered a selection index (#1 or 1)
        index_match: Optional[AdventureBook] = None
        clean_num = user_input.lstrip("#")
        if clean_num.isdigit():
            idx = int(clean_num)
            if 1 <= idx <= len(current_results):
                index_match = current_results[idx - 1]

        selected_book = exact_id_match if (exact_id_match and len(user_input) <= 6) else index_match

        if selected_book:
            display_book_details(selected_book)
            print("Actions:")
            print(f"  [1] Create EPUB (Title: '{sanitize_filename(selected_book.name)}.epub')")
            print(f"  [2] Create EPUB (Short: '{selected_book.id.lower()}.epub')")
            print(f"  [3] Create EPUB without images (Fast build)")
            print(f"  [4] Print make_epub command only")
            print(f"  [5] Back to search")

            choice = input("Select action [1-5, default 1]: ").strip() or "1"
            if choice == "1":
                build_epub_for_book(selected_book, workspace_dir, use_title_name=True, adventures_index=index_file)
                # Refresh status
                selected_book.epub_file = find_existing_epub(selected_book.name, selected_book.id, workspace_dir)
            elif choice == "2":
                build_epub_for_book(selected_book, workspace_dir, use_title_name=False, adventures_index=index_file)
                selected_book.epub_file = find_existing_epub(selected_book.name, selected_book.id, workspace_dir)
            elif choice == "3":
                build_epub_for_book(selected_book, workspace_dir, use_title_name=True, extra_flags=["--no-images"], adventures_index=index_file)
                selected_book.epub_file = find_existing_epub(selected_book.name, selected_book.id, workspace_dir)
            elif choice == "4":
                cmd = f"python3 make_epub.py --out {shlex.quote(sanitize_filename(selected_book.name) + '.epub')} {shlex.quote(str(selected_book.source_json))}"
                print(f"\nCommand:\n  {cmd}\n")
            continue

        # Otherwise perform search
        last_query = user_input
        current_results = filter_books(books, last_query)
        display_books_table(current_results, title=f"Search Results for '{last_query}' ({len(current_results)} found)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search 5etools adventures and select books to create as EPUB."
    )
    parser.add_argument(
        "query",
        nargs="?",
        default="",
        help="Optional search query (e.g. 'strahd', 'dragon heist', 'lmop').",
    )
    parser.add_argument(
        "-s", "--search",
        dest="search_flag",
        default="",
        help="Filter and display books matching search term.",
    )
    parser.add_argument(
        "-l", "--list",
        action="store_true",
        help="List all official adventures from adventures.json.",
    )
    parser.add_argument(
        "-c", "--create", "--build",
        dest="build_target",
        metavar="ID_OR_NAME",
        help="Build EPUB for the matching book ID or name.",
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Force interactive search and select menu.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output search results as JSON.",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Build EPUB without downloading images (when using --create).",
    )
    parser.add_argument(
        "--no-maps",
        action="store_true",
        help="Build EPUB without map images (when using --create).",
    )
    parser.add_argument(
        "--jpeg",
        action="store_true",
        help="Convert WebP images to JPEG in EPUB (when using --create).",
    )
    parser.add_argument(
        "--index",
        dest="custom_index",
        help="Custom path to adventures.json file.",
    )

    args = parser.parse_args()

    workspace_dir = Path(__file__).resolve().parent
    try:
        index_file = find_adventures_json(args.custom_index)
    except FileNotFoundError as err:
        sys.exit(f"Error: {err}")

    books = load_adventures(index_file, workspace_dir)

    # 1. Direct build via CLI
    if args.build_target:
        matches = filter_books(books, args.build_target)
        if not matches:
            sys.exit(f"Error: No adventure found matching '{args.build_target}'.")
        target_book = matches[0]
        if _RICH_AVAILABLE:
            console.print(f"[bold green]Selected Book:[/bold green] {target_book.name} ({target_book.id})")
        else:
            print(f"Selected Book: {target_book.name} ({target_book.id})")

        extra_flags: list[str] = []
        if args.no_images:
            extra_flags.append("--no-images")
        if args.no_maps:
            extra_flags.append("--no-maps")
        if args.jpeg:
            extra_flags.append("--jpeg")

        success = build_epub_for_book(
            target_book,
            workspace_dir,
            use_title_name=True,
            extra_flags=extra_flags,
            adventures_index=index_file,
        )
        sys.exit(0 if success else 1)

    query = (args.query or args.search_flag).strip()

    # 2. JSON output
    if args.json:
        results = filter_books(books, query) if query else books
        out_list = [
            {
                "id": b.id,
                "name": b.name,
                "cover_path": b.cover_path,
                "cover_url": b.cover_url,
                "storyline": b.storyline,
                "group": b.group,
                "level": b.level_str,
                "published": b.published,
                "author": b.author,
                "source_json": str(b.source_json),
                "source_exists": b.source_exists,
                "epub_built": bool(b.epub_file),
                "epub_path": str(b.epub_file) if b.epub_file else None,
            }
            for b in results
        ]
        print(json.dumps(out_list, indent=2))
        return

    # 3. Explicit list option
    if args.list:
        display_books_table(books, title=f"All Official Adventures ({len(books)})")
        return

    # 4. Search query from CLI (non-interactive display)
    if query and not args.interactive:
        results = filter_books(books, query)
        display_books_table(results, title=f"Search Results for '{query}' ({len(results)} found)")
        if len(results) == 1:
            display_book_details(results[0])
            print(f"To build this EPUB, run:")
            print(f"  python3 search_books.py --create {results[0].id}\n")
        elif results:
            print(f"To build any of these EPUBs, run:")
            print(f"  python3 search_books.py --create <ID>\n")
        return

    # 5. Interactive mode (default when run with no arguments in a terminal)
    interactive_menu(books, workspace_dir, index_file)


if __name__ == "__main__":
    main()
