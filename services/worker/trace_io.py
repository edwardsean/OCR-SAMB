"""What went into each stage of a page and what came out of it, for the trace (the user, 2026-10-08: "for a step that
has an input and output, can you put it there too? like … the text model output for mapping is the json fields").

Pure: each function turns a stage's own data into {"in": …, "out": …}, kept small (texts cut at PREVIEW characters,
values only for the fields that were found), so 90 days of trace stay a few GB at most. The whole page (the full
transcript, every Tesseract word) stays on the page and its technical detail; this is the record of each try."""
PREVIEW = 1500


def _cut(text, n=PREVIEW):
    text = text or ""
    return text if len(text) <= n else text[:n] + f"… ({len(text) - n:,} more characters)"


def _value(f):
    return f.get("value") if isinstance(f, dict) else f


def prepare(ticket, prep, flags, up_key):
    return {"in": {"image": ticket.get("image_key")},
            "out": {"upright image": up_key, "turned (degrees)": prep.get("rotation"),
                    "straightened (degrees)": round(prep["skew_angle"], 2) if prep.get("skew_angle") is not None else None,
                    "dark band (share of the page)": round(prep["dark_band_ratio"], 3)
                    if prep.get("dark_band_ratio") else None,
                    "QR code": prep.get("qr_text"), "looks like SAMB's Faktur (layout score)":
                    round(prep["layout_score"], 2) if prep.get("layout_score") is not None else None,
                    "quality": ", ".join(flags) if flags else None}}


def read(blocks, mapping, fields_all, notes, schema_size):
    """The AI OCR's copy (vision model) and the text model's mapping onto the combined field list, twice; a field the
    two mappings disagree on is left empty."""
    blocks = blocks or []
    text = "\n".join(f"[{b.get('id')}] {b.get('text')}" for b in blocks if b.get("text"))
    mapping = mapping or {}
    raw, raw2 = (mapping.get("raw") or {}).get("fields") or {}, (mapping.get("raw2") or {}).get("fields") or {}
    disagreed = sorted(k for k in set(raw) | set(raw2) if mapping.get("raw2") is not None
                       and (raw.get(k) or {}).get("value") != (raw2.get(k) or {}).get("value"))
    found = {k: _value(v) for k, v in (fields_all or {}).items() if k != "lines" and _value(v) not in (None, "")}
    lines = (fields_all or {}).get("lines") or []
    return {"in": {"page image": "the upright image (vision model)", "field list": f"{schema_size} fields"},
            "out": {"AI OCR copy (vision model)": f"{len(blocks)} blocks\n{_cut(text)}",
                    "mapping onto the field list (text model)": found or "nothing found",
                    "table rows mapped": len(lines) or None,
                    "left empty: the two mappings disagreed": ", ".join(disagreed) or None,
                    "notes (handwriting, marks)": [f"{x.get('kind')}: {x.get('text')} ({x.get('about')})"
                                                   for x in notes or []][:8] or None}}


def classify(state, jev, machine, label, qr_sor, layout, title):
    """What the classification model saw (the fields found, never the image) and what it and the rules decided."""
    state = state or {}
    probs = {k: round(v, 3) for k, v in sorted((jev.get("probabilities") or {}).items(), key=lambda kv: -kv[1]) if v}
    return {"in": {"fields found": state.get("found") or None, "title printed": state.get("document_title"),
                   "table rows": state.get("line_rows"), "first rows": state.get("first_rows") or None},
            "out": {"model's answer": jev.get("choice"), "confidence": jev.get("confidence"),
                    "probabilities": probs or None, "error": jev.get("error") or jev.get("skipped"),
                    "QR code is an SOR": bool(qr_sor) or None, "FP layout score": round(layout, 2) if layout else None,
                    "printed FAKTUR PENJUALAN title": title,
                    "decision": f"{machine.get('status')}: {machine.get('doc_type') or machine.get('guess') or '—'}"
                                f" ({machine.get('reason')})",
                    "a person said": label}}


def knowledge(info, before, after):
    """Pass B: the text model again, with the tips learned for this customer and type; only the fields they name."""
    if not info:
        return {"out": {"tips": "none for this customer and type: nothing done"}}
    changed = {k: f"{_value(before.get(k))} → {_value(v)}" for k, v in (after or {}).items()
               if k != "lines" and _value(v) != _value((before or {}).get(k))}
    return {"in": {"customer": info.get("chain"), "found by": info.get("chain_by"), "tips version": info.get("version")},
            "out": {"changed": changed or "nothing changed", "values the tips flipped": info.get("flips") or None}}


def project(doc_type, fields):
    """Code, not AI: the combined field list onto this document type's own fields."""
    return {"in": {"document type": doc_type},
            "out": {f"{doc_type}'s fields": {k: _value(v) for k, v in (fields or {}).items()
                                             if k != "lines" and _value(v) not in (None, "")} or "none found",
                    "table rows": len((fields or {}).get("lines") or []) or None}}


def tesseract(rd):
    words = rd.get("ocr_words") or []
    return {"in": {"image": "the upright page, dark bands masked"},
            "out": {"variant used": rd.get("ocr_variant"), "confidence": rd.get("ocr_conf"),
                    "characters read confidently": rd.get("confident_chars"), "words": len(words) or None,
                    "quality": ", ".join(rd.get("quality_flags") or []) or None,
                    "text": _cut(rd.get("classical_text"))}}


def check(res):
    """Each value's verdict: ✓ and what backs it, or ⚠ and why not."""
    head = (res or {}).get("header") or {}
    out = {k: (f"✓ {v.get('by')}" if v.get("verdict") == "ok" else f"{'⚠' if v.get('verdict') == 'check' else '·'} "
               f"{v.get('why') or v.get('verdict')}") for k, v in head.items()}
    lines = (res or {}).get("lines") or []
    ok = sum(1 for r in lines for c in (r.values() if isinstance(r, dict) else []) if isinstance(c, dict)
             and c.get("verdict") == "ok")
    return {"out": {"values": out or None, "table cells backed": ok if lines else None,
                    "summary": (res or {}).get("summary")}}


def look(sl):
    """The look-again (vision model, blind): the fields asked, and each first and second answer."""
    sl = sl or {}
    res = sl.get("results") or {}
    if not sl.get("asked") and not res:
        return {"out": {"asked": sl.get("waiting") or "nothing: no value that decides (its linking number, a Faktur's "
                                                        "amounts) is unsure; the others are kept as read"}}
    return {"in": {"asked": ", ".join(sl.get("asked") or sorted(res))},
            "out": {k: f"{_value(r.get('first'))} → {_value(r.get('second'))}"
                       + (" (kept)" if r.get("kept_second") else " (not kept)") for k, r in res.items()} or None}


def save(oc, keys, cls):
    return {"out": {"outcome": oc, "type": cls.get("doc_type"), "how the type was decided": cls.get("type_status"),
                    "linking numbers": {k: (v.get("value") if isinstance(v, dict) else v)
                                        for k, v in (keys or {}).items() if v} or None}}

