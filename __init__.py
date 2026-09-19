"""Web View Screenshot: Firefox-style screenshots of any Anki web view.

Works on the web view under the mouse pointer, or the one with keyboard
focus: the reviewer, the card browser preview, and add-on panels alike.

  Ctrl+Shift+S  pick: hover highlights the element under the pointer, click
                selects it, drag selects a region.  The page still scrolls
                (wheel, PageUp/PageDown) while you pick.  The selection then
                gets handles: resize by edges or corners, drag inside to move,
                arrow keys nudge (Shift = 10 px), Alt+Up/Down widen to the
                parent element or back to the child, click outside to start
                over.  Enter, Ctrl+C, a double-click or the Capture button
                takes the shot; V takes the visible area; Esc cancels.
                Pressing the shortcut again also cancels.  A selection taller
                or wider than the visible area is captured by scrolling the
                page and stitching the pieces.
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
    QImage,
    QKeySequence,
    QObject,
    QPainter,
    QPoint,
    QRect,
    QTimer,
    QWebEngineView,
    Qt,
)
from aqt.utils import tooltip

_cfg = mw.addonManager.getConfig(__name__) or {}
OUT_DIR = os.path.expanduser(_cfg.get("directory", "~/Screenshots"))
SAVE_FILE = _cfg.get("save_to_file", True)
COPY_CLIPBOARD = _cfg.get("copy_to_clipboard", True)
KEY_PICK = QKeySequence(_cfg.get("shortcut_pick", "Ctrl+Shift+S"))
KEY_PDF = QKeySequence(_cfg.get("shortcut_fullpage_pdf", "Ctrl+Shift+D"))

POLL_MS = 30
PICKER_TIMEOUT_S = 300
STEP_TIMEOUT_S = 3

# Qt's runJavaScript() cannot await a Promise, so the page leaves its answers
# in window.__ws.result and Python polls for them.
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
    pick: 'Click an element or drag a region · scroll to reach more · V = visible area · Esc = cancel',
    adjust: 'Drag handles to resize, inside to move · arrows nudge · Alt+↑/↓ = parent/child · Enter or double-click = capture · Esc = cancel'
  };
  var CURSORS = { nw: 'nwse-resize', se: 'nwse-resize', ne: 'nesw-resize', sw: 'nesw-resize',
                  n: 'ns-resize', s: 'ns-resize', e: 'ew-resize', w: 'ew-resize', move: 'move', out: 'crosshair' };

  var phase, sel = null, drag = null, hover = null, hoverEl = null;
  var picked = null, children = [], anchor = null;
  var pointer = { x: 0, y: 0 }, queued = false;

  function rect(l, t, r, b) {
    return { left: Math.min(l, r), top: Math.min(t, b), width: Math.abs(r - l), height: Math.abs(t - b) };
  }
  function elementAt(x, y) {
    return document.elementsFromPoint(x, y).find(function (n) { return n !== cover && !root.contains(n); }) || null;
  }
  function boundsOf(node) {
    if (!node || node === document.documentElement) return rect(0, 0, window.innerWidth, window.innerHeight);
    var r = node.getBoundingClientRect();
    return rect(r.left, r.top, r.right, r.bottom);
  }
  // The nearest scroll container from `node` up; null means the window itself.
  function scrollerOf(node) {
    for (var n = node; n && n !== document.body && n !== document.documentElement; n = n.parentElement) {
      var cs = getComputedStyle(n);
      if ((/(auto|scroll)/.test(cs.overflowY) && n.scrollHeight > n.clientHeight) ||
          (/(auto|scroll)/.test(cs.overflowX) && n.scrollWidth > n.clientWidth)) return n;
    }
    return null;
  }
  function scrollAt(x, y, dx, dy) {
    var s = scrollerOf(elementAt(x, y));
    if (s) { s.scrollLeft += dx; s.scrollTop += dy; } else window.scrollBy(dx, dy);
  }
  function moved(r, dx, dy) {
    return { left: r.left + dx, top: r.top + dy, width: r.width, height: r.height };
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

  // The selection is remembered relative to the element under its centre, so
  // it stays on the same content when the page scrolls.
  function setSel(r, node) {
    sel = r;
    var a = node || elementAt(r.left + r.width / 2, r.top + r.height / 2) || document.documentElement;
    var ar = a.getBoundingClientRect();
    anchor = { node: a, dx: r.left - ar.left, dy: r.top - ar.top };
  }
  function pickElement(node) {
    picked = node;
    setSel(boundsOf(node), node);
    setPhase('adjust');
    draw(sel);
  }

  function draw(r) {
    box.style.display = 'block';
    box.style.left = r.left + 'px'; box.style.top = r.top + 'px';
    box.style.width = r.width + 'px'; box.style.height = r.height + 'px';
    size.style.display = 'block';
    size.textContent = Math.round(r.width) + ' × ' + Math.round(r.height);
    size.style.left = Math.max(4, r.left) + 'px';
    var st = r.top - 22;
    if (st < 4) st = Math.max(4, r.top + 4);
    size.style.top = st + 'px';
    if (phase !== 'adjust') return;
    var bw = bar.offsetWidth, bh = bar.offsetHeight, vw = window.innerWidth, vh = window.innerHeight;
    var bottom = Math.min(r.top + r.height, vh), top = bottom + 8;
    if (top + bh > vh) top = Math.max(r.top, 0) - bh - 8;
    if (top < 0) top = bottom - bh - 8;
    bar.style.top = Math.max(0, Math.min(vh - bh, top)) + 'px';
    bar.style.left = Math.max(0, Math.min(r.left + r.width - bw, vw - bw)) + 'px';
  }
  function setPhase(p) {
    phase = p;
    var adjust = p === 'adjust';
    for (var k in grips) grips[k].style.display = adjust ? 'block' : 'none';
    bar.style.display = adjust ? 'flex' : 'none';
    hint.textContent = HINTS[p];
    cover.style.cursor = adjust ? 'default' : 'crosshair';
  }

  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(function () {
      queued = false;
      update(pointer.x, pointer.y);
    });
  }
  function onMove(e) {
    pointer = { x: e.clientX, y: e.clientY };
    schedule();
  }
  function update(x, y) {
    if (!drag) {
      if (phase === 'adjust') { cover.style.cursor = CURSORS[hit(x, y)]; return; }
      hoverEl = elementAt(x, y);
      draw(hover = boundsOf(hoverEl));
      return;
    }
    var dx = x - drag.x, dy = y - drag.y;
    if (drag.kind === 'new') {
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) drag.dragging = true;
      if (drag.dragging) hover = rect(drag.x, drag.y, x, y);
      else { hoverEl = elementAt(x, y); hover = boundsOf(hoverEl); }
      draw(hover);
    } else {
      sel = drag.kind === 'move' ? moved(drag.from, dx, dy) : resized(drag.kind, drag.from, dx, dy);
      draw(sel);
    }
  }
  function onScroll() {
    if (drag) return;
    if (phase !== 'adjust') return schedule();
    if (!anchor || !anchor.node.isConnected) return;
    var ar = anchor.node.getBoundingClientRect();
    sel = { left: ar.left + anchor.dx, top: ar.top + anchor.dy, width: sel.width, height: sel.height };
    draw(sel);
  }
  function onWheel(e) {
    swallow(e);
    var k = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? window.innerHeight : 1;
    scrollAt(e.clientX, e.clientY, e.deltaX * k, e.deltaY * k);
  }
  function onDown(e) {
    swallow(e);
    if (e.button !== 0) return;
    if (e.target === captureBtn) return finish({ mode: 'rect', rect: sel });
    if (e.target === cancelBtn) return finish({ mode: 'cancel' });
    var part = phase === 'adjust' ? hit(e.clientX, e.clientY) : 'out';
    if (part === 'move' && e.detail >= 2) return finish({ mode: 'rect', rect: sel });
    if (part === 'out') {
      setPhase('pick');
      sel = null; picked = null; children = [];
      drag = { kind: 'new', x: e.clientX, y: e.clientY, dragging: false };
      hoverEl = elementAt(e.clientX, e.clientY);
      draw(hover = boundsOf(hoverEl));
    } else {
      drag = { kind: part, x: e.clientX, y: e.clientY, from: sel };
    }
  }
  function onUp(e) {
    swallow(e);
    if (e.button !== 0 || !drag) return;
    if (drag.kind !== 'new') {
      setSel(sel);
    } else if (!drag.dragging) {
      pickElement(elementAt(e.clientX, e.clientY) || document.documentElement);
    } else if (hover.width > 0 && hover.height > 0) {
      picked = null;
      setSel(hover);
      setPhase('adjust');
      draw(sel);
    }
    drag = null;
    if (phase === 'adjust') cover.style.cursor = CURSORS[hit(e.clientX, e.clientY)];
  }
  function stepTree(up) {
    if (!picked) return;
    if (up) {
      if (!picked.parentElement) return;
      children.push(picked);
      pickElement(picked.parentElement);
    } else if (children.length) {
      pickElement(children.pop());
    }
  }
  function onKey(e) {
    swallow(e);
    if (e.key === 'Escape') return finish({ mode: 'cancel' });
    if (e.key === 'PageDown' || e.key === 'PageUp') {
      return scrollAt(pointer.x, pointer.y, 0, (e.key === 'PageDown' ? 0.9 : -0.9) * window.innerHeight);
    }
    if (phase !== 'adjust') {
      if (e.key === 'v' || e.key === 'V') finish({ mode: 'visible' });
      return;
    }
    var copy = (e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'c' || e.key === 'C');
    if (e.key === 'Enter' || copy) return finish({ mode: 'rect', rect: sel });
    if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) return stepTree(e.key === 'ArrowUp');
    var step = e.shiftKey ? 10 : 1;
    var dx = e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0;
    var dy = e.key === 'ArrowUp' ? -step : e.key === 'ArrowDown' ? step : 0;
    if (dx || dy) { setSel(moved(sel, dx, dy)); draw(sel); }
  }
  function swallow(e) { e.preventDefault(); e.stopImmediatePropagation(); }

  var listeners = [['mousemove', onMove], ['mousedown', onDown], ['mouseup', onUp], ['keydown', onKey],
                   ['wheel', onWheel], ['scroll', onScroll],
                   ['click', swallow], ['dblclick', swallow], ['contextmenu', swallow], ['keyup', swallow], ['keypress', swallow]];
  listeners.forEach(function (l) { window.addEventListener(l[0], l[1], { capture: true, passive: false }); });

  // Two frames and a beat, so a grab afterwards sees the page without the overlay.
  function settle(fn) {
    requestAnimationFrame(function () { requestAnimationFrame(function () { setTimeout(fn, 40); }); });
  }

  // Capturing beyond the visible area: Python asks for scroll offsets relative
  // to where the page was when the selection was made, grabs, and stitches.
  var scroller = null, origin = null, behaviour = null;
  function pos() {
    return scroller ? { x: scroller.scrollLeft, y: scroller.scrollTop } : { x: window.scrollX, y: window.scrollY };
  }
  function visible() {
    var vw = window.innerWidth, vh = window.innerHeight;
    if (!scroller) return { left: 0, top: 0, right: vw, bottom: vh };
    var r = scroller.getBoundingClientRect();
    return { left: Math.max(0, r.left), top: Math.max(0, r.top),
             right: Math.min(vw, r.left + scroller.clientWidth), bottom: Math.min(vh, r.top + scroller.clientHeight) };
  }
  function prepareScroller() {
    // A picked element scrolls inside its ancestors; a drawn region scrolls
    // with whatever it was drawn over.
    scroller = scrollerOf(picked ? picked.parentElement : anchor && anchor.node);
    origin = pos();
    var s = (scroller || document.documentElement).style;
    behaviour = s.scrollBehavior;
    s.scrollBehavior = 'auto';
    return visible();
  }
  ws.scroll = function (dx, dy) {
    ws.result = null;
    if (scroller) { scroller.scrollLeft = origin.x + dx; scroller.scrollTop = origin.y + dy; }
    else window.scrollTo(origin.x + dx, origin.y + dy);
    settle(function () {
      var p = pos();
      ws.result = { dx: p.x - origin.x, dy: p.y - origin.y, vis: visible() };
    });
  };
  ws.done = function (r) {
    if (origin) {
      if (scroller) { scroller.scrollLeft = origin.x; scroller.scrollTop = origin.y; }
      else window.scrollTo(origin.x, origin.y);
      (scroller || document.documentElement).style.scrollBehavior = behaviour;
    }
    r = r || { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight };
    var flash = el('div', 'position:fixed;pointer-events:none;background:#fff;opacity:.7;transition:opacity .3s ease-out;z-index:' + Z +
      ';left:' + r.left + 'px;top:' + r.top + 'px;width:' + r.width + 'px;height:' + r.height + 'px', document.documentElement);
    requestAnimationFrame(function () { flash.style.opacity = '0'; });
    setTimeout(function () { flash.remove(); }, 350);
    delete window.__ws;
  };
  ws.cancel = function () { finish({ mode: 'cancel' }); };

  function finish(result) {
    if (result.mode === 'rect' && !(result.rect && result.rect.width > 0 && result.rect.height > 0)) return;
    listeners.forEach(function (l) { window.removeEventListener(l[0], l[1], true); });
    root.remove();
    cover.remove();
    if (result.mode === 'rect') result.vis = prepareScroller();
    settle(function () { ws.result = result; });
  }
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
        _web_view_of(QApplication.widgetAt(QCursor.pos()))
        or _web_view_of(QApplication.focusWidget())
        or mw.web
    )


def _js(view, code, callback=None):
    """Run `code` in the view; False if the view no longer exists."""
    try:
        if callback is None:
            view.page().runJavaScript(code)
        else:
            view.page().runJavaScript(code, callback)
        return True
    except RuntimeError:
        return False


def _notify(view, msg, period=3000):
    try:
        parent = view.window()
    except RuntimeError:
        parent = None
    tooltip(msg, period=period, parent=parent)


def _out_path(view, ext):
    os.makedirs(OUT_DIR, exist_ok=True)
    # AnkiWebView shadows view.title() with a plain string; page().title() is safe.
    title = re.sub(r"[^\w.-]+", "_", view.page().title() or "webview").strip("_")[:60]
    return os.path.join(OUT_DIR, f"{time.strftime('%Y%m%d-%H%M%S')}-{title}.{ext}")


def _deliver(view, image):
    if image is None or image.isNull():
        _notify(view, "Screenshot failed: empty grab")
        return
    notes = []
    if COPY_CLIPBOARD:
        QApplication.clipboard().setImage(image)
        notes.append("copied to the clipboard")
    if SAVE_FILE:
        path = _out_path(view, "png")
        image.save(path, "PNG")
        notes.append(f"saved as {path}")
    _notify(view, "Screenshot " + " and ".join(notes))


def _grab_image(view):
    """The view's pixels, DPR 1 so every coordinate is a device pixel."""
    try:
        image = view.grab().toImage()
    except RuntimeError:
        return None
    image.setDevicePixelRatio(1.0)
    return image


class _Await:
    """Run `js` and poll window.__ws.result until the page sets it."""

    def __init__(self, view, js, on_done, timeout_s):
        self.view = view
        self.on_done = on_done
        self.deadline = time.monotonic() + timeout_s
        # Parented to mw, not the view: if Anki destroys the view meanwhile
        # the poll must keep running to notice.
        self.timer = QTimer(mw)
        self.timer.timeout.connect(self._poll)
        if _js(view, js):
            self.timer.start(POLL_MS)
        else:
            self._finish({"mode": "gone"})

    def stop(self):
        self.timer.stop()

    def _poll(self):
        if time.monotonic() > self.deadline:
            self._finish({"mode": "timeout"})
        elif not _js(self.view, "JSON.stringify(window.__ws ? window.__ws.result : {mode:'gone'})", self._got):
            self._finish({"mode": "gone"})

    def _got(self, raw):
        if not self.timer.isActive():
            return
        result = json.loads(raw) if raw else None
        if result:
            self._finish(result)

    def _finish(self, result):
        self.timer.stop()
        self.on_done(result)


class _Capture:
    """Photograph `rect` (CSS px, viewport coordinates).  Whatever lies outside
    the visible area is reached by scrolling the page and the grabs are
    stitched together."""

    running = set()  # keeps captures (and their pollers) alive until they finish

    def __init__(self, view, rect, vis):
        _Capture.running.add(self)
        self.view = view
        self.rect = rect
        self.left, self.top = rect["left"], rect["top"]
        self.width, self.height = rect["width"], rect["height"]
        self.vis = vis
        self.dx = self.dy = 0.0
        self.col = self.row = 0.0  # target-relative CSS px already covered
        self.band = 0.0  # bottom of the row of tiles being filled
        self.scale = 1.0
        self.image = None
        self.tiles = 0
        self.waiter = None
        self._next()

    def _next(self):
        vis = self.vis
        x = self.left - self.dx + self.col
        y = self.top - self.dy + self.row
        dx, dy = self.dx, self.dy
        # Scroll only when the rest of the target does not fit in view.
        if not (vis["left"] <= x and x + self.width - self.col <= vis["right"]):
            dx = self.left + self.col - vis["left"]
        if not (vis["top"] <= y and y + self.height - self.row <= vis["bottom"]):
            dy = self.top + self.row - vis["top"]
        self.waiter = _Await(self.view, f"window.__ws && window.__ws.scroll({dx:.2f}, {dy:.2f})", self._tile, STEP_TIMEOUT_S)

    def _tile(self, res):
        if "vis" not in res:
            return self._finish()
        self.dx, self.dy, self.vis = res["dx"], res["dy"], res["vis"]
        src = _grab_image(self.view)
        if src is None or src.isNull():
            return self._finish()
        if self.image is None:
            self.scale = self.view.devicePixelRatio() * self.view.zoomFactor()
            self.image = QImage(
                max(1, round(self.width * self.scale)),
                max(1, round(self.height * self.scale)),
                QImage.Format.Format_ARGB32_Premultiplied,
            )
            self.image.fill(Qt.GlobalColor.transparent)
        vis = self.vis
        tl, tt = self.left - self.dx, self.top - self.dy
        l, t = max(tl, vis["left"]), max(tt, vis["top"])
        r, b = min(tl + self.width, vis["right"]), min(tt + self.height, vis["bottom"])
        rl, rt, rr, rb = l - tl, t - tt, r - tl, b - tt
        if r - l >= 1 and b - t >= 1 and rr > self.col + 0.5 and rb > self.row + 0.5:
            s = self.scale
            source = QRect(round(l * s), round(t * s), round((r - l) * s), round((b - t) * s)).intersected(src.rect())
            painter = QPainter(self.image)
            painter.drawImage(QPoint(round(rl * s), round(rt * s)), src, source)
            painter.end()
            self.tiles += 1
            if self.col == 0:
                self.band = rb
            self.col = rr
            if self.col < self.width - 1:
                return self._next()
        # This row of tiles is complete, or the page cannot scroll any further.
        self.col = 0
        if self.band <= self.row + 0.5 or self.band >= self.height - 1:
            return self._finish()
        self.row = self.band
        self._next()

    def _finish(self):
        _Capture.running.discard(self)
        _js(self.view, f"window.__ws && window.__ws.done({json.dumps(self.rect)})")
        if self.tiles == 0:
            _notify(self.view, "Screenshot failed: nothing visible to grab")
            return
        _deliver(self.view, self.image)


class _Picker:
    active = None

    def __init__(self, view):
        self.view = view
        self.waiter = _Await(view, PICKER_JS, self._on_result, PICKER_TIMEOUT_S)
        _Picker.active = self

    def cancel(self):
        """Close the picker in the page.  False if the view no longer exists."""
        if _js(self.view, "window.__ws && window.__ws.cancel()"):
            return True
        self.waiter.stop()
        _Picker.active = None
        return False

    def _on_result(self, result):
        _Picker.active = None
        mode = result.get("mode")
        if mode == "rect":
            _Capture(self.view, result["rect"], result["vis"])
        elif mode == "visible":
            image = _grab_image(self.view)
            _js(self.view, "window.__ws && window.__ws.done()")
            _deliver(self.view, image)
        elif mode == "cancel":
            _js(self.view, "delete window.__ws")
            _notify(self.view, "Screenshot cancelled")
        elif mode == "timeout":
            _js(self.view, "window.__ws && window.__ws.cancel(); delete window.__ws")


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
        _notify(view, f"Saved {path}" if ok else f"PDF export failed: {path}")

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
