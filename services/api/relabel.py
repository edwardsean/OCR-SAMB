"""Changing a page's type (the user, 2026-10-08: "a mechanism that a user can reclassify a document that is classified
by the classifier … then the teacher analyzes why and changes the classifier's context").

A person's type for a page is a label (staging.type_label), the same as an answer on the Label screen:
  - it decides the page's type from then on (vf.handle), and the page is processed again as that type. Nothing is read
    again: the AI's copy and its mapping onto the combined list are kept; the type's fields, the checks and the
    grouping change;
  - when the classifier answered otherwise, a practice-pile label is a lesson (app.vf_after_label → staging.lesson →
    rtm-teacher). The teacher sees the page, the label and what the classifier read, says why it was missed, and
    tries one change to the classifier's context, which is replayed on every labelled page. A change that passes is
    used at once, with no person approving it (the user, 2026-10-08: "if it passes, then the context is changed");
    a person can take the context back on Teknis → Konteks klasifikasi;
  - one label in five, drawn once, is in the exam pile: it grades the teacher's changes and is never taught.

A page's type can't be changed
  - when its order is already sent to Satellite (the same rule as Coba lagi: taking an order back is a developer's
    `python -m publisher.publish --undo <SOR>`);
  - while the page is queued: its worker would save the old type after the label (vf.handle finds the page read and
    drops the label's ticket)."""
from api import stuck

PILE_EXAM = ("Jawaban ini masuk tumpukan ujian (1 dari 5 jawaban, diundi): dipakai untuk menilai pelajaran guru AI, "
             "tidak diajarkan kepadanya.")
WHERE = "Teknis → Konteks klasifikasi"


def refusal(status, published):
    """Pure: why a person can't change this page's type now, or None."""
    if published:
        return "Ordernya sudah dikirim ke Satellite: jenis halaman ini tidak bisa diubah lagi."
    if status == "queued":
        return "Halaman ini sedang diproses AI. Ubah jenisnya setelah selesai."
    return None


def load(c, batch, page):
    """What progress() needs, from the database."""
    p = c.execute(f"""SELECT p.status::text AS status, p.doc_type::text AS doc_type, p.type_votes->'machine' AS machine,
                             {stuck.PUBLISHED} AS published
                        FROM staging.page p WHERE p.batch_id=%s AND p.page_no=%s""", (batch, page)).fetchone()
    label = c.execute("""SELECT label::text AS label, labelled_by, labelled_at, pile, note FROM staging.type_label
                          WHERE batch_id=%s AND page_no=%s""", (batch, page)).fetchone()
    lesson = c.execute("SELECT * FROM staging.lesson WHERE batch_id=%s AND page_no=%s", (batch, page)).fetchone()
    proposal = pending = None
    ahead = 0
    if lesson and lesson["proposal"]:
        proposal = c.execute("SELECT version, status, approved_by FROM staging.context_version WHERE version=%s",
                             (lesson["proposal"],)).fetchone()
    if lesson and lesson["status"] == "waiting":
        r = c.execute("SELECT version FROM staging.context_version WHERE status='proposed'").fetchone()
        pending = r and r["version"]
        ahead = c.execute("SELECT count(*) AS n FROM staging.lesson WHERE status='waiting' AND created_at < %s",
                          (lesson["created_at"],)).fetchone()["n"]
    return {"page": p, "label": label, "lesson": lesson, "proposal": proposal, "pending": pending, "ahead": ahead}


def change_text(change, names):
    """The teacher's one change to the context, in a sentence."""
    if not change or change.get("kind") in (None, "none"):
        return None
    t = names.get(change.get("type"), change.get("type"))
    note = f" ({change['note']})" if change.get("note") else ""
    if change["kind"] == "field_for_type":
        return f"{t} juga bisa mencetak {change.get('field')}{note}."
    if change["kind"] == "edit_type":
        titles = f" Judul baru: {', '.join(change['titles_add'])}." if change.get("titles_add") else ""
        return f"Deskripsi {t}: {(change.get('what') or '(tetap)').rstrip('.')}.{titles}"
    if change["kind"] == "new_field":
        f = change.get("field") or {}
        return f"Isian baru untuk {t}: {f.get('name')}, {f.get('meaning')}{note}."
    return None


def progress(d, names):
    """Pure: a type label's path, for the status bar after a person changes a page's type: {headline, steps, why, tip,
    detail, final}. why = the teacher's reading of the miss; tip = its change, once one passed the replay (and so is in
    use); steps = [(label, state)], state done | now | todo | stop; final = nothing more happens by itself (the screen
    stops asking). None when the page has no label."""
    label, page, lesson = d["label"], d["page"] or {}, d["lesson"]
    if not label:
        return None
    t = names.get(label["label"], label["label"])
    m = page.get("machine") or {}
    redone = page.get("status") == "read" and page.get("doc_type") == label["label"]
    saved = (f"Jenis disimpan: {t}" + (f", oleh {label['labelled_by']}" if label.get("labelled_by") else ""), "done")
    again = (f"Halaman diproses ulang sebagai {t}", "done" if redone else "now")
    out = {"why": None, "tip": None, "detail": None}
    if m.get("status") == "decided" and m.get("doc_type") == label["label"]:
        return {**out, "headline": "Jenis disimpan: sama dengan jawaban sistem, jadi tidak ada yang perlu dipelajari.",
                "steps": [], "final": True}
    if label["pile"] == "exam":
        return {**out, "headline": f"Jenis diubah menjadi {t}. {PILE_EXAM}", "steps": [saved, again], "final": redone}
    if not lesson:
        return {**out, "headline": f"Jenis diubah menjadi {t}.", "steps": [saved, again], "final": redone}

    a = lesson.get("answer") or {}
    tries = a.get("tries") or []
    out["why"] = a.get("why_missed") or (tries[-1].get("why_missed") if tries else None)
    teach = "Guru AI mencari tahu kenapa sistem salah, lalu menguji perbaikannya pada halaman yang sudah dijawab orang"
    use = "Konteks klasifikasi diubah: halaman baru dikenali dengan perbaikan itu"
    st = lesson["status"]
    if st == "waiting":
        if d.get("pending"):                 # a proposal from before changes were used at once (2026-10-08)
            head = (f"Menunggu: usulan lama konteks #{d['pending']} harus disetujui atau ditolak dulu di {WHERE}. "
                    "Guru AI mengubah satu hal pada satu waktu.")
        elif (lesson.get("error") or "").startswith("will retry"):
            head = "Model guru AI belum bisa dihubungi; dicoba lagi otomatis dalam setengah jam."
            out["detail"] = lesson["error"]
        else:
            head = "Guru AI sedang mempelajari kesalahan ini." + (f" Ada {d['ahead']} pelajaran sebelum ini."
                                                                 if d.get("ahead") else "")
        return {**out, "headline": head, "final": bool(d.get("pending")),
                "steps": [saved, again, (teach, "todo" if d.get("pending") else "now"), (use, "todo")]}
    if st == "proposed":
        v = d.get("proposal") or {}
        n = v.get("version", lesson.get("proposal"))
        out["tip"] = change_text(a.get("change"), names)
        if v.get("status") == "proposed":     # from before 2026-10-08: it waits for a person
            return {**out, "final": True, "headline": f"Perubahan (konteks #{n}) lolos uji ulang dan menunggu "
                                                      f"persetujuan di {WHERE}.",
                    "steps": [saved, again, (teach, "done"), (use, "todo")]}
        if v.get("status") == "rejected":
            return {**out, "final": True, "headline": f"Perubahan konteks #{n} ditolak"
                                                      + (f" oleh {v['approved_by']}." if v.get("approved_by") else "."),
                    "steps": [saved, again, (teach, "done"), (use, "stop")]}
        later = ("" if v.get("status") == "active" else
                 " Sejak itu konteksnya sudah diganti lagi (perubahan berikutnya, atau dikembalikan seseorang).")
        return {**out, "final": True, "steps": [saved, again, (teach, "done"), (use, "done")],
                "headline": f"Perbaikannya lolos uji ulang, jadi konteks klasifikasi langsung diubah (konteks #{n}): "
                            f"halaman baru dikenali dengannya.{later}"}
    if st == "no_change":
        head = ("Konteks terbaru sudah menjawab halaman ini dengan benar: tidak ada yang perlu diubah."
                if a.get("note") else "Guru AI tidak menemukan perubahan konteks yang membantu.")
        return {**out, "final": True, "headline": head, "steps": [saved, again, (teach, "done")]}
    return {**out, "final": True, "detail": lesson.get("error"),
            "headline": ("Guru AI belum menemukan perubahan yang lolos uji ulang, jadi konteksnya tidak diubah. "
                         "Jenis yang Anda pilih tetap dipakai untuk halaman ini."),
            "steps": [saved, again, (teach, "stop")]}
