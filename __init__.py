"""Web View Screenshot: Firefox-style screenshots of any Anki web view.

Works on whatever web view has keyboard focus, or the one under the mouse
pointer: the reviewer, the card browser preview, and add-on panels alike.

  Ctrl+Shift+S  pick: hover highlights the element under the pointer, click
                selects it, drag selects a region.  The selection then gets
                handles: resize by edges or corners, drag inside to move,
                arrow keys nudge (Shift = 10 px), click outside to start over.
                Enter, Ctrl+C or the Capture button takes the shot; V takes
                the visible area; Esc cancels.  Pressing the shortcut again
                also cancels.
  Ctrl+Shift+D  save the whole page, scrolled content included, as a PDF.

The PNG is copied to the clipboard and saved under `directory` (config.json);
either sink can be switched off there.
"""

import json
import os
import re
import time

from aqt import mw
from aqt.qt import (
    QApplication,
    QCursor,
    QEvent,
    QKeySequence,
    QObject,
    QRect,
    QTimer,
    QWebEngineView,
)
from aqt.utils import tooltip

_cfg = mw.addonManager.getConfig(__name__) or {}
OUT_DIR = os.path.expanduser(_cfg.get("directory", "~/Screenshots"))
SAVE_FILE = _cfg.get("save_to_file", True)
COPY_CLIPBOARD = _cfg.get("copy_to_clipboard", True)
KEY_PICK = QKeySequence(_cfg.get("shortcut_pick", "Ctrl+Shift+S"))
KEY_PDF = QKeySequence(_cfg.get("shortcut_fullpage_pdf", "Ctrl+Shift+D"))

POLL_MS = 80
PICKER_TIMEOUT_S = 300
GRAB_DELAY_MS = 150  # let the overlay's removal reach the compositor first

# Qt's runJavaScript() cannot await a Promise, so the picker leaves its answer
# in window.__ws.result and Python polls for it.
PICKER_JS = r"""
(function () {
  if (window.__ws) return;
  var ws = window.__ws = { result: null };
  var Z = 2147483647, GRIP = 8, BLUE = '#0a84ff';

  function el(tag, css, parent) {
    var n = document.createElement(tag);
    n.style.cssText = css;
    parent.appendChild(n);
    return n;
  }
  // `cover` owns the pointer (no page hover effects, our cursor); `root` is
  // click-through except for the toolbar.
  var cover = el('div', 'position:fixed;inset:0;z-index:' + (Z - 1) + ';cursor:crosshair', document.documentElement);
  var root = el('div', 'position:fixed;inset:0;z-index:' + Z + ';pointer-events:none;font:13px sans-serif', document.documentElement);
  var box = el('div', 'position:absolute;box-sizing:border-box;border:2px solid ' + BLUE + ';background:rgba(10,132,255,.18);display:none', root);
  var grips = {};
  [['nw', 'left:-7px;top:-7px'], ['n', 'left:calc(50% - 5px);top:-7px'], ['ne', 'right:-7px;top:-7px'],
   ['e', 'right:-7px;top:calc(50% - 5px)'], ['se', 'right:-7px;bottom:-7px'], ['s', 'left:calc(50% - 5px);bottom:-7px'],
   ['sw', 'left:-7px;bottom:-7px'], ['w', 'left:-7px;top:calc(50% - 5px)']
  ].forEach(function (g) {
    grips[g[0]] = el('div', 'position:absolute;width:10px;height:10px;box-sizing:border-box;border-radius:50%;background:#fff;border:2px solid ' + BLUE + ';display:none;' + g[1], box);
  });
  var size = el('div', 'position:absolute;background:' + BLUE + ';color:#fff;font-size:12px;padding:2px 6px;border-radius:3px;display:none', root);
  var hint = el('div', 'position:absolute;left:50%;top:8px;transform:translateX(-50%);background:rgba(0,0,0,.78);color:#fff;padding:6px 12px;border-radius:6px;white-space:nowrap', root);
  var bar = el('div', 'position:absolute;display:none;gap:6px;pointer-events:auto', root);
  function button(text, primary) {
    var b = el('button', 'cursor:pointer;padding:5px 12px;border-radius:4px;font:inherit;border:1px solid ' + BLUE + ';' +
      (primary ? 'background:' + BLUE + ';color:#fff' : 'background:#fff;color:' + BLUE), bar);
    b.type = 'button';
    b.textContent = text;
    return b;
  }
  var cancelBtn = button('Cancel', false), captureBtn = button('Capture', true);
  var HINTS = {
    pick: 'Click an element or drag a region · V = visible area · Esc = cancel',
    adjust: 'Drag handles to resize, inside to move, arrows to nudge · Enter or Ctrl+C = capture · click outside = start over · Esc = cancel'
  };
  var CURSORS = { nw: 'nwse-resize', se: 'nwse-resize', ne: 'nesw-resize', sw: 'nesw-resize',
                  n: 'ns-resize', s: 'ns-resize', e: 'ew-resize', w: 'ew-resize', move: 'move', out: 'crosshair' };

  var phase, sel = null, drag = null, hover = null, pending = null, queued = false;

  function rect(l, t, r, b) {
    var W = window.innerWidth, H = window.innerHeight;
    var x0 = Math.max(0, Math.min(l, r)), y0 = Math.max(0, Math.min(t, b));
    var x1 = Math.min(W, Math.max(l, r)), y1 = Math.min(H, Math.max(t, b));
    return { left: x0, top: y0, width: Math.max(0, x1 - x0), height: Math.max(0, y1 - y0) };
  }
  function elementRect(x, y) {
    var target = document.elementsFromPoint(x, y).find(function (n) { return n !== cover && !root.contains(n); });
    if (!target || target === document.documentElement) return rect(0, 0, window.innerWidth, window.innerHeight);
    var r = target.getBoundingClientRect();
    return rect(r.left, r.top, r.right, r.bottom);
  }
  function moved(r, dx, dy) {
    return { left: Math.max(0, Math.min(window.innerWidth - r.width, r.left + dx)),
             top: Math.max(0, Math.min(window.innerHeight - r.height, r.top + dy)),
             width: r.width, height: r.height };
  }
  function resized(edge, r, dx, dy) {
    var l = r.left, t = r.top, rt = r.left + r.width, bt = r.top + r.height;
    if (edge.indexOf('w') >= 0) l += dx;
    if (edge.indexOf('e') >= 0) rt += dx;
    if (edge.indexOf('n') >= 0) t += dy;
    if (edge.indexOf('s') >= 0) bt += dy;
    return rect(l, t, rt, bt);
  }
  function hit(x, y) {
    if (!sel) return 'out';
    var l = sel.left, t = sel.top, r = l + sel.width, b = t + sel.height;
    if (x < l - GRIP || x > r + GRIP || y < t - GRIP || y > b + GRIP) return 'out';
    var v = Math.abs(y - t) <= GRIP ? 'n' : Math.abs(y - b) <= GRIP ? 's' : '';
    var h = Math.abs(x - l) <= GRIP ? 'w' : Math.abs(x - r) <= GRIP ? 'e' : '';
    return (v + h) || 'move';
  }

  function draw(r) {
    box.style.display = 'block';
    box.style.left = r.left + 'px'; box.style.top = r.top + 'px';
    box.style.width = r.width + 'px'; box.style.height = r.height + 'px';
    size.style.display = 'block';
    size.textContent = Math.round(r.width) + ' × ' + Math.round(r.height);
    size.style.left = r.left + 'px';
    size.style.top = (r.top >= 22 ? r.top - 22 : r.top + 4) + 'px';
    if (phase !== 'adjust') return;
    var bw = bar.offsetWidth, bh = bar.offsetHeight;
    var top = r.top + r.height + 8;
    if (top + bh > window.innerHeight) top = r.top - bh - 8;
    if (top < 0) top = r.top + r.height - bh - 8;
    bar.style.top = Math.max(0, top) + 'px';
    bar.style.left = Math.max(0, Math.min(r.left + r.width - bw, window.innerWidth - bw)) + 'px';
  }
  function setPhase(p) {
    phase = p;
    var adjust = p === 'adjust';
    for (var k in grips) grips[k].style.display = adjust ? 'block' : 'none';
    bar.style.display = adjust ? 'flex' : 'none';
    hint.textContent = HINTS[p];
    cover.style.cursor = adjust ? 'default' : 'crosshair';
  }

  function onMove(e) {
    pending = e;
    if (queued) return;
    queued = true;
    requestAnimationFrame(function () {
      queued = false;
      update(pending.clientX, pending.clientY);
    });
  }
  function update(x, y) {
    if (!drag) {
      if (phase === 'adjust') { cover.style.cursor = CURSORS[hit(x, y)]; return; }
      hover = elementRect(x, y);
      draw(hover);
      return;
    }
    var dx = x - drag.x, dy = y - drag.y;
    if (drag.kind === 'new') {
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) drag.dragging = true;
      hover = drag.dragging ? rect(drag.x, drag.y, x, y) : elementRect(x, y);
      draw(hover);
    } else {
      sel = drag.kind === 'move' ? moved(drag.from, dx, dy) : resized(drag.kind, drag.from, dx, dy);
      draw(sel);
    }
  }
  function onDown(e) {
    swallow(e);
    if (e.button !== 0) return;
    if (e.target === captureBtn) return finish({ mode: 'rect', rect: sel });
    if (e.target === cancelBtn) return finish({ mode: 'cancel' });
    var part = phase === 'adjust' ? hit(e.clientX, e.clientY) : 'out';
    if (part === 'out') {
      setPhase('pick');
      sel = null;
      drag = { kind: 'new', x: e.clientX, y: e.clientY, dragging: false };
      draw(hover = elementRect(e.clientX, e.clientY));
    } else {
      drag = { kind: part, x: e.clientX, y: e.clientY, from: sel };
    }
  }
  function onUp(e) {
    swallow(e);
    if (e.button !== 0 || !drag) return;
    if (drag.kind === 'new') {
      if (!drag.dragging) hover = elementRect(e.clientX, e.clientY);
      if (hover.width > 0 && hover.height > 0) { sel = hover; setPhase('adjust'); draw(sel); }
    }
    drag = null;
    if (phase === 'adjust') cover.style.cursor = CURSORS[hit(e.clientX, e.clientY)];
  }
  function onKey(e) {
    swallow(e);
    if (e.key === 'Escape') return finish({ mode: 'cancel' });
    if (phase !== 'adjust') {
      if (e.key === 'v' || e.key === 'V') finish({ mode: 'visible' });
      return;
    }
    var copy = (e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'c' || e.key === 'C');
    if (e.key === 'Enter' || copy) return finish({ mode: 'rect', rect: sel });
    var step = e.shiftKey ? 10 : 1;
    var dx = e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0;
    var dy = e.key === 'ArrowUp' ? -step : e.key === 'ArrowDown' ? step : 0;
    if (dx || dy) draw(sel = moved(sel, dx, dy));
  }
  function swallow(e) { e.preventDefault(); e.stopImmediatePropagation(); }

  var listeners = [['mousemove', onMove], ['mousedown', onDown], ['mouseup', onUp], ['keydown', onKey],
                   ['click', swallow], ['dblclick', swallow], ['contextmenu', swallow], ['keyup', swallow], ['keypress', swallow]];
  listeners.forEach(function (l) { window.addEventListener(l[0], l[1], true); });

  function finish(result) {
    if (result.mode === 'rect' && !(result.rect && result.rect.width > 0 && result.rect.height > 0)) return;
    listeners.forEach(function (l) { window.removeEventListener(l[0], l[1], true); });
    root.remove();
    cover.remove();
    ws.result = result;
  }
  ws.cancel = function () { finish({ mode: 'cancel' }); };
  setPhase('pick');
})();
"""


def _web_view_of(widget):
    while widget is not None:
        if isinstance(widget, QWebEngineView):
            return widget
        widget = widget.parentWidget()
    return None


def _target_view():
    return (
        _web_view_of(QApplication.focusWidget())
        or _web_view_of(QApplication.widgetAt(QCursor.pos()))
        or mw.web
    )


def _out_path(view, ext):
    os.makedirs(OUT_DIR, exist_ok=True)
    # AnkiWebView shadows view.title() with a plain string; page().title() is safe.
    title = re.sub(r"[^\w.-]+", "_", view.page().title() or "webview").strip("_")[:60]
    return os.path.join(OUT_DIR, f"{time.strftime('%Y%m%d-%H%M%S')}-{title}.{ext}")


def _deliver(view, pix):
    if pix.isNull():
        tooltip("Screenshot failed: empty grab")
        return
    notes = []
    if COPY_CLIPBOARD:
        QApplication.clipboard().setPixmap(pix)
        notes.append("copied to the clipboard")
    if SAVE_FILE:
        path = _out_path(view, "png")
        pix.save(path, "PNG")
        notes.append(f"saved as {path}")
    tooltip("Screenshot " + " and ".join(notes), period=3000)


def _grab(view, rect=None):
    try:
        pix = view.grab()
    except RuntimeError:
        tooltip("Screenshot failed: the view was closed")
        return
    if rect and not pix.isNull():
        scale = pix.devicePixelRatio() * view.zoomFactor()
        crop = QRect(
            round(rect["left"] * scale), round(rect["top"] * scale),
            round(rect["width"] * scale), round(rect["height"] * scale),
        ).intersected(pix.rect())
        pix = pix.copy(crop)
    _deliver(view, pix)


class _Picker:
    active = None

    def __init__(self, view):
        self.view = view
        self.deadline = time.time() + PICKER_TIMEOUT_S
        # Parented to mw, not the view: if Anki destroys the view mid-pick the
        # poll must keep running to notice and clear `active`.
        self.timer = QTimer(mw)
        self.timer.timeout.connect(self._poll)
        view.page().runJavaScript(PICKER_JS)
        self.timer.start(POLL_MS)
        _Picker.active = self

    def cancel(self):
        """Close the picker in the page. False if the view no longer exists."""
        try:
            self.view.page().runJavaScript("window.__ws && window.__ws.cancel()")
            return True
        except RuntimeError:
            self._done()
            return False

    def _poll(self):
        if time.time() > self.deadline:
            self._done()
            self.cancel()
            return
        try:
            self.view.page().runJavaScript(
                "JSON.stringify(window.__ws ? window.__ws.result : {mode:'gone'})", self._on_result
            )
        except RuntimeError:
            self._done()

    def _on_result(self, raw):
        if not self.timer.isActive():
            return
        result = json.loads(raw) if raw else None
        if not result:
            return
        self._done()
        try:
            self.view.page().runJavaScript("delete window.__ws")
        except RuntimeError:
            return
        mode = result.get("mode")
        if mode == "rect":
            QTimer.singleShot(GRAB_DELAY_MS, lambda: _grab(self.view, result["rect"]))
        elif mode == "visible":
            QTimer.singleShot(GRAB_DELAY_MS, lambda: _grab(self.view))
        elif mode == "cancel":
            tooltip("Screenshot cancelled")

    def _done(self):
        self.timer.stop()
        _Picker.active = None


def start_picker():
    if _Picker.active and _Picker.active.cancel():
        return
    view = _target_view()
    if view is None:
        tooltip("No web view to capture")
        return
    _Picker(view)


def save_full_page_pdf():
    view = _target_view()
    if view is None:
        tooltip("No web view to capture")
        return
    page = view.page()

    def done(path, ok):
        tooltip(f"Saved {path}" if ok else f"PDF export failed: {path}", period=3000)

    try:
        page.pdfPrintingFinished.disconnect()
    except TypeError:
        pass
    page.pdfPrintingFinished.connect(done)
    page.printToPdf(_out_path(view, "pdf"))


class _Shortcuts(QObject):
    # One key press can reach this filter twice (QtWebEngine forwards it from
    # its focus widget to the view), so repeats within DEBOUNCE_S are dropped.
    DEBOUNCE_S = 0.4

    def __init__(self):
        super().__init__()
        self._last_fire = 0.0

    def eventFilter(self, _obj, ev):
        if ev.type() not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            return False
        pressed = QKeySequence(ev.keyCombination())
        if pressed == KEY_PICK:
            action = start_picker
        elif pressed == KEY_PDF:
            action = save_full_page_pdf
        else:
            return False
        if ev.type() == QEvent.Type.ShortcutOverride:
            ev.accept()
            return True
        now = time.monotonic()
        if ev.isAutoRepeat() or now - self._last_fire < self.DEBOUNCE_S:
            return True
        self._last_fire = now
        action()
        return True


_shortcuts = _Shortcuts()  # keep a reference or the filter is garbage-collected
QApplication.instance().installEventFilter(_shortcuts)
