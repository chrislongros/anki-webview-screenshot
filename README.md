# Web View Screenshot for Anki

Firefox-style screenshots of any Anki web view: the reviewer, the card browser
preview, the card layout preview, and add-on panels such as AMBOSS.

## Usage

Press **Ctrl+Shift+S** (Cmd+Shift+S on macOS) with the pointer or keyboard
focus on the web view you want to capture.

- Hover to highlight the element under the pointer, **click** to select it,
  or **drag** to select a region.
- The selection then gets handles. Resize by the edges or corners, drag inside
  to move, nudge with the arrow keys (Shift = 10 px), click outside to start
  over.
- **Enter**, **Ctrl+C** or the **Capture** button takes the shot.
  **V** captures the visible area. **Esc** cancels.

The PNG is copied to the clipboard and saved under `~/Screenshots`. Either can
be switched off, and both shortcuts changed, in the add-on's config.

**Ctrl+Shift+D** saves the whole page, scrolled content included, as a PDF.

## Requirements

Anki 23.10 or newer (Qt 6). Windows, macOS and Linux.

## How it works

The picker is a small script injected into the page, so it sees the page's
real element boxes and works at any zoom level or display scale. The capture
itself is a Qt widget grab cropped to the chosen rectangle, so no OS-level
screen capture permission is needed.

## Licence

GNU AGPL v3 or later, like Anki itself.
