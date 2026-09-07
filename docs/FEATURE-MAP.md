# TubeBrain — Feature Mind Map

A complete map of every feature, command, and workflow in TubeBrain.

## Visual (Mermaid)

```mermaid
mindmap
  root((TubeBrain))

    Purpose
      Problem
        Forgetting videos
        Messy auto-captions
        Slow copy-paste
      Solution
        Clean captions
        Pipe to LLM
        Just ask

    Install
      Requirements
        Python 3.11+
        Tesseract OCR (optional)
        No API keys
      Steps
        git clone
        uv pip install -e .
        tb doctor

    Core workflow
      1 Record
        Single video
        Playlist (auto-detect)
        Output: run_id
      2 Export
        paragraph
        segments
        srt
        json
        --lang
        --audio
        --all
      3 Ask
        tb summarize
        tb ask
        tb chat
        raw pipe to claude

    Commands
      Download and inspect
        record
          URL or playlist
          --lang
        process
          --fps
          --ocr-lang
          --skip-frames
          --skip-ocr
          --frames-only
        runs
        inspect
        delete

      Export
        export
          --fmt paragraph
          --fmt segments
          --fmt srt
          --fmt json
          --lang
          --audio
          --all
          -o file

      LLM
        summarize
        ask
        chat
        mindmap
          --fmt mermaid (default)
          --fmt markdown
          --fmt json

      Search
        search
          -r run_id
          -t time range
          --kind
          --json

      Utility
        doctor

    Output formats
      paragraph
        One clean block
        Best for LLMs
        Best for RAG
      segments
        Timestamped paragraphs
        YouTube links
        Best for reading
      srt
        Standard subtitles
        Any video player
      json
        Metadata + segments
        OCR included
        Best for programs

    Caption cleaning
      Pipeline
        Sort by timestamp
        Merge within 3s
        Strip leading overlap
        Dedupe self-repeats
        Remove adjacent dupes
      Noise patterns
        Progressive sentences
        Self-repeating phrases
        Adjacent duplicates

    How it works
      Record
        yt-dlp downloads video
        yt-dlp downloads captions
      Process (optional)
        OpenCV extracts frames
        Tesseract runs OCR
      Store
        SQLite database
        runs run_id folder
        mp4, captions.json, frames/
      Export
        Cleaner transforms
        LLM pipe

    Storage
      runs/
        tubebrain.db
        run_id/
          title.mp4
          captions.json
          frames/ (optional)

    Use cases
      Summarize a video
      Extract todos/steps
      Find mentions of X
      Get subtitles
      Listen as audio
      Search across library
      Build knowledge base
      Process playlists
```

## Text tree

```
TubeBrain
│
├── PURPOSE
│   ├── Problem
│   │   • Forgetting video content
│   │   • Messy YouTube auto-captions (repeats, overlaps, progressive phrases)
│   │   • Manual copy-paste is painful
│   │
│   └── Solution
│       • Downloads + cleans captions
│       • Pipes clean text to any LLM
│       • "Ask instead of watch"
│
├── INSTALL
│   ├── Requirements: Python 3.11+, Tesseract (optional), no API keys
│   ├── Steps: clone → uv pip install -e . → tb doctor
│   └── tb doctor verifies: python, tesseract, yt-dlp, opencv, pytesseract
│
├── CORE WORKFLOW (3 steps)
│   ├── 1. RECORD — tb record <url|playlist>
│   ├── 2. EXPORT — tb export <run_id> --fmt {paragraph|segments|srt|json}
│   └── 3. ASK — tb summarize | tb ask | tb chat | pipe to claude
│
├── COMMANDS
│   │
│   ├── Download & Inspect
│   │   ├── tb record <url>            (single video or playlist, --lang)
│   │   ├── tb process <run_id>        (frames + OCR, --fps, --ocr-lang, skip flags)
│   │   ├── tb runs                    (list all videos)
│   │   ├── tb inspect <run_id>        (raw captions + OCR)
│   │   └── tb delete <run_id>         (remove a video)
│   │
│   ├── Export
│   │   ├── tb export <run_id> --fmt paragraph   (best for LLMs)
│   │   ├── tb export <run_id> --fmt segments   (timestamped + YouTube links)
│   │   ├── tb export <run_id> --fmt srt        (subtitles)
│   │   ├── tb export <run_id> --fmt json       (structured data)
│   │   ├── tb export <run_id> --lang es        (translation)
│   │   ├── tb export <run_id> --audio          (MP3)
│   │   ├── tb export --all -o library/         (batch export)
│   │   └── tb export -o file.txt               (write to file)
│   │
│   ├── LLM Shortcuts
│   │   ├── tb summarize <run_id>      (one-shot summary)
│   │   ├── tb ask <run_id> "..."      (single question)
│   │   ├── tb chat <run_id>           (interactive Q&A loop)
│   │   └── tb mindmap <run_id>        (structured mind map: mermaid|markdown|json)
│   │
│   ├── Search
│   │   ├── tb search "text"           (all videos)
│   │   ├── tb search "text" -r <id>   (one video)
│   │   ├── tb search "text" -t 0-60   (time range)
│   │   └── tb search "text" --kind transcript|frame|ocr
│   │
│   └── Utility
│       └── tb doctor                  (check dependencies)
│
├── OUTPUT FORMATS
│   ├── paragraph  → 1 clean block    (best for LLMs, RAG)
│   ├── segments   → timestamped      (best for reading)
│   ├── srt        → standard subs    (any video player)
│   └── json       → metadata + segs  (programs, RAG)
│
├── CAPTION CLEANING PIPELINE
│   ├── 1. Sort by timestamp
│   ├── 2. Merge segments within 3s gap
│   ├── 3. Strip leading overlap with previous text
│   ├── 4. Dedupe self-repeating phrases ("X X X" → "X")
│   └── 5. Remove adjacent duplicate words ("the the" → "the")
│
├── HOW IT WORKS (data flow)
│   URL → yt-dlp(video) → runs/<id>/<title>.mp4
│       → yt-dlp(captions) → captions.json
│       → OpenCV → frames/*.jpg (optional)
│       → Tesseract → OCR text (optional)
│       → Cleaner (merge + dedupe) → SQLite
│       → tb export → {paragraph|segments|srt|json|audio}
│       → | claude -p → LLM answer
│
├── STORAGE LAYOUT
│   runs/
│   ├── tubebrain.db          (searchable captions + OCR)
│   └── <run_id>/
│       ├── <title>.mp4
│       ├── captions.json
│       └── frames/           (only if tb process ran)
│
└── USE CASES
    ├── Summarize a video
    ├── Extract todos / steps / lists
    ├── Find mentions of a topic
    ├── Get subtitles for any video
    ├── Listen to a video as MP3
    ├── Search across a video library
    ├── Build a personal knowledge base
    └── Process a whole playlist
```

## Use it

- Open this in any Mermaid renderer (GitHub, VS Code Mermaid extension, mermaid.live)
- Copy the text tree into a mind-map tool like Markmap (markmap.js.org) or XMind
- Print the Mermaid block into an HTML file with `<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>` to get an interactive view
