"""What a person can click on the page viewer: a box on each printed word, number and table cell, with the AI OCR's
characters (read-then-map, Stage 2a). Pure: no image here; the worker's image steps are in worker/boxes.py.

  POSITIONS come from Tesseract's words: it knows exactly where each word it read is printed. TEXT comes from the AI
  OCR's copy, which reads characters better. A copied token and a Tesseract word are paired only when their characters
  agree, allowing the usual misreads (O/0, I/1, S/5, B/8, G/6, Z/2): p1's "Dasar" has a box because Tesseract read
  "Casar" there, and the copy's 584.144,00 never lands on it (the old rule paired whatever was left over in a line,
  one for one, and that put 584.144,00 on "Dasar", "NO" on "ITEM", DISCOUNT% on COST).

  Within a copied line the pairing keeps the order (dynamic programming, weighted by the characters that agree), so
  a repeated token ("20" as the pack size and in "CARTON 20") goes to the right word. Tesseract often runs words
  together ("2TELOR", "OTYiCRT/", "15:16:50"): a word holding several tokens is shared out by where each token's
  characters sit in it, only when every token is found in it on its own. A token Tesseract split ("98," "640,00")
  takes both words.

  A copied line's search window is its own box (the AI's boxes can be half a row off) widened by 0.6 of its height,
  so a table row sees its neighbours' words too: on each printed line, the copied line that pairs best with it keeps
  its pairings, and the others lose theirs there (p3 row 1's 02701899 must never land on row 2's 02701936).

  No box is better than a box in the wrong place: a token that pairs with nothing gets none; a token of one or two
  characters needs another pairing of its line on the same printed line (a "5" in noise took p3 row 5's number); a
  word the copy doesn't have gets none (Tesseract is often sure and wrong on these scans: "$40.0" on 99,640.00); a
  Tesseract "word" far wider or flatter than the page's words is not one word ("30000" over three columns, "000"
  read from a dotted rule); stamps and marks get none (the copy describes them).

  Boxes are [ymin, xmin, ymax, xmax] on 0-1000 of the upright page; Tesseract's words are [text, conf, x, y, w, h]
  in pixels of the upright image.
"""
import difflib

from common import transcript
from common.verify import flat

SIMILAR = 0.6         # a token and a word are the same printed word at this similarity (5+ characters)
SIMILAR_SHORT = 0.75  # ... 3-4 characters: "oad" is not "2.00" because two zeros agree
STRONG = 0.8          # a token out of order (a name wrapped onto the next line) pairs alone at this, unrivalled
MAX_PARTS = 4         # tokens one Tesseract word can hold ("15:16:50")
WINDOW = 0.6          # a copied line looks for its words this share of its height above and below its box
WIDE = 2.5            # a Tesseract word this many times wider per character than the page's words is several words
DESCRIBED = ("stamp", "mark")   # the copy describes these ("illegible circular stamp", "circle around 320"): no boxes
_CANON = str.maketrans("OQDILSZBG", "000115286")


def canon(s):
    """Letters and digits, uppercase, with the characters Tesseract confuses made one."""
    return flat(s).translate(_CANON)


def sim(a, b):
    """How alike a token and a word are (0-1): equal after canon(); otherwise, for 3+ characters of about the same
    length, the share of characters that agree in order. One or two characters must be equal."""
    ca, cb = canon(a), canon(b)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0
    if min(len(ca), len(cb)) <= 2 or not 0.5 <= len(ca) / len(cb) <= 2:
        return 0.0
    r = difflib.SequenceMatcher(None, ca, cb, autojunk=False).ratio()
    return r if r >= (SIMILAR_SHORT if max(len(ca), len(cb)) <= 4 else SIMILAR) else 0.0


def line_text(b):
    """The copied line as it is tokenised: a table row's number (its text, when short) and then its cells; its text when
    no cell holds anything."""
    cells = b.get("cells")
    if not cells or not any(str(c).strip() for c in cells):
        return b.get("text") or ""
    row = " ".join(str(c) for c in cells)
    head = (b.get("text") or "").strip()
    first = next((str(c).strip() for c in cells if str(c).strip()), "")
    return f"{head} {row}" if head and len(flat(head)) <= 3 and flat(head) != flat(first) else row


def tokens(b):
    """The copied line's tokens: [(token, start, end)] with their places in line_text(b)."""
    return [(m.group(0), m.start(), m.end()) for m in transcript.TOKEN.finditer(line_text(b))]


def _shared(a, b):
    """The share of a's characters found, in order, in b."""
    return sum(m.size for m in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()) / len(a) \
        if a else 0.0


def parts(toks, word):
    """Several tokens printed as one Tesseract word: each token's (start, end) share of the word's width, and the
    similarity; None unless every token is found in the word on its own (SIMILAR of its characters, in order) and
    the tokens make up most of the word."""
    raw = [k for k, ch in enumerate(word) if ch.isalnum()]
    cw, ct = canon(word), [canon(t) for t in toks]
    if not cw or len(cw) != len(raw) or not all(ct):
        return None
    whole = "".join(ct)
    hit = {}                                          # a character of the tokens -> its place in the word
    for m in difflib.SequenceMatcher(None, whole, cw, autojunk=False).get_matching_blocks():
        hit.update({m.a + d: m.b + d for d in range(m.size)})
    spans, at = [], 0
    for c in ct:
        got = [hit[k] for k in range(at, at + len(c)) if k in hit]
        if len(got) < max(1, SIMILAR * len(c)):
            return None
        spans.append((min(got), max(got)))
        at += len(c)
    if len(hit) < SIMILAR * len(cw) or any(spans[k][1] >= spans[k + 1][0] for k in range(len(spans) - 1)):
        return None
    n = len(word)
    cuts = []
    for k, (a, z) in enumerate(spans):                # from a token's first character to its last, gaps shared
        lo = 0 if k == 0 else (raw[spans[k - 1][1]] + 1 + raw[a]) / 2
        hi = n if k == len(spans) - 1 else (raw[z] + 1 + raw[spans[k + 1][0]]) / 2
        cuts.append((lo / n, hi / n))
    return cuts, 2 * len(hit) / (len(whole) + len(cw))


def _words(words):
    """Tesseract's words as dicts, each with the printed line it sits on (numbered top to bottom)."""
    ws = [dict(t=w[0], c=w[1], x0=w[2], y0=w[3], x1=w[2] + w[4], y1=w[3] + w[5], k=k)
          for k, w in enumerate(words or []) if len(w) >= 6 and flat(w[0]) and w[4] > 0 and w[5] > 0]
    per = sorted((w["x1"] - w["x0"]) / len(flat(w["t"])) for w in ws if len(flat(w["t"])) >= 3)
    tall = sorted(w["y1"] - w["y0"] for w in ws if len(flat(w["t"])) >= 3)
    if per:                                           # p5: "30000" was three columns, 30.00 30.00 0.00, as one word;
        ws = [w for w in ws if (w["x1"] - w["x0"]) / len(flat(w["t"])) <= WIDE * per[len(per) // 2]   # "000" was
              and w["y1"] - w["y0"] >= 0.5 * tall[len(tall) // 2]]                                    # a dotted rule
    if not ws:
        return ws
    hs = sorted(w["y1"] - w["y0"] for w in ws)
    med, lines = hs[len(hs) // 2] or 20, []
    for w in sorted(ws, key=lambda w: (w["y0"] + w["y1"]) / 2):
        cy = (w["y0"] + w["y1"]) / 2
        if lines and abs(lines[-1][0] - cy) <= med * 0.6:
            lines[-1][1].append(w)
            lines[-1][0] = sum((x["y0"] + x["y1"]) / 2 for x in lines[-1][1]) / len(lines[-1][1])
        else:
            lines.append([cy, [w]])
    for n, (_, line) in enumerate(lines):
        for w in line:
            w["line"] = n
    return ws


def _adjacent(a, b):
    """Two words side by side on one line, at most about one character apart."""
    h = max(a["y1"] - a["y0"], b["y1"] - b["y0"], 1)
    return a["line"] == b["line"] and 0 <= b["x0"] - a["x1"] <= 1.5 * h


def align(toks, ws, floor=0.0):
    """The order-keeping pairing of tokens with words that agrees on the most characters: [(token indices, word
    indices, similarity)], one token to one word, several tokens to one word, or one token over two neighbours.
    Pairings less alike than `floor` are left out."""
    n, m = len(toks), len(ws)
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        for j in range(m + 1):
            if not i and not j:
                continue
            best, how = float("-inf"), None
            if i and dp[i - 1][j] > best:
                best, how = dp[i - 1][j], None
            if j and dp[i][j - 1] > best:
                best, how = dp[i][j - 1], "w"
            if i and j:
                s = sim(toks[i - 1], ws[j - 1]["t"])
                if s >= max(floor, 1e-9) and dp[i - 1][j - 1] + s * len(canon(toks[i - 1])) > best:
                    best, how = dp[i - 1][j - 1] + s * len(canon(toks[i - 1])), (1, 1, s)
                for a in range(2, min(MAX_PARTS, i) + 1):
                    sp = parts(toks[i - a:i], ws[j - 1]["t"])
                    if sp and sp[1] >= floor and dp[i - a][j - 1] + sp[1] * len(canon("".join(toks[i - a:i]))) > best:
                        best, how = dp[i - a][j - 1] + sp[1] * len(canon("".join(toks[i - a:i]))), (a, 1, sp[1])
            if i and j >= 2 and _adjacent(ws[j - 2], ws[j - 1]):
                s = sim(toks[i - 1], ws[j - 2]["t"] + ws[j - 1]["t"])
                both = all(_shared(canon(ws[j - q]["t"]), canon(toks[i - 1])) >= 0.5 for q in (1, 2))
                if s >= max(floor, 1e-9) and both and dp[i - 1][j - 2] + s * len(canon(toks[i - 1])) > best:
                    best, how = dp[i - 1][j - 2] + s * len(canon(toks[i - 1])), (1, 2, s)
            dp[i][j], back[i][j] = best, how
    out, i, j = [], n, m
    while i or j:
        how = back[i][j]
        if how is None:
            i -= 1
        elif how == "w":
            j -= 1
        else:
            a, z, s = how
            out.append((list(range(i - a, i)), list(range(j - z, j)), s))
            i, j = i - a, j - z
    return out[::-1]


def _pairs(b, ws, W, H):
    """One copied line's pairings with the words in its window: [(block, token indices, words, similarity)]."""
    box, toks = b["box"], [t for t, _, _ in tokens(b)]
    pad = max(WINDOW * (box[2] - box[0]), 10)
    y0, y1 = (box[0] - pad) * H / 1000, (box[2] + pad) * H / 1000
    x0, x1 = (box[1] - 20) * W / 1000, (box[3] + 20) * W / 1000
    mine = sorted((w for w in ws if y0 <= (w["y0"] + w["y1"]) / 2 <= y1 and x0 <= (w["x0"] + w["x1"]) / 2 <= x1),
                  key=lambda w: (w["line"], w["x0"]))
    floor = STRONG if b.get("kind") == "handwriting" else 0.0   # Tesseract reads handwriting as near-nonsense
    out = [(b, ti, [mine[j] for j in wj], s) for ti, wj, s in align(toks, mine, floor)]
    used_t = {i for _, ti, _, _ in out for i in ti}
    used_w = {w["k"] for _, _, wl, _ in out for w in wl}
    for i, t in enumerate(toks):                      # wrapped onto the next line: out of order, strong and unrivalled
        if i in used_t or len(canon(t)) < 3:
            continue
        good = [(sim(t, w["t"]), w) for w in mine if w["k"] not in used_w and sim(t, w["t"])]
        if len(good) == 1 and good[0][0] >= STRONG and \
                not any(u != i and u not in used_t and sim(tt, good[0][1]["t"]) for u, tt in enumerate(toks)):
            out.append((b, [i], [good[0][1]], good[0][0]))
            used_t.add(i)
            used_w.add(good[0][1]["k"])
    return [f for f in out if _supported(f, out, W, H)]


def _supported(f, out, W, H):
    """A token of one or two characters ("5", "09", "0") is everywhere on a page: it keeps its word only beside
    another pairing of its line on the same printed line, or, alone in its copied line, inside that line's own box
    (p3: row 5's number "5" took a "5" Tesseract saw in the black band's noise, inside row 5's window)."""
    b, ti, wl, _ = f
    if len(ti) > 1 or len(canon(tokens(b)[ti[0]][0])) > 2:
        return True
    if any(g is not f and any(w["line"] == wl[0]["line"] for w in g[2]) for g in out):
        return True
    box, w = b["box"], wl[0]
    h, cy, cx = box[2] - box[0], (w["y0"] + w["y1"]) / 2 * 1000 / H, (w["x0"] + w["x1"]) / 2 * 1000 / W
    return len(tokens(b)) == 1 and box[0] - 0.25 * h <= cy <= box[2] + 0.25 * h and box[1] - 5 <= cx <= box[3] + 5


def _weight(f):
    b, ti, _, s = f
    toks = tokens(b)
    return s * sum(len(canon(toks[i][0])) for i in ti)


def _overlap_x(a, b):
    """Two copied lines' boxes share at least half the narrower one's width."""
    lo, hi = max(a[1], b[1]), min(a[3], b[3])
    return hi - lo >= 0.5 * min(a[3] - a[1], b[3] - b[1])


def _settle(found, H):
    """On each printed line, the copied line that pairs best with it keeps its pairings there (lines side by side,
    like a header's fields, don't compete); then a word two copied lines took stays with the better pairing."""
    got = {}
    for f in found:
        for w in f[2]:
            got[(w["line"], f[0]["id"])] = got.get((w["line"], f[0]["id"]), 0) + _weight(f) / len(f[2])
    boxes = {f[0]["id"]: f[0]["box"] for f in found}
    keep = [f for f in found if not any(
        got.get((w["line"], other), 0) > got[(w["line"], f[0]["id"])] and _overlap_x(boxes[other], f[0]["box"])
        for w in f[2] for other in boxes if other != f[0]["id"])]
    best = {}
    for n, f in enumerate(keep):
        b, _, wl, _ = f
        cy = sum((w["y0"] + w["y1"]) / 2 for w in wl) / len(wl) * 1000 / H
        sc = _weight(f) - abs(cy - (b["box"][0] + b["box"][2]) / 2) * 1e-4     # a tie: the nearer copied line
        for w in wl:
            if w["k"] not in best or sc > best[w["k"]][0]:
                best[w["k"]] = (sc, n)
    return [f for n, f in enumerate(keep) if all(best[w["k"]][1] == n for w in f[2])]


def _unit(tok, bid, i, tess, how, box):
    t, s, e = tok
    return {"block": bid, "i": i, "s": s, "e": e, "text": t, "tess": tess, "match": how, "box": box}


def _candidates(f, W, H):
    """A pairing's boxes: [((block, token), (score, box, Tesseract's text, match))]. Several tokens printed as one
    word share it out by where their characters sit in it."""
    b, ti, wl, s = f
    toks, tess = tokens(b), " ".join(w["t"] for w in wl)
    if len(ti) == 1:
        t = toks[ti[0]][0]
        a, z = _inked(wl[0]), _inked(wl[-1])
        return [((b["id"], ti[0]), (s * len(canon(t)), _box(min(w["y0"] for w in wl), wl[0]["x0"] + a[0],
                                                            max(w["y1"] for w in wl), wl[-1]["x0"] + z[1], W, H),
                                    tess, "equal" if flat(t) == flat(tess) else "misread"))]
    w = wl[0]
    cuts, _ = parts([toks[i][0] for i in ti], w["t"])
    return [((b["id"], i), (s * len(canon(toks[i][0])), _box(w["y0"], w["x0"] + (w["x1"] - w["x0"]) * f0, w["y1"],
                                                             w["x0"] + (w["x1"] - w["x0"]) * f1, W, H), tess, "split"))
            for i, (f0, f1) in zip(ti, cuts)]


def _inked(w):
    """The stretch of a word's box (pixels from its left edge) from its first letter or digit to its last, by their
    share of its characters: Tesseract's "CARTON}?" is CARTON and a misread 12, and the box must not take the 12."""
    t, width = w["t"], w["x1"] - w["x0"]
    k = [i for i, ch in enumerate(t) if ch.isalnum()]
    return (width * k[0] / len(t), width * (k[-1] + 1) / len(t)) if k else (0, width)


def place(b, run, words, size, spots):
    """Tokens `run` of copied line b paired only with `words` (the worker's zoomed reading of the stretch between
    their boxed neighbours: it holds nothing else of the line). New units on free spots; spots grows."""
    W, H = size
    ws = sorted(_words(words), key=lambda w: w["x0"])
    toks = tokens(b)
    out = []
    for ti, wj, s in align([toks[i][0] for i in run], ws, STRONG if b.get("kind") == "handwriting" else 0.0):
        f = (b, [run[i] for i in ti], [ws[j] for j in wj], s)
        for (bid, i), (_, box, tess, how) in _candidates(f, W, H):
            if not any(iou(box, x) > 0.3 or overlaps(box, x) for x in spots):
                spots.append(box)
                out.append(_unit(toks[i], bid, i, tess, how, box))
    return out


def _box(y0, x0, y1, x1, W, H):
    return [int(y0 * 1000 / H), int(x0 * 1000 / W), int(y1 * 1000 / H), int(x1 * 1000 / W)]


def iou(a, b):
    y0, x0, y1, x1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    if y1 <= y0 or x1 <= x0:
        return 0.0
    i = (y1 - y0) * (x1 - x0)
    return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)


def overlaps(a, b):
    """Either box's centre lies inside the other."""
    def inside(p, q):
        cy, cx = (p[0] + p[2]) / 2, (p[1] + p[3]) / 2
        return q[0] <= cy <= q[2] and q[1] <= cx <= q[3]
    return inside(a, b) or inside(b, a)


def match(blocks, reads, size):
    """The clickable units: reads = one or more lists of Tesseract's words (the page's own reading first, then any
    closer ones the worker made). A token takes its best-paired read's box, unless another read pairs it about as well
    somewhere else (two readers disagree about where it is: no box). A word the copy doesn't have gets no box: on
    these scans Tesseract is often sure and wrong ("$40.0" on 99,640.00), and a box must give what is printed.
    [{id, block, i, s, e, text, tess, match: equal | misread | split, box}]"""
    W, H = size or (0, 0)
    if not W or not H:
        return []
    blocks = [b for b in blocks or [] if b.get("box") and tokens(b) and b.get("kind") not in DESCRIBED]
    toks = {b["id"]: tokens(b) for b in blocks}
    cands = {}
    for words in reads:
        ws = _words(words)
        for f in _settle([f for b in blocks for f in _pairs(b, ws, W, H)], H):
            for key, c in _candidates(f, W, H):
                cands.setdefault(key, []).append(c)
    units, spots = [], []
    for (bid, i), cs in sorted(cands.items(), key=lambda kv: -max(c[0] for c in kv[1])):
        cs.sort(key=lambda c: -c[0])
        score, box, tess, how = cs[0]
        if any(not overlaps(c[1], box) and c[0] >= 0.8 * score for c in cs[1:]):
            continue                                  # two readings put it in different places
        if any(iou(box, x) > 0.3 for x in spots):
            continue                                  # another token has this spot
        spots.append(box)
        units.append(_unit(toks[bid][i], bid, i, tess, how, box))
    for n, u in enumerate(units):
        u["id"] = f"w{n}"
    return units
