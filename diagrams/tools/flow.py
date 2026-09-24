# -*- coding: utf-8 -*-
"""Grid layout + orthogonal routing on top of archify.build, for the detail diagrams."""
import io, json, re, sys
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
from archify import build, cards_html, esc
import archify

W, H = 170, 80
CW, RH = 215, 150
MX, MY = 30, 60
R = 8
archify.NH = H


class Flow:
    def __init__(self):
        self.nodes, self.edges, self.frames, self.links = [], [], [], {}

    def node(self, nid, kind, col, row, label, sub, tag, ctx="", link=None, live=None):
        x, y = MX + col * CW, MY + row * RH
        self.nodes.append((nid, kind, x, y, W, label, sub, tag, ctx))
        if link or live:
            self.links[nid] = {"detail": link, "live": live}
        return self

    def box(self, nid):
        n = next(n for n in self.nodes if n[0] == nid)
        return n[2], n[3]

    def edge(self, a, b, label="", variant="default", route=None, via=None, lp=None, out=None, into=None):
        ax, ay = self.box(a); bx, by = self.box(b)
        acx, acy, bcx, bcy = ax + W / 2, ay + H / 2, bx + W / 2, by + H / 2
        if route is None:
            if ay == by:
                route = "h"
            elif ax == bx:
                route = "v"
            else:
                route = "vhv"
        if route == "h":
            pts = [(ax + W, acy), (bx, bcy)] if bx > ax else [(ax, acy), (bx + W, bcy)]
        elif route == "v":
            pts = [(acx, ay + H), (bcx, by)] if by > ay else [(acx, ay), (bcx, by + H)]
        elif route == "vhv":        # leave top/bottom, cross at a channel y, enter top/bottom
            down = by > ay
            sy = ay + H if down else ay
            ey = by if down else by + H
            if ay == by:            # same row: go round, above (via < top) or below
                sy = ey = ay if via is not None and via < ay else ay + H
            my = via if via is not None else (sy + ey) / 2
            ox = acx + (out or 0); ix = bcx + (into or 0)
            pts = [(ox, sy), (ox, my), (ix, my), (ix, ey)]
        elif route == "hv":         # leave left/right side, go to target column, enter top/bottom
            right = bx > ax
            sx = ax + W if right else ax
            sy = acy + (out or 0)
            ey = by if by > ay else by + H
            pts = [(sx, sy), (bcx + (into or 0), sy), (bcx + (into or 0), ey)]
        elif route == "vh":         # leave top/bottom, go to target row, enter left/right side
            down = by > ay
            sy = ay + H if down else ay
            ex = bx if bx > ax else bx + W
            pts = [(acx + (out or 0), sy), (acx + (out or 0), bcy + (into or 0)), (ex, bcy + (into or 0))]
        elif route == "hvh":        # leave side, cross at channel x, enter side
            right = bx > ax
            sx = ax + W if right else ax
            ex = bx if right else bx + W
            mx = via if via is not None else (sx + ex) / 2
            pts = [(sx, acy + (out or 0)), (mx, acy + (out or 0)), (mx, bcy + (into or 0)), (ex, bcy + (into or 0))]
        elif route == "right":      # leave the right side, run down/up the channel at x=via, enter the right side
            pts = [(ax + W, acy + (out or 0)), (via, acy + (out or 0)), (via, bcy + (into or 0)), (bx + W, bcy + (into or 0))]
        else:
            raise ValueError(route)
        pts = [(round(x, 1), round(y, 1)) for x, y in pts]
        d = self._path(pts)
        if lp is None and label:
            segs = list(zip(pts, pts[1:]))
            (x1, y1), (x2, y2) = max(segs, key=lambda s: abs(s[0][0] - s[1][0]) + abs(s[0][1] - s[1][1]))
            lp = ((x1 + x2) / 2, (y1 + y2) / 2 + 3)
        eid = f"{a}-{b}-{len(self.edges)}"
        self.edges.append((eid, a, b, label, variant, ";".join(f"{x:g},{y:g}" for x, y in pts), d, lp if label else None))
        return self

    @staticmethod
    def _path(pts):
        if len(pts) == 2:
            return f"M {pts[0][0]:g} {pts[0][1]:g} L {pts[1][0]:g} {pts[1][1]:g}"
        out = [f"M {pts[0][0]:g} {pts[0][1]:g}"]
        for i in range(1, len(pts) - 1):
            (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
            def toward(xa, ya, xb, yb, r):
                dx, dy = xb - xa, yb - ya
                L = max(abs(dx), abs(dy)) or 1
                r = min(r, L / 2)
                return xa + dx / L * r, ya + dy / L * r
            p1 = toward(x1, y1, x0, y0, R); p2 = toward(x1, y1, x2, y2, R)
            out.append(f"L {p1[0]:g} {p1[1]:g} Q {x1:g} {y1:g} {p2[0]:g} {p2[1]:g}")
        out.append(f"L {pts[-1][0]:g} {pts[-1][1]:g}")
        return " ".join(out)

    def frame(self, label, ids, kind="region", cls="c-subgroup", tcls="t-dim", pad=14, top=22):
        bs = [self.box(i) for i in ids]
        x0 = min(b[0] for b in bs) - pad; y0 = min(b[1] for b in bs) - top
        x1 = max(b[0] for b in bs) + W + pad; y1 = max(b[1] for b in bs) + H + pad
        self.frames.append((kind, len(self.frames), label, x0, y0, x1 - x0, y1 - y0, cls, tcls, 12))
        return self

    def viewbox(self):
        xs = [n[2] + W for n in self.nodes] + [f[3] + f[5] for f in self.frames]
        xs += [float(pt.split(",")[0]) + (60 if e[3] else 10) for e in self.edges for pt in e[5].split(";")]   # routes outside the boxes
        ys = [n[3] + H for n in self.nodes] + [f[4] + f[6] for f in self.frames]
        return (int(max(xs) + MX), int(max(ys) + 50))

    def svg(self, title):
        s = build(title, self.viewbox(), self.nodes, self.edges, self.frames)
        return add_link_chips(s, self.links)


def add_link_chips(svg, links):
    """Put a 'details ›' chip (opens the detail diagram) and/or an 'open ↗' chip (opens the live UI screen)
    in the top-right of each box that has one, and mark the box with data-node-detail."""
    for nid, lk in links.items():
        m = re.search(r'(<g id="node-%s" data-node-id="%s")' % (nid, nid), svg)
        if not m:
            continue
        rect = re.search(r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)" rx="6" class="c-mask"/>', svg[m.end():])
        x, y, w = float(rect.group(1)), float(rect.group(2)), float(rect.group(3))
        chips, cx = [], x + w - 6
        for key, text, cls in (("detail", "details ›", "chip-detail"), ("live", "open ↗", "chip-live")):
            href = lk.get(key)
            if not href:
                continue
            cw = 50 if key == "detail" else 44
            cx -= cw
            tgt = ' target="_blank" rel="noopener"' if key == "live" else ""
            chips.append(f'<a href="{esc(href)}"{tgt} class="node-chip {cls}" aria-label="{esc(text)}">'
                         f'<rect x="{cx:g}" y="{y + 5:g}" width="{cw}" height="14" rx="7"/>'
                         f'<text x="{cx + cw / 2:g}" y="{y + 15:g}" text-anchor="middle">{esc(text)}</text></a>')
            cx -= 4
        attrs = m.group(1)
        if lk.get("detail"):
            attrs += f' data-node-detail="{esc(lk["detail"])}"'
        head_end = svg.index(">", m.end()) + 1
        head = svg[m.start():head_end].replace(m.group(1), attrs, 1)
        # the box's own closing tag is on its own line with exactly 8 spaces; the icon group inside the box
        # closes with 10 spaces, and "        </g>" is a substring of that, so anchor on the newline.
        close = svg.index("\n        </g>", head_end) + 1
        svg = svg[:m.start()] + head + svg[head_end:close] + "          " + "".join(chips) + "\n" + svg[close:]
    return svg


LINK_CSS = """
    /* detail-diagram links */
    svg .node-chip rect { fill: var(--panel, #111827); stroke: var(--accent, #60a5fa); stroke-width: 1; }
    svg .node-chip text { font-size: 8.5px; font-weight: 700; fill: var(--accent, #60a5fa); font-family: ui-monospace, Menlo, monospace; }
    svg .node-chip.chip-live rect { stroke: #22c55e; } svg .node-chip.chip-live text { fill: #22c55e; }
    svg .node-chip:hover rect { fill: var(--accent, #60a5fa); } svg .node-chip:hover text { fill: #fff; }
    .back-link { font-size: 0.85rem; margin-right: 0.9rem; text-decoration: none; opacity: 0.8; }
    .back-link:hover { opacity: 1; text-decoration: underline; }
    .detail-hint { font-size: 0.8rem; opacity: 0.7; margin: -0.6rem 0 1rem; }
    svg [data-node-detail] { cursor: pointer; }
"""

LINK_JS = """
<script>
/* detail-diagram links: a click on a box that has its own diagram opens it.
   Runs in the capture phase, before the viewer's own click (which would only focus the box and move the camera).
   'open ↗' chips keep their own link; boxes without a detail diagram keep the viewer's normal click. */
(function () {
  var svg = document.querySelector('.diagram-container svg');
  if (!svg) return;
  var container = svg.closest('.diagram-container');
  function open(node) { window.location.href = node.getAttribute('data-node-detail'); }
  svg.addEventListener('click', function (e) {
    if (container && container.getAttribute('data-just-panned') === 'true') return;   // a drag, not a click
    if (e.target.closest('a.chip-live')) return;                                        // 'open ↗' keeps its own link
    var node = e.target.closest('[data-node-detail]');
    if (!node) return;
    e.stopPropagation(); e.preventDefault(); open(node);
  }, true);
  svg.addEventListener('keydown', function (e) {
    var node = e.target.closest('[data-node-detail]');
    if (node && e.key === 'Enter') { e.stopPropagation(); e.preventDefault(); open(node); }
  }, true);
})();
</script>
"""


def page(template_html, title, svg, views, cards, back=None, hint=None, back_label="← Overview"):
    s = template_html
    a = s.index('      <svg viewBox='); b = s.index('      </svg>', a) + len('      </svg>')
    s = s[:a] + svg + s[b:]
    old = re.search(r'<title>(.*?) Diagram</title>', s).group(1)
    s = s.replace(f'<title>{old} Diagram</title>', f'<title>{esc(title)} Diagram</title>', 1)
    h1 = re.search(r'<h1>.*?</h1>', s).group(0)
    back_html = f'<a class="back-link" href="{back}">{esc(back_label)}</a>' if back else ''
    s = s.replace(h1, f'{back_html}<h1>{esc(title)}</h1>', 1)
    if hint:
        s = s.replace('    <script id="archify-guided-views-data"', f'    <p class="detail-hint">{hint}</p>\n    <script id="archify-guided-views-data"', 1)
    s = re.sub(r'(<script id="archify-guided-views-data" type="application/json">).*?(</script>)',
               lambda m: m.group(1) + json.dumps(views, ensure_ascii=False, separators=(',', ':')) + m.group(2), s, count=1, flags=re.S)
    cs = s.index('    <!-- Info Cards -->'); ce = s.index('    </div>\n\n  </div>', cs) + len('    </div>')
    s = s[:cs] + cards_html(cards) + s[ce:]
    if '/* detail-diagram links */' not in s:
        s = s.replace('</style>', LINK_CSS + '</style>', 1)
        s = s.replace('</body>', LINK_JS + '</body>', 1)
    return s
