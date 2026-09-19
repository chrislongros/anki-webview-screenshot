# Web View Screenshot for Anki

Firefox-style screenshots of any Anki web view: the reviewer, the card browser
preview, the card layout preview, and add-on panels such as AMBOSS.

## Install

In Anki, open Tools > Add-ons > Get Add-ons and enter the code **171106637**,
or visit the [AnkiWeb page](https://ankiweb.net/shared/info/171106637).

## Usage

Press **Ctrl+Shift+S** (Cmd+Shift+S on macOS) with the pointer over the web
view you want to capture, or with keyboard focus in it.

- Hover to highlight the element under the pointer, **click** to select it,
  or **drag** to select a region. The page still scrolls while you pick
  (mouse wheel, PageUp/PageDown), nested scroll boxes included.
- The selection then gets handles. Resize by the edges or corners, drag inside
  to move, nudge with the arrow keys (Shift = 10 px), **Alt+Up** widens the
  selection to the parent element and **Alt+Down** goes back. The selection
  stays on its content if you scroll. Click outside to start over.
- **Enter**, **Ctrl+C**, a **double-click** inside the selection or the
  **Capture** button takes the shot. **V** captures the visible area.
  **Esc** cancels.
- A selection taller or wider than the visible area is captured whole: the
  add-on scrolls the page in steps and stitches the pieces together, then
  scrolls back. Elements that stay fixed while scrolling (sticky headers)
  can repeat in such a capture.

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
