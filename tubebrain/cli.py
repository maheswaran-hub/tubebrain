"""
TubeBrain CLI.

Commands:
    tb record <url>          Download YouTube video (or playlist) + captions
    tb process <run_id>      Extract frames + OCR
    tb search <text>         Search extracted text
    tb export <run_id>       Export clean captions (paragraph/segments/srt/json)
    tb summarize <run_id>    Summarize a video with Claude
    tb ask <run_id>          Ask Claude a question about a video
    tb chat <run_id>         Interactive Q&A with a video
    tb mindmap <run_id>      Generate a mind map of a video
    tb runs                  List all videos
    tb inspect <run_id>      See one video's evidence
    tb delete <run_id>       Delete a video
    tb doctor                Check dependencies
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .formatter import (
    export_json,
    export_paragraph,
    export_segments,
    export_srt,
    format_run,
)
from .ingest import (
    OCRProcessor,
    download_captions,
    download_youtube,
    extract_frames,
    get_playlist_urls,
    get_video_duration,
)
from .models import Evidence, EvidenceKind, Run, SearchQuery, SourceType, new_run_id
from .store import EvidenceStore

app = typer.Typer(name="tb", no_args_is_help=True, add_completion=False)
console = Console()

DATA_DIR = Path("runs")
DATA_DIR.mkdir(exist_ok=True)
DEFAULT_DB = DATA_DIR / "tubebrain.db"


def _store() -> EvidenceStore:
    return EvidenceStore(DEFAULT_DB)


def _safe(s: str) -> str:
    return s.encode("ascii", "replace").decode("ascii")


def _fmt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ─── doctor ─────────────────────────────────────────────────────

@app.command()
def doctor() -> None:
    """Check that all dependencies are installed and working."""
    table = Table(title="TubeBrain doctor", show_header=True, header_style="bold magenta")
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Details", style="dim")

    all_ok = True

    def _check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal all_ok
        if not ok:
            all_ok = False
        table.add_row(name, "[green]OK[/green]" if ok else "[red]MISSING[/red]", detail or "-")

    py_ok = sys.version_info >= (3, 11)
    _check("python", py_ok, f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

    tesseract_path = shutil.which("tesseract")
    if tesseract_path:
        try:
            r = subprocess.run([tesseract_path, "--version"], capture_output=True, text=True, timeout=5)
            version = r.stdout.split("\n")[0].split()[-1] if r.stdout else "?"
            _check("tesseract", True, f"{version}  ({tesseract_path})")
        except Exception as e:
            _check("tesseract", False, f"error: {e}")
    else:
        _check("tesseract", False, "not on PATH — winget install UB-Mannheim.TesseractOCR --force")

    try:
        import yt_dlp
        _check("yt-dlp", True, yt_dlp.version.__version__)
    except ImportError:
        _check("yt-dlp", False, "pip install yt-dlp")

    try:
        import cv2
        _check("opencv", True, cv2.__version__)
    except ImportError:
        _check("opencv", False, "pip install opencv-python")

    try:
        import pytesseract
        ver = pytesseract.get_tesseract_version()
        _check("pytesseract", True, str(ver))
    except ImportError:
        _check("pytesseract", False, "pip install pytesseract")
    except Exception as e:
        _check("pytesseract", False, str(e))

    console.print(table)
    console.print()
    if all_ok:
        console.print("[green]All systems go.[/green]")
    else:
        console.print("[yellow]Install missing components and re-run `tb doctor`.[/yellow]")
        raise typer.Exit(1)


# ─── record ─────────────────────────────────────────────────────

def _record_single(url: str, lang: str) -> str | None:
    """Download one video and its captions. Returns the run_id, or None on failure."""
    store = _store()

    console.print(f"[cyan]Downloading:[/cyan] {url}")
    run_id = new_run_id()
    run_dir = DATA_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Download video
    try:
        video_path = download_youtube(url, run_dir)
    except Exception as e:
        console.print(f"[red]Download failed: {e}[/red]")
        return None

    # Download captions
    console.print("[cyan]Fetching captions...[/cyan]")
    captions = download_captions(url, run_dir, lang=lang)
    if captions:
        (run_dir / "captions.json").write_text(json.dumps(captions), encoding="utf-8")
        for cap in captions:
            store.store_evidence(Evidence(
                run_id=run_id,
                kind=EvidenceKind.TRANSCRIPT,
                timestamp=cap["start"],
                data={"text": cap["text"], "start": cap["start"], "end": cap["end"]},
            ))

    # Get duration
    duration = get_video_duration(video_path)

    # Create run
    run = Run(
        id=run_id,
        source_type=SourceType.YOUTUBE,
        source_path=str(video_path),
        duration=duration,
        metadata={
            "url": url,
            "title": video_path.stem,
            "caption_lang": lang,
        },
    )
    store.create_run(run)

    console.print(f"[green]Created run:[/green] {run_id}")
    console.print(f"  Title:    {_safe(video_path.stem)}")
    console.print(f"  Duration: {_fmt_ts(duration)}")
    console.print(f"  Captions: {len(captions)} segments")
    return run_id


@app.command()
def record(
    url: str = typer.Argument(..., help="YouTube video or playlist URL"),
    lang: str = typer.Option("en", "--lang", "-l", help="Caption language code"),
) -> None:
    """
    Download a YouTube video (or playlist) and its auto-captions.

    Examples:
        tb record https://www.youtube.com/watch?v=VIDEO_ID
        tb record https://www.youtube.com/playlist?list=PLAYLIST_ID
    """
    # Check if it's a playlist
    playlist_urls = get_playlist_urls(url)
    if playlist_urls:
        console.print(f"[cyan]Found playlist with {len(playlist_urls)} videos[/cyan]")
        for i, video_url in enumerate(playlist_urls, 1):
            console.print(f"\n[bold]({i}/{len(playlist_urls)})[/bold]")
            run_id = _record_single(video_url, lang)
            if run_id:
                console.print(f"  → [dim]tb export {run_id} --fmt paragraph[/dim]")
        console.print()
        return

    run_id = _record_single(url, lang)
    if run_id:
        console.print()
        console.print(f"[dim]Next: tb export {run_id} --fmt paragraph[/dim]")


# ─── process ────────────────────────────────────────────────────

@app.command()
def process(
    run_id: str = typer.Argument(..., help="Run ID to process"),
    fps: float = typer.Option(1.0, "--fps", "-f", help="Frames per second (0 = skip frames)"),
    ocr_lang: str = typer.Option("eng", "--ocr-lang", help="Tesseract language(s)"),
    skip_frames: bool = typer.Option(False, "--skip-frames", help="Skip frame extraction"),
    skip_ocr: bool = typer.Option(False, "--skip-ocr", help="Skip OCR processing"),
    frames_only: bool = typer.Option(False, "--frames-only", help="Extract frames only, no OCR"),
) -> None:
    """
    Extract frames, run OCR, or both.

    Examples:
        tb process abc12345 --fps 1             Extract frames + run OCR
        tb process abc12345 --skip-ocr          Extract frames only
        tb process abc12345 --skip-frames       OCR on existing frames only
        tb process abc12345 --skip-frames --skip-ocr   Captions only (already done in record)
    """
    store = _store()
    run = store.get_run(run_id)
    if not run:
        console.print(f"[red]Run not found: {run_id}[/red]")
        raise typer.Exit(1)

    run_dir = DATA_DIR / run_id
    video_path = Path(run.source_path)
    if not video_path.exists():
        console.print(f"[red]Video not found: {run.source_path}[/red]")
        raise typer.Exit(1)

    frames = []

    # Extract frames
    if not skip_frames and fps > 0:
        console.print(f"[cyan]Extracting frames @ {fps} fps...[/cyan]")
        frames_dir = run_dir / "frames"
        start = time.time()
        frames = extract_frames(video_path, frames_dir, fps=fps)
        for fi in frames:
            store.store_evidence(Evidence(
                run_id=run_id,
                kind=EvidenceKind.FRAME,
                timestamp=fi["timestamp"],
                file_path=fi["file_path"],
                data={"width": fi["width"], "height": fi["height"]},
            ))
        console.print(f"  > {len(frames)} frames extracted ({time.time()-start:.1f}s)")

    # OCR — runs on frames extracted in this call OR existing frames in DB
    do_ocr = not skip_ocr and not frames_only
    if do_ocr:
        console.print(f"[cyan]Running OCR ({ocr_lang})...[/cyan]")
        try:
            ocr = OCRProcessor(langs=[l.strip() for l in ocr_lang.split("+")])
        except Exception as e:
            console.print(f"[yellow]OCR unavailable: {e}[/yellow]")
        else:
            count = 0
            for fi in frames:
                try:
                    results = ocr.extract_text(fi["file_path"])
                except Exception:
                    continue
                for r in results:
                    store.store_evidence(Evidence(
                        run_id=run_id,
                        kind=EvidenceKind.OCR,
                        timestamp=fi["timestamp"],
                        data={"text": r["text"], "confidence": r["confidence"]},
                        file_path=fi["file_path"],
                    ))
                    count += 1
            console.print(f"  > {count} text regions found")

    # Summary
    parts = []
    if not skip_frames and fps > 0:
        parts.append(f"{len(frames)} frames")
    if do_ocr:
        parts.append("OCR")
    if skip_frames and skip_ocr:
        console.print("[dim]No frames or OCR processed (captions are already stored from tb record).[/dim]")
    elif parts:
        console.print(f"[green]Done.[/green] {' + '.join(parts)}. Run `tb export {run_id}` to export clean captions.")
    else:
        console.print(f"[green]Done.[/green] Run `tb export {run_id}` to export clean captions.")


# ─── search ─────────────────────────────────────────────────────

@app.command()
def search(
    query: str = typer.Argument(..., help="Text to search for"),
    run_id: str = typer.Option(None, "--run-id", "-r", help="Filter by run ID"),
    time_range: str = typer.Option(None, "--time", "-t", help="Time range: START-END (seconds)"),
    kind: str = typer.Option(None, "--kind", "-k", help="frame, ocr, transcript"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """
    Search extracted text.

    Examples:
        tb search "machine learning"
        tb search "API" -r abc12345
        tb search "chart" -t 60-180
    """
    store = _store()

    tr = None
    if time_range:
        parts = time_range.split("-")
        if len(parts) == 2:
            tr = (float(parts[0]), float(parts[1]))

    kinds = []
    if kind:
        for k in kind.split(","):
            try:
                kinds.append(EvidenceKind(k.strip()))
            except ValueError:
                console.print(f"[yellow]Unknown kind: {k}[/yellow]")

    sq = SearchQuery(query=query, run_id=run_id, time_range=tr, kinds=kinds, limit=limit)
    result = store.search_evidence(sq)

    if json_output:
        console.print(json.dumps({
            "total": result.total,
            "evidence": [e.to_dict() for e in result.evidence],
        }, indent=2))
        return

    if not result.evidence:
        console.print("[dim]No results found.[/dim]")
        return

    console.print(f"[green]Found {result.total} results in {result.took_ms:.1f}ms[/green]")
    console.print()
    for ev in result.evidence:
        ts = _fmt_ts(ev.timestamp)
        text = ev.data.get("text", "")
        console.print(f"  [cyan]{ts:>8}[/cyan]  [{ev.kind.value:11}]  {text[:120]}")


# ─── runs / inspect / delete ────────────────────────────────────

@app.command()
def runs() -> None:
    """List all processed videos."""
    store = _store()
    all_runs = store.list_runs()

    if not all_runs:
        console.print("[dim]No runs. Use `tb record <url>` to download a video.[/dim]")
        return

    table = Table(title=f"Videos ({len(all_runs)} total)")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="green")
    table.add_column("Duration", style="yellow")
    table.add_column("Captions", justify="right")
    table.add_column("Created")

    for r in all_runs:
        caps = store.search_evidence(SearchQuery(run_id=r.id, kinds=[EvidenceKind.TRANSCRIPT], limit=10000)).total
        table.add_row(
            r.id,
            _safe(r.metadata.get("title", "unknown")[:40]),
            _fmt_ts(r.duration),
            str(caps),
            r.created_at.strftime("%Y-%m-%d"),
        )

    console.print(table)


@app.command()
def inspect(
    run_id: str = typer.Argument(..., help="Run ID"),
    limit: int = typer.Option(15, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Inspect a run's details and evidence."""
    store = _store()
    run = store.get_run(run_id)
    if not run:
        console.print(f"[red]Run not found: {run_id}[/red]")
        raise typer.Exit(1)

    if json_output:
        evidence = store.search_evidence(SearchQuery(run_id=run_id, limit=10000)).evidence
        console.print(json.dumps({
            "run": run.to_dict(),
            "evidence": [e.to_dict() for e in evidence],
        }, indent=2))
        return

    console.print(f"[cyan bold]{_safe(run.metadata.get('title', 'Untitled'))}[/cyan bold]")
    console.print(f"  ID:       {run.id}")
    console.print(f"  URL:      {run.metadata.get('url', 'unknown')}")
    console.print(f"  Duration: {_fmt_ts(run.duration)}")
    console.print(f"  Created:  {run.created_at.isoformat()}")
    console.print()

    sq = SearchQuery(run_id=run_id, kinds=[EvidenceKind.TRANSCRIPT], limit=limit)
    caps = store.search_evidence(sq).evidence
    if caps:
        console.print(f"[green]Captions (showing {len(caps)}):[/green]")
        for c in caps:
            text = c.data.get("text", "").strip()
            if text:
                console.print(f"  {_fmt_ts(c.timestamp):>8}  {text[:120]}")

    sq2 = SearchQuery(run_id=run_id, kinds=[EvidenceKind.OCR], limit=limit)
    ocrs = store.search_evidence(sq2).evidence
    if ocrs:
        console.print()
        console.print(f"[green]On-screen text (showing {len(ocrs)}):[/green]")
        for o in ocrs:
            text = o.data.get("text", "").strip()
            if text:
                console.print(f"  {_fmt_ts(o.timestamp):>8}  {text[:120]}")


@app.command()
def delete(
    run_id: str = typer.Argument(..., help="Run ID to delete"),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """Delete a run and its video file."""
    store = _store()
    run = store.get_run(run_id)
    if not run:
        console.print(f"[red]Run not found: {run_id}[/red]")
        raise typer.Exit(1)

    if not force:
        if not typer.confirm(f"Delete run {run_id} ({run.metadata.get('title', 'unknown')})?"):
            raise typer.Abort()

    store.delete_run(run_id)
    run_dir = DATA_DIR / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    console.print(f"[green]Deleted:[/green] {run_id}")


# ─── LLM helpers ───────────────────────────────────────────────

def _ask_llm(text: str, prompt: str) -> str:
    """Pipe (prompt + text) into `claude -p` and return its stdout."""
    result = subprocess.run(
        ["claude", "-p", f"{prompt}\n\n{text}"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _load_run_text(run_id: str) -> tuple[Run, str]:
    """Load a run from the store and return (run, paragraph_text)."""
    store = _store()
    run = store.get_run(run_id)
    if not run:
        console.print(f"[red]Run not found: {run_id}[/red]")
        raise typer.Exit(1)
    text = export_paragraph(run, store)
    return run, text


# ─── summarize ─────────────────────────────────────────────────

@app.command()
def summarize(
    run_id: str = typer.Argument(..., help="Run ID to summarize"),
) -> None:
    """
    Summarize a video's captions using Claude.

    Example:
        tb summarize abc12345
    """
    try:
        _, text = _load_run_text(run_id)
    except SystemExit:
        return
    with console.status("[cyan]Summarizing with Claude...[/cyan]"):
        try:
            summary = _ask_llm(text, "Summarize this video transcript concisely.")
        except RuntimeError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)
    console.print(summary)


# ─── ask ───────────────────────────────────────────────────────

@app.command()
def ask(
    run_id: str = typer.Argument(..., help="Run ID"),
    question: str = typer.Argument(..., help="Question to ask about the video"),
) -> None:
    """
    Ask Claude a question about a video.

    Example:
        tb ask abc12345 "what repos are mentioned?"
    """
    try:
        _, text = _load_run_text(run_id)
    except SystemExit:
        return
    with console.status("[cyan]Asking Claude...[/cyan]"):
        try:
            answer = _ask_llm(text, question)
        except RuntimeError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)
    console.print(answer)


# ─── chat ──────────────────────────────────────────────────────

@app.command()
def chat(
    run_id: str = typer.Argument(..., help="Run ID to chat with"),
) -> None:
    """
    Interactive Q&A about a video. Ask multiple questions without re-running.

    Type a question and press Enter. Press Enter on an empty line to quit.

    Example:
        tb chat abc12345
    """
    try:
        run, text = _load_run_text(run_id)
    except SystemExit:
        return

    console.print(f"[green]Chatting with:[/green] {_safe(run.metadata.get('title', run_id))}")
    console.print("[dim]Press Enter on an empty line to quit.[/dim]\n")

    while True:
        try:
            question = console.input("[bold cyan]you>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not question:
            break
        with console.status("[cyan]thinking...[/cyan]"):
            try:
                answer = _ask_llm(text, question)
            except RuntimeError as e:
                console.print(f"[red]claude error: {e}[/red]")
                continue
        console.print(f"[bold green]claude>[/bold green] {answer}\n")


# ─── mindmap ───────────────────────────────────────────────────

MINDMAP_MERMAID_PROMPT = """\
You generate a Mermaid mindmap from a video transcript. Output ONLY valid Mermaid \
syntax — no explanation, no markdown fences, no preamble.

Rules:
- Start with: mindmap
- Use 1 root node: root((Title)) — use the video's main topic
- Add 3-7 main branches (capitalized, no parens)
- Each main branch can have 2-5 sub-branches
- Sub-branches can have 1-3 leaves if needed
- Keep node text short (1-6 words)
- Use plain text, no special characters that break Mermaid

Example output format:
mindmap
  root((Main Topic))
    Branch One
      Sub topic
        Detail
        Detail
    Branch Two
      Sub topic
"""

MINDMAP_MARKDOWN_PROMPT = """\
You generate a nested markdown mind map from a video transcript. Output ONLY the \
nested bullet structure — no explanation, no fences, no preamble.

Rules:
- Start with a single H1 (# Title) using the video's main topic
- Use nested bullets with 2-space indentation
- Top level: 3-7 main branches
- 2nd level: 2-5 sub-topics per branch
- 3rd level: 1-3 details per sub-topic
- Keep each line short (1-8 words)
- No other content (no intro, no outro)

Example format:
# Main Topic
- Branch One
  - Sub topic
    - Detail
    - Detail
- Branch Two
  - Sub topic
"""


@app.command()
def mindmap(
    run_id: str = typer.Argument(..., help="Run ID to generate a mind map from"),
    fmt: str = typer.Option(
        "mermaid",
        "--fmt",
        "-f",
        help="mermaid | markdown | json",
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write to file instead of stdout"
    ),
) -> None:
    """
    Generate a mind map of a video's key topics using Claude.

    The video's cleaned captions are sent to Claude, which structures them
    into a hierarchical mind map. Three output formats are supported:

      mermaid   Mermaid mindmap syntax (renders in GitHub, VS Code, mermaid.live)
      markdown  Nested bullets (Obsidian, any markdown editor)
      json      Structured tree (for programmatic use)

    Examples:
        tb mindmap abc12345                  # Mermaid to stdout
        tb mindmap abc12345 -f markdown      # Nested bullets
        tb mindmap abc12345 -o map.md        # Write to file
        tb mindmap abc12345 -f json -o map.json
    """
    try:
        run, text = _load_run_text(run_id)
    except SystemExit:
        return

    if fmt == "mermaid":
        prompt = MINDMAP_MERMAID_PROMPT
    elif fmt == "markdown":
        prompt = MINDMAP_MARKDOWN_PROMPT
    elif fmt == "json":
        prompt = (
            "Output ONLY a JSON array of nodes for a mind map. "
            "No prose, no fences, no explanation. "
            "Schema: [{\"text\": \"...\", \"children\": [...]}] "
            "Root is the video's main topic. 3-7 top-level children, "
            "2-5 grandchildren each, optional great-grandchildren."
        )
    else:
        console.print(
            f"[red]Unknown format: {fmt}[/red]  Valid options: mermaid, markdown, json"
        )
        raise typer.Exit(1)

    with console.status("[cyan]Generating mind map with Claude...[/cyan]"):
        try:
            result = _ask_llm(text, prompt)
        except RuntimeError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)

    # Strip accidental code fences the LLM sometimes adds
    cleaned = result.strip()
    if cleaned.startswith("```"):
        # Drop opening fence (and any language tag) + closing fence
        lines = cleaned.split("\n")
        cleaned = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        )

    if output:
        output.write_text(cleaned, encoding="utf-8")
        console.print(f"[green]Written:[/green] {output} ({len(cleaned):,} chars)")
    else:
        console.print(cleaned)


# ─── export ────────────────────────────────────────────────────

def _get_content(run: Run, store: EvidenceStore, fmt: str) -> str:
    """Return formatted content string for a run."""
    if fmt == "paragraph":
        return export_paragraph(run, store)
    elif fmt == "srt":
        return export_srt(run, store)
    elif fmt == "json":
        return json.dumps(export_json(run, store), indent=2, ensure_ascii=False)
    elif fmt == "segments":
        return export_segments(run, store)
    else:
        raise ValueError(f"Unknown format: {fmt}")


def _get_run_captions_for_lang(
    run: Run, store: EvidenceStore, lang: str
) -> str:
    """Return cleaned paragraph text in the requested language.

    If lang differs from the stored captions, try to download that language's
    captions via yt-dlp and clean them in-memory. Falls back to stored captions
    if the target language isn't available.
    """
    from .formatter import _merge_captions, _clean_caption

    stored_lang = run.metadata.get("caption_lang", "en")
    if lang == stored_lang:
        return export_paragraph(run, store)

    # Try to download target language captions
    console.print(f"[cyan]Fetching {lang} captions...[/cyan]")
    captions = download_captions(run.metadata["url"], DATA_DIR / run.id, lang=lang)
    if not captions:
        console.print(
            f"[yellow]No {lang} captions available. Using stored {stored_lang} captions.[/yellow]"
        )
        return export_paragraph(run, store)

    # Clean and merge captions in target language
    evidence_list = [
        Evidence(
            run_id=run.id,
            kind=EvidenceKind.TRANSCRIPT,
            timestamp=c["start"],
            data={"text": c["text"], "start": c["start"], "end": c["end"]},
        )
        for c in captions
    ]
    merged = _merge_captions(evidence_list)
    return " ".join(text for _, text in merged)


@app.command()
def export(
    run_id: str | None = typer.Argument(
        None, help="Run ID to export (omit if using --all)"
    ),
    fmt: str = typer.Option(
        "segments",
        "--fmt",
        "-f",
        help="paragraph | segments | srt | json",
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write to file instead of stdout"
    ),
    all: bool = typer.Option(
        False, "--all", help="Export all runs in the database"
    ),
    lang: str | None = typer.Option(
        None, "--lang", help="Export captions in a different language (en, es, fr...)"
    ),
    audio: bool = typer.Option(
        False, "--audio", help="Extract audio as MP3"
    ),
) -> None:
    """
    Export captions in a clean format for LLMs, subtitle players, or audio.

    Formats:
      paragraph  One clean paragraph — best for LLM summarization / RAG
      segments   Timestamped paragraphs with MM:SS links — best for reading
      srt        Standard SRT subtitle file — compatible with any video player
      json       Structured JSON with metadata + segments + on-screen text

    Examples:
        tb export abc12345 --fmt paragraph   # one clean paragraph
        tb export abc12345 --fmt srt        # SRT subtitles
        tb export abc12345 -f segments       # timestamped segments (default)
        tb export abc12345 -o notes.md      # write to notes.md
        tb export --all --fmt paragraph      # export all videos
        tb export abc12345 --lang es         # Spanish captions
        tb export abc12345 --audio           # extract MP3 audio
    """
    # --audio is a shortcut that ignores format
    if audio:
        _export_audio(run_id)
        return

    # --all ignores run_id
    if all:
        store = _store()
        all_runs = store.list_runs()
        if not all_runs:
            console.print("[yellow]No runs found.[/yellow]")
            return
        out_dir = output or Path("library")
        out_dir.mkdir(exist_ok=True)
        ext = "txt" if fmt in ("paragraph", "segments") else fmt
        for r in all_runs:
            filename = f"{r.id}.{ext}"
            try:
                content = _get_content(r, store, fmt)
                (out_dir / filename).write_text(content, encoding="utf-8")
                console.print(f"[green]Wrote:[/green] {out_dir / filename}")
            except Exception as e:
                console.print(f"[red]Error on {r.id}:[/red] {e}")
        return

    if not run_id:
        console.print("[red]Provide a run_id or use --all[/red]")
        raise typer.Exit(1)

    store = _store()
    run = store.get_run(run_id)
    if not run:
        console.print(f"[red]Run not found: {run_id}[/red]")
        raise typer.Exit(1)

    # If a different language was requested, swap to the in-memory translated text
    if lang:
        text = _get_run_captions_for_lang(run, store, lang)
        # text is a single cleaned paragraph — wrap it in a minimal content str
        if fmt in ("paragraph", "segments"):
            content = text
        else:
            # SRT/JSON need structured output; fall back to current run's format
            try:
                content = _get_content(run, store, fmt)
            except ValueError as e:
                console.print(f"[red]{e}[/red]  Valid options: paragraph, segments, srt, json")
                raise typer.Exit(1)
    else:
        try:
            content = _get_content(run, store, fmt)
        except ValueError as e:
            console.print(f"[red]{e}[/red]  Valid options: paragraph, segments, srt, json")
            raise typer.Exit(1)

    if output:
        output.write_text(content, encoding="utf-8")
        console.print(f"[green]Written:[/green] {output} ({len(content):,} chars)")
    else:
        console.print(content)


def _export_audio(run_id: str | None) -> None:
    """Extract MP3 audio from a video."""
    if not run_id:
        console.print("[red]--audio requires a run_id[/red]")
        raise typer.Exit(1)
    store = _store()
    run = store.get_run(run_id)
    if not run:
        console.print(f"[red]Run not found: {run_id}[/red]")
        raise typer.Exit(1)

    video = Path(run.source_path)
    if not video.exists():
        console.print(f"[red]Video not found: {video}[/red]")
        raise typer.Exit(1)

    out = video.parent / f"{video.stem}.mp3"
    result = subprocess.run(
        ["ffmpeg", "-i", str(video), "-b:a", "128k", "-y", str(out)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]ffmpeg failed:[/red] {result.stderr.strip()}")
        raise typer.Exit(1)
    console.print(f"[green]Wrote:[/green] {out}")


if __name__ == "__main__":
    app()
