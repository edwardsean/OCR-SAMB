"""Is the text on this page running across (upright / upside down) or down (sideways)?

Measured on the sample: the old test (ink profile of the whole page) was fooled by table ruling lines — it turned
upright pages 71 and 197 sideways and left sideways pages 201 and 272 alone. This test removes ruling lines,
keeps only character-sized blobs, and smears them along each direction: characters on one text line merge into a
long thin bar. Upright pages scored <= 0.38, sideways pages >= 8.4 (down / across).
"""
import cv2
import numpy as np

SIDEWAYS = 2.0     # down/across above this = text runs down the page (big margin both sides on the sample)


def text_direction(a):
    s = cv2.resize(a, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    ink = ((s < 128) * 255).astype(np.uint8)
    h, w = ink.shape
    lines = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (w // 12, 1))) | \
        cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, h // 12)))
    txt = cv2.subtract(ink, cv2.dilate(lines, np.ones((3, 3), np.uint8)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(txt, 8)
    ok = (st[1:, cv2.CC_STAT_AREA] >= 6) & (st[1:, cv2.CC_STAT_WIDTH] <= 40) & (st[1:, cv2.CC_STAT_HEIGHT] <= 40)
    keep = np.zeros_like(txt)
    keep[np.isin(lab, np.where(ok)[0] + 1)] = 255

    def bars(kernel, along_x):
        m = cv2.morphologyEx(keep, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, kernel))
        _, _, st2, _ = cv2.connectedComponentsWithStats(m, 8)
        ww, hh = st2[1:, cv2.CC_STAT_WIDTH], st2[1:, cv2.CC_STAT_HEIGHT]
        if along_x:
            return int(ww[(ww >= 4 * hh) & (ww >= 40)].sum())
        return int(hh[(hh >= 4 * ww) & (hh >= 40)].sum())

    across, down = bars((11, 1), True), bars((1, 11), False)
    return across, down, round(down / max(across, 1), 2)
