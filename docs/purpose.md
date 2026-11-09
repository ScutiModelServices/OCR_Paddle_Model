# Purpose

**Kind:** image(s) → text (OCR / document parsing)

**Group:** OCR

**Tags:** `cpu`, `gpu`, `not-yet-run`

| | |
|---|---|
| Input | One PNG, JPEG or WebP page image as the request body (`POST /v1/ocr`). |
| Output | JSON with the page text: Markdown from PaddleOCR-VL on a capable GPU, plain lines from PP-OCRv5 on the CPU. |
| Runs on | Picks its model at start. NVIDIA GPU (Linux container, compute capability 8.0+, enough memory): PaddleOCR-VL. Anything else, including Windows: PP-OCRv5 on the CPU. Only the chosen model is downloaded. Never run yet. |

**Use for:** page OCR on machines with or without a GPU. Called by `ToolServices/Reader`.
**Not for:** native GPU use on Windows; layout-faithful tables from the CPU model.
