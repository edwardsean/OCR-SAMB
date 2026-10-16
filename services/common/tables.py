"""Every table of the pipeline's database (PIPELINE_DB, schemas satellite and staging): one TypedDict per table,
a row as `SELECT *` gives it (psycopg's dict_row). A query that selects fewer columns gives fewer keys.

GENERATED from the database by `python -m common.tablegen write`. Do not edit by hand: change the schema
(schema/0NN-*.sql), apply it, and generate again. tests/test_tables.py fails while this file and the database
differ. A column's comment in the database (COMMENT ON COLUMN) is the comment above it.

  TABLES["satellite.sor"] is Sor, NAME[Sor] is "satellite.sor", KEYS["satellite.sor"] its primary key,
  columns(Sor) its columns in order, select(Sor) them as a SELECT list.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, TypedDict, TypeVar

BundleStatus = Literal['grouping', 'auto_ok', 'needs_review', 'reviewed', 'published']
DocType = Literal['FP', 'TTG', 'SJ', 'PO', 'FPJ', 'PEL', 'CONTINUATION', 'OTHER']
LinkKey = Literal['sor', 'po_no', 'billing_no', 'amount', 'adjacency']
PageStatus = Literal['rendered', 'queued', 'read', 'failed', 'dead_letter']


# -------------------------------------------------- satellite


class CustomerProfile(TypedDict, total=False):
    """satellite.customer_profile"""
    customer_code: str
    customer_name: str
    doc_aliases: Any
    expected_docs: list[DocType]
    # Observed: Hari Hari prints the SOR as "No Ref." on its Receiving Slip (sor); Boots and Hero print only the
    # PO number (po_no).
    link_key: LinkKey
    notes: str | None
    rounding_allowance: Decimal | None
    allowance_by: str | None
    allowance_at: datetime | None
    receipt_shows: str | None
    receipt_by: str | None
    receipt_at: datetime | None


class DocFakturPajak(TypedDict, total=False):
    """satellite.doc_faktur_pajak"""
    id: int
    # §6.1: SOR
    sor_no: str | None
    # §6.1: Billing Number
    billing_number: str | None
    # §6.1: Kode Seri
    kode_seri: str | None
    # §6.1: NPWP pengusaha
    npwp_pengusaha: str | None
    # §6.1: NITKU pengusaha
    nitku_pengusaha: str | None
    # §6.1: NPWP pembeli
    npwp_pembeli: str | None
    # §6.1: NITKU pembeli
    nitku_pembeli: str | None
    # §6.1: Dasar Pengenaan Pajak
    dpp: Decimal | None
    # §6.1: PPN
    ppn: Decimal | None
    # §6.1: Tanggal transaksi
    tanggal_transaksi: date | None
    page_ref: list[int] | None
    linked_by: LinkKey | None
    confidence: Decimal | None
    source_batch: str | None
    source_pages: list[int] | None


class DocFakturPenjualan(TypedDict, total=False):
    """satellite.doc_faktur_penjualan"""
    id: int
    # §6.1: SOR
    sor_no: str
    # §6.1: Dasar Pengenaan Pajak
    dpp: Decimal | None
    # §6.1: PPN
    ppn: Decimal | None
    # §6.1: Total
    total: Decimal | None
    # linking (not in §6.1): Nomor CPO
    nomor_cpo: str | None
    # linking (not in §6.1): Kepada
    customer_name: str | None
    # linking (not in §6.1): Customer code
    customer_code: str | None
    page_ref: list[int] | None
    linked_by: LinkKey | None
    confidence: Decimal | None
    source_batch: str | None
    source_pages: list[int] | None


class DocFakturPenjualanLine(TypedDict, total=False):
    """satellite.doc_faktur_penjualan_line"""
    doc_id: int
    line_no: int
    # §6.1: Kode material (field "Kode")
    kode_material: str | None
    # §6.1: Nama produk
    nama_produk: str | None
    # §6.1: Kemasan
    kemasan: str | None
    # §6.1: Qty (CRT)
    qty_crt: Decimal | None
    # §6.1: Qty (PCS)
    qty_pcs: Decimal | None


class DocPelunasanLine(TypedDict, total=False):
    """satellite.doc_pelunasan_line"""
    id: int
    payment_document_no: str | None
    payment_date: date | None
    total_payment_amount: Decimal | None
    customer_name: str | None
    reference_no: str | None
    reference_kind: str | None
    vendor_code: str | None
    vendor_name: str | None
    year_month: str | None
    invoice_receipt_date: date | None
    store_code: str | None
    due_date: date | None
    amount: Decimal | None
    line_text: str | None
    sor_no: str | None
    ar_document: str | None


class DocPo(TypedDict, total=False):
    """satellite.doc_po"""
    id: int
    sor_no: str
    # §6.1: Purchase Order No
    purchase_order_no: str | None
    # §6.1: Vendor code
    vendor_code: str | None
    # §6.1: Vendor name (nama lengkap SAMB)
    vendor_name: str | None
    # §6.1: PPN
    ppn: Decimal | None
    # §6.1: Total
    total: Decimal | None
    # linking (not in §6.1): Customer
    customer_name: str | None
    page_ref: list[int] | None
    linked_by: LinkKey | None
    confidence: Decimal | None
    source_batch: str | None
    source_pages: list[int] | None


class DocPoLine(TypedDict, total=False):
    """satellite.doc_po_line"""
    doc_id: int
    line_no: int
    # §6.1: Product code
    product_code: str | None
    # §6.1: Product description
    product_description: str | None
    # §6.1: Qty
    qty: Decimal | None
    # §6.1: UOM
    uom: str | None
    # §6.1: Unit price
    unit_price: Decimal | None
    # §6.1: Discount
    discount: str | None
    # §6.1: Satuan
    satuan: Decimal | None


class DocSuratJalan(TypedDict, total=False):
    """satellite.doc_surat_jalan"""
    id: int
    sor_no: str
    no_sj: str | None
    tgl_sj: date | None
    page_ref: list[int]
    linked_by: LinkKey
    confidence: Decimal | None
    source_batch: str | None
    source_pages: list[int] | None


class DocTtg(TypedDict, total=False):
    """satellite.doc_ttg"""
    id: int
    sor_no: str
    # §6.1: Posting date
    posting_date: date | None
    # §6.1: Document No
    document_no: str | None
    # §6.1: Purchase Order No
    purchase_order_no: str | None
    # §6.1: Vendor Number
    vendor_number: str | None
    # linking (not in §6.1): No Ref
    no_ref: str | None
    # linking (not in §6.1): Customer
    customer_name: str | None
    page_ref: list[int] | None
    linked_by: LinkKey | None
    confidence: Decimal | None
    source_batch: str | None
    source_pages: list[int] | None


class DocTtgLine(TypedDict, total=False):
    """satellite.doc_ttg_line"""
    doc_id: int
    line_no: int
    # §6.1: Item code
    item_code: str | None
    # §6.1: Material description
    material_description: str | None
    # §6.1: Qty
    qty: Decimal | None
    # §6.1: UOM
    uom: str | None
    # §6.1: Satuan
    satuan: Decimal | None


class ProductCodeMap(TypedDict, total=False):
    """satellite.product_code_map"""
    customer_code: str
    customer_item_code: str
    customer_barcode: str | None
    samb_material_code: str
    description: str | None
    confirmed_by: str | None
    confirmed_at: datetime | None


class Sor(TypedDict, total=False):
    """satellite.sor"""
    # The SOR, e.g. SOR26110264726. Satellite's export: order_no.
    sor_no: str
    # The ship-to store's id. Satellite's export: customer_id.
    customer_code: str
    # The ship-to store's name. Satellite's export: customer_name.
    customer_name: str | None
    # The customer's PO number: the Faktur Penjualan's Nomor CPO; a PO or a receipt finds its order by it.
    # Satellite's export: po_number, in the PO file.
    cpo_no: str | None
    # The order's date. Satellite's export: created_at.
    tgl_so: date | None
    # The total as invoiced, with PPN (after any tolakan; 0 before billing). Satellite's export:
    # inv_grand_total_amount.
    total: Decimal | None
    # Satellite's sales-order number, SG…. Satellite's export: sales_order_no, in the PO file.
    so_no: str | None
    # DPP as invoiced. Satellite's export: inv_subtotal_after_discount_amount.
    dpp: Decimal | None
    # PPN as invoiced. Satellite's export: inv_vat_amount.
    ppn: Decimal | None
    # The PPN rate, e.g. 11.00. Satellite's export: vat_percentage.
    vat_pct: Decimal | None
    # SAP's billing number, once the order is posted. Satellite's export: sap_bill_doc_no.
    billing_no: str | None
    # The goods receipt (CGR) number. Satellite's export: cgr_doc_no.
    cgr_no: str | None
    # The goods receipt's date. Satellite's export: cgr_doc_date.
    cgr_date: date | None
    # SAP's posting date. Satellite's export: posting_date.
    posting_date: date | None
    # The order's status: INVOICE_GENERATED, CGR, …. Satellite's export: document_status.
    status: str | None
    # When this row was written here (set by us).
    loaded_at: datetime | None
    # DPP as ordered: what the Faktur Penjualan prints. Satellite's export: subtotal_after_discount_amount.
    order_dpp: Decimal | None
    # PPN as ordered. Satellite's export: vat_amount.
    order_ppn: Decimal | None
    # The total as ordered, with PPN: what the Faktur Penjualan prints. Satellite's export: grand_total_amount.
    order_total: Decimal | None
    # The parent customer id the store is under. Satellite's export: ship_to_parent_customer_id.
    customer_parent: str | None


class SorDocument(TypedDict, total=False):
    """satellite.sor_document"""
    sor_no: str
    pdf_path: str
    page_count: int
    version: int
    source_batch: str | None
    updated_at: datetime


class SorItem(TypedDict, total=False):
    """satellite.sor_item"""
    # The order this line belongs to. Satellite's export: order_id, as its order's order_no.
    sor_no: str
    # The line's number in the order. Satellite's export: line_no.
    line_no: int
    # SAMB's material code (the Faktur Penjualan's Kode). Satellite's export: item_code.
    item_code: str
    # The product's name. Satellite's export: item_description.
    description: str | None
    # The unit it was ordered in: CAR, PCS, …. Satellite's export: ordered_uom_id.
    uom: str | None
    # Pieces in one unit. Satellite's export: conversion_factor.
    pcs_per_uom: Decimal | None
    # The quantity ordered, in units. Satellite's export: ordered_qty.
    qty_uom: Decimal | None
    # The quantity ordered, in pieces. Satellite's export: ordered_base_qty.
    qty_pcs: Decimal | None
    # The price of one unit. Satellite's export: price_list_amount.
    price_uom: Decimal | None
    # The price of one piece (the Faktur Penjualan's Harga). Satellite's export: base_price_list_amount.
    price_pcs: Decimal | None
    # Each discount given: {kind: {type, value, amount}}, kinds reg, dc, b2b, dmg, new, go, special, lsb, promo,
    # additional. Satellite's export: disc_<kind>_type, disc_<kind>_value, disc_<kind>_amount.
    discounts: Any
    # All discounts together. Satellite's export: total_discount_amount.
    discount_total: Decimal | None
    # The amount before discounts. Satellite's export: subtotal_amount.
    gross: Decimal | None
    # The net amount (the Faktur Penjualan's Jumlah). Satellite's export: total_amount.
    line_amount: Decimal | None
    # PPN on the line. Satellite's export: vat_amount.
    vat: Decimal | None
    # Invoiced, in pieces. Satellite's export: invoice_base_qty.
    invoice_qty: Decimal | None
    # The invoiced net amount. Satellite's export: invoice_nett_amount.
    invoice_amount: Decimal | None
    # Received, in pieces (the goods receipt). Satellite's export: cgr_base_qty.
    cgr_qty: Decimal | None
    # Rejected by the store (tolakan), in pieces. Satellite's export: cgr_rejected_base_qty.
    rejected_qty: Decimal | None
    # Why the store rejected it. Satellite's export: cgr_reject_reason.
    reject_reason: str | None


# -------------------------------------------------- staging


class AiPayload(TypedDict, total=False):
    """staging.ai_payload"""
    call_id: int
    at: datetime
    http: int | None
    request: Any
    response: Any


class Bundle(TypedDict, total=False):
    """staging.bundle"""
    id: int
    sor_no: str
    status: BundleStatus
    json: Any
    checks: Any
    confidence: Decimal | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    published_at: datetime | None
    created_at: datetime
    hold_reason: str | None
    folder: str | None
    fingerprint: str | None
    checked_at: datetime | None


class BundleDecision(TypedDict, total=False):
    """staging.bundle_decision"""
    sor_no: str
    check_name: str
    input_print: str
    reason: str
    note: str | None
    decided_by: str
    decided_at: datetime


class BundleDocument(TypedDict, total=False):
    """staging.bundle_document"""
    bundle_id: int
    document_id: int


class ContextVersion(TypedDict, total=False):
    """staging.context_version"""
    version: int
    parent: int | None
    status: str
    content: Any
    gate: Any
    created_by: str
    created_at: datetime
    approved_by: str | None
    approved_at: datetime | None
    note: str | None


class Document(TypedDict, total=False):
    """staging.document"""
    id: int
    batch_id: str
    doc_type: DocType
    page_from: int
    page_to: int
    customer_code: str | None
    key_sor: str | None
    key_po_no: str | None
    resolved_sor: str | None
    linked_by: LinkKey | None
    confidence: Decimal | None
    keys: Any
    evidence: Any
    hold_reason: str | None
    suggested_sor: str | None


class Efaktur(TypedDict, total=False):
    """staging.efaktur"""
    id: int
    sha256: str
    upload_id: int | None
    file_name: str
    file_path: str
    pages: int
    received_at: datetime
    billing_no: str
    sor_no: str
    kode_seri: str | None
    buyer: str | None
    dpp: Decimal | None
    ppn: Decimal | None
    status: str
    why: str | None
    checked_at: datetime | None
    attached_version: int | None
    attached_at: datetime | None


class ExtractExample(TypedDict, total=False):
    """staging.extract_example"""
    id: int
    batch_id: str
    page_no: int
    confirmation: str
    doc_type: str | None
    chain: str | None
    field: str
    row_key: str | None
    kind: str
    value: str | None
    shown: str | None
    region: Any
    tess_words: str | None
    blocks: Any
    anchor: Any
    printed: bool | None
    source: str
    pile: str
    status: str
    made_by: str | None
    made_at: datetime
    lesson_status: str | None
    lesson: Any
    lesson_doc: str | None
    lesson_version: int | None
    lesson_at: datetime | None


class FieldCheck(TypedDict, total=False):
    """staging.field_check"""
    id: int
    batch_id: str
    page_no: int
    field_path: str
    vlm_value: str | None
    # value printed in Tesseract's text (same line, letters+digits, not inside a longer number)
    classical_match: bool
    # phase 7: the value a person corrected it to
    adjudicated_value: str | None
    # phase 7: who corrected it (a person; there is no verifier model)
    adjudicator: str | None
    # ok | check | empty (phase 5, code); corrected (a person, phase 7)
    status: str
    source_text: str | None
    confirmed_by: str | None
    reason: str | None


class FieldConfirmation(TypedDict, total=False):
    """staging.field_confirmation"""
    batch_id: str
    page_no: int
    field: str
    value: str
    confirmed_by: str
    confirmed_at: datetime
    row_key: str | None
    shown: str | None


class JobRun(TypedDict, total=False):
    """staging.job_run"""
    name: str
    last_started: datetime | None
    last_finished: datetime | None
    runs: int
    last_result: Any
    last_error: str | None


class KnowledgeMap(TypedDict, total=False):
    """staging.knowledge_map"""
    batch_id: str
    page_no: int
    hints_sha: str
    map_version: str
    raw: Any
    raw2: Any
    created_at: datetime


class KnowledgePage(TypedDict, total=False):
    """staging.knowledge_page"""
    doc_type: str
    version: int
    parent: int | None
    status: str
    markdown: str
    source: str
    gate: Any
    note: str | None
    created_by: str
    created_at: datetime
    approved_by: str | None
    approved_at: datetime | None
    # what the teacher is doing with this version now: testing it on stored pages, or applying it (for the
    # status bar)
    progress: Any


class Lesson(TypedDict, total=False):
    """staging.lesson"""
    id: int
    batch_id: str
    page_no: int
    label: DocType
    status: str
    teacher_model: str | None
    answer: Any
    proposal: int | None
    error: str | None
    created_at: datetime
    asked_at: datetime | None


class LineMatch(TypedDict, total=False):
    """staging.line_match"""
    batch_id: str
    page_no: int
    row_index: int
    sor_no: str
    so_line_no: int | None
    how: str
    status: str
    reason: str | None
    customer_code: str | None
    ean: str | None
    proposed_at: datetime


class ModelCall(TypedDict, total=False):
    """staging.model_call"""
    id: int
    at: datetime
    pacific_day: date
    provider: str
    model: str | None
    purpose: str
    batch_id: str | None
    page_no: int | None
    ok: bool
    ms: int | None
    tokens: Any
    error: str | None


class Notice(TypedDict, total=False):
    """staging.notice"""
    id: int
    at: datetime
    kind: str
    items: Any
    text: str
    seen_at: datetime | None


class Page(TypedDict, total=False):
    """staging.page"""
    batch_id: str
    page_no: int
    # Upright image after enhancement (phase 2). Until then it equals original_path.
    image_path: str
    rotation: int
    black_ratio: Decimal | None
    classical_text: str | None
    # Decided type; NULL when type_status = unsure. Only a decided type may be used by grouping.
    doc_type: DocType | None
    doc_type_conf: Decimal | None
    is_continuation: bool
    footer: str | None
    # Keys derived by code from fields + QR: sor, po_no, … each with how it was confirmed. Only confirmed keys
    # may be used for grouping.
    keys: Any
    # vlm-first: fields_all projected onto the page's type (per-type names, as v1), so the phase-5 check and
    # table mapping are unchanged.
    fields: Any
    status: PageStatus
    model_vlm: str | None
    model_ms: int | None
    model_cost_idr: Decimal | None
    read_at: datetime | None
    original_path: str | None
    thumb_path: str | None
    upright_path: str | None
    clean_path: str | None
    thumb_upright_path: str | None
    osd_conf: Decimal | None
    skew_angle: Decimal | None
    dark_band_ratio: Decimal | None
    speckle_ratio: Decimal | None
    ocr_variant: str | None
    variant_scores: Any
    ocr_conf: Decimal | None
    confident_chars: int | None
    ocr_words: Any
    quality_flags: list[str]
    qr_text: str | None
    error: str | None
    ms_enhance_ocr: int | None
    type_status: str | None
    type_guess: DocType | None
    type_votes: Any
    layout_score: Decimal | None
    enhance_version: int | None
    classify_version: int | None
    extract_status: str | None
    extract_error: str | None
    extract_version: int | None
    vlm_meta: Any
    vlm_read: Any
    verify_version: int | None
    fields_all: Any
    fields_version: str | None
    context_version: int | None
    zoom: Any
    second_look: Any
    prep_version: int | None
    outcome: str | None
    # S4: the store the page says the goods go to, asked blind only when it can decide a key (worker/vf.py
    # store_step)
    ship_to: Any
    # the printed title FAKTUR PENJUALAN, found by Tesseract in the top 35% (worker/vf.py fp_title); NULL = not
    # looked for
    fp_title: bool | None
    # the AI OCR's copy of the whole page, never a witness (read-then-map)
    transcript: Any
    transcript_version: str | None
    transcript_status: str | None
    # handwriting, stamps, marks and remarks the text model found: AI claims, shown, never checked
    notes: Any
    mapping: Any
    pick: Any


class ReadingTrial(TypedDict, total=False):
    """staging.reading_trial"""
    batch_id: str
    page_no: int
    variant: str
    transcript: Any
    fields_all: Any
    notes: Any
    mapping: Any
    versions: Any
    meta: Any
    error: str | None
    created_at: datetime


class ScanBatch(TypedDict, total=False):
    """staging.scan_batch"""
    id: str
    file_name: str
    file_path: str
    sha256: str
    scanned_day: date
    page_total: int
    # Fan-in counter. Each page worker runs UPDATE … SET page_done = page_done + 1 RETURNING page_done,
    # page_total; the worker that sees them equal publishes {batch_id} to q.group.
    page_done: int
    # splitting → split → queued (phase 1) → reading → grouping → done
    status: str
    received_at: datetime
    pages_rendered: int
    error: str | None
    run: int
    upload_id: int | None
    # When a person stopped this file's AI work (status stopped); NULL once it goes on.
    stopped_at: datetime | None
    # Who stopped it.
    stopped_by: str | None
    # The status it had when stopped (received, splitting, split, queued, reading, read): where going on starts.
    stopped_from: str | None


class Setting(TypedDict, total=False):
    """staging.setting"""
    name: str
    value: str
    updated_by: str
    updated_at: datetime


class Trace(TypedDict, total=False):
    """staging.trace"""
    id: int
    at: datetime
    ended_at: datetime | None
    ms: int | None
    status: str
    kind: str
    service: str | None
    batch_id: str | None
    page_no: int | None
    sor_no: str | None
    parent: int | None
    who: str | None
    detail: Any
    error: str | None


class TypeLabel(TypedDict, total=False):
    """staging.type_label"""
    batch_id: str
    page_no: int
    label: DocType
    customer: str | None
    note: str | None
    labelled_by: str | None
    labelled_at: datetime
    # Assigned once at random (20% exam). Relabelling a page changes the label, never the pile.
    pile: str


class Upload(TypedDict, total=False):
    """staging.upload"""
    id: int
    code: str
    uploaded_by: str
    doc_date: date
    created_at: datetime
    note: str | None


TABLES: dict[str, type] = {
    'satellite.customer_profile': CustomerProfile,
    'satellite.doc_faktur_pajak': DocFakturPajak,
    'satellite.doc_faktur_penjualan': DocFakturPenjualan,
    'satellite.doc_faktur_penjualan_line': DocFakturPenjualanLine,
    'satellite.doc_pelunasan_line': DocPelunasanLine,
    'satellite.doc_po': DocPo,
    'satellite.doc_po_line': DocPoLine,
    'satellite.doc_surat_jalan': DocSuratJalan,
    'satellite.doc_ttg': DocTtg,
    'satellite.doc_ttg_line': DocTtgLine,
    'satellite.product_code_map': ProductCodeMap,
    'satellite.sor': Sor,
    'satellite.sor_document': SorDocument,
    'satellite.sor_item': SorItem,
    'staging.ai_payload': AiPayload,
    'staging.bundle': Bundle,
    'staging.bundle_decision': BundleDecision,
    'staging.bundle_document': BundleDocument,
    'staging.context_version': ContextVersion,
    'staging.document': Document,
    'staging.efaktur': Efaktur,
    'staging.extract_example': ExtractExample,
    'staging.field_check': FieldCheck,
    'staging.field_confirmation': FieldConfirmation,
    'staging.job_run': JobRun,
    'staging.knowledge_map': KnowledgeMap,
    'staging.knowledge_page': KnowledgePage,
    'staging.lesson': Lesson,
    'staging.line_match': LineMatch,
    'staging.model_call': ModelCall,
    'staging.notice': Notice,
    'staging.page': Page,
    'staging.reading_trial': ReadingTrial,
    'staging.scan_batch': ScanBatch,
    'staging.setting': Setting,
    'staging.trace': Trace,
    'staging.type_label': TypeLabel,
    'staging.upload': Upload,
}
NAME: dict[type, str] = {cls: name for name, cls in TABLES.items()}
KEYS: dict[str, tuple[str, ...]] = {
    'satellite.customer_profile': ('customer_code',),
    'satellite.doc_faktur_pajak': ('id',),
    'satellite.doc_faktur_penjualan': ('id',),
    'satellite.doc_faktur_penjualan_line': ('doc_id', 'line_no'),
    'satellite.doc_pelunasan_line': ('id',),
    'satellite.doc_po': ('id',),
    'satellite.doc_po_line': ('doc_id', 'line_no'),
    'satellite.doc_surat_jalan': ('id',),
    'satellite.doc_ttg': ('id',),
    'satellite.doc_ttg_line': ('doc_id', 'line_no'),
    'satellite.product_code_map': ('customer_code', 'customer_item_code'),
    'satellite.sor': ('sor_no',),
    'satellite.sor_document': ('sor_no',),
    'satellite.sor_item': ('sor_no', 'line_no'),
    'staging.ai_payload': ('call_id',),
    'staging.bundle': ('id',),
    'staging.bundle_decision': ('sor_no', 'check_name'),
    'staging.bundle_document': ('bundle_id', 'document_id'),
    'staging.context_version': ('version',),
    'staging.document': ('id',),
    'staging.efaktur': ('id',),
    'staging.extract_example': ('id',),
    'staging.field_check': ('id',),
    'staging.field_confirmation': ('batch_id', 'page_no', 'field'),
    'staging.job_run': ('name',),
    'staging.knowledge_map': ('batch_id', 'page_no', 'hints_sha', 'map_version'),
    'staging.knowledge_page': ('doc_type', 'version'),
    'staging.lesson': ('id',),
    'staging.line_match': ('batch_id', 'page_no', 'row_index'),
    'staging.model_call': ('id',),
    'staging.notice': ('id',),
    'staging.page': ('batch_id', 'page_no'),
    'staging.reading_trial': ('batch_id', 'page_no', 'variant'),
    'staging.scan_batch': ('id',),
    'staging.setting': ('name',),
    'staging.trace': ('id',),
    'staging.type_label': ('batch_id', 'page_no'),
    'staging.upload': ('id',),
}


def columns(table: type) -> tuple[str, ...]:
    """A table's columns, in the database's order."""
    return tuple(table.__annotations__)


def select(table: type, without: tuple[str, ...] = (), prefix: str = "") -> str:
    """Its columns as a SELECT list (`prefix` "s." for a table aliased s), leaving out `without`."""
    return ", ".join(prefix + c for c in columns(table) if c not in without)


T = TypeVar("T")


def row(c, table: type[T], **where: Any) -> T | None:
    """One row of `table`, as SELECT * gives it, by column values (usually its key), or None when there is
    none: row(c, Page, batch_id=bid, page_no=n)."""
    if not where or not set(where) <= set(columns(table)):
        raise ValueError(f"{NAME[table]} has no column {sorted(set(where) - set(columns(table)))}")
    sql = f"SELECT * FROM {NAME[table]} WHERE " + " AND ".join(f"{k} = %s" for k in where)
    return c.execute(sql, tuple(where.values())).fetchone()
