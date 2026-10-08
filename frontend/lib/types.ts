// The shapes /api/v1 answers with (services/api/v1.py, from app.py's view functions). Values as stored: amounts are
// numbers, dates ISO strings. Only what the screens use is typed; the rest passes through.

export type Box = [number, number, number, number] | null; // [ymin, xmin, ymax, xmax] on 0-1000

export type Words = {
  DOC: Record<string, string>;
  DOC_SHORT: Record<string, string>;
  CHECK: Record<string, string>;
  CHECK_STATUS: Record<string, string>;
  BUNDLE: Record<string, string>;
  BATCH: Record<string, string>;
  OUTCOME: Record<string, string>;
  FLAG: Record<string, string>;
  HOLD: Record<string, string>;
  LINK: Record<string, string>;
  PUB_STATE: Record<string, string>;
  REASON: Record<string, string>;
  NONE_REASON: Record<string, string>;
  ROLE: Record<string, string>;
  BY: Record<string, string>;
  COL: Record<string, string>;
  FIELD: Record<string, string>;
  FIELD_BY_TYPE: Record<string, Record<string, string>>;
  DESC_BY_TYPE: Record<string, Record<string, string>>;
  LABEL_TYPES: { key: string; name: string; what: string }[];
};

export type Session = {
  needs_you: number; unsure_left: number; teacher: string | null; today: string; vf: boolean;
  /** batches with a step a person can act on now: the Batch tab's count */
  batches_need: number;
};

export type Scan = {
  id: string;
  file_name: string;
  page_total: number;
  pages_rendered: number;
  page_done: number;
  status: string;
  received_at: string;
};

export type HomeScan = Scan & {
  unsure: number; waiting_ai: number; held: number; need: number; waiting: number; ready: number; published: number;
  orders: number;
};
export type Home = { scans: HomeScan[]; uploads: Upload[]; todo: Record<"need" | "unsure" | "ready" | "held", { n: number; batch: string | null }> };

export type PageRow = {
  page_no: number; status: string; thumb_path: string | null; thumb_upright_path: string | null;
  quality_flags: string[] | null; qr_text: string | null; error: string | null; doc_type: string | null;
  type_status: string | null; type_guess: string | null;
};
export type Orders = { total: number; need: number; waiting: number; ready: number; published: number; held: number };
/** An upload batch (2026-10-05): one upload action, however many files: its number, who uploaded it, the scan date. */
export type UploadRef = { id: number; code: string; uploaded_by: string; doc_date: string };
export type Upload = UploadRef & {
  created_at: string; note: string | null; files: number; pages: number; read: number; busy: number;
  need: number; waiting: number; ready: number; published: number; orders: number;
  steps: Step[]; next: StepKey | null; finished: boolean;
};

/** A batch's five steps (services/api/steps.py), in the order each needs the one before. */
export type StepKey = "baca" | "jenis" | "cocokkan" | "periksa" | "kirim";
/** need: a person can act now · sys: the system works, wait · done · none: nothing reached it yet · later: only
 * what never blocks sending (a Faktur Pajak, an order waiting for another batch's document) */
export type StepState = "need" | "sys" | "done" | "none" | "later";
/** after: the earlier step it waits for, when nothing has reached it or it can't be finished before that one */
type StepBase = { state: StepState; after?: StepKey };
export type Step =
  | StepBase & { key: "baca"; pages: number; done: number; read: number; failed: number; busy: number; again: number;
                 waiting_ai: number; unscheduled: number; splitting: number }
  | StepBase & { key: "jenis"; unsure: number; answered: number; pending: number }
  | StepBase & { key: "cocokkan"; block: number; wait: number; later: number; loose_unread: number; loose_unsure: number; loose_other: number }
  | StepBase & { key: "periksa"; need: number; depends: number; outside: number; waiting: number; ready: number; published: number; orders: number }
  | StepBase & { key: "kirim"; ready: number; published: number };
/** an order's place in its batch's step 4 */
export type OrderStep = "need" | "depends" | "outside" | "waiting" | "ready" | "published";
export type PageRef = { batch_id: string; page_no: number; file_name: string; thumb: string | null; error: string | null };
/** A stuck page (services/api/stuck.py): why, in plain words, and whether a person may try it again. */
export type StuckPage = PageRef & {
  kind: "crashed" | "call_failed"; cause: "setting" | "connection" | "answer" | "other"; reason: string;
  published: boolean; can_retry: boolean;
};
export type FailedFile = { batch_id: string; file_name: string; error: string | null };
export type UploadDetail = {
  upload: Upload; files: Scan[]; steps: Step[]; next: StepKey | null; finished: boolean;
  /** the open steps among 1–3: where an order's missing document may still be */
  blockers: StepKey[];
  orders: Record<string, OrderStep>; failed: StuckPage[]; unsure: PageRef[];
  failed_files: FailedFile[]; not_now: string | null;
  activity: Activity;
};
/** What each page of a batch is doing now (services/api/activity.py): one state per page. */
export type PageState = "queued" | "reading" | "waiting" | "waiting_ai" | "failed" | "idle" | "done";
export type PageNow = {
  batch_id: string; page_no: number; file_name: string; doc_type: string | null; unsure: boolean; thumb: string | null;
  state: PageState; text?: string; since?: string; ahead?: number | null; again?: boolean; ms?: number | null;
  kind?: StuckPage["kind"]; cause?: StuckPage["cause"]; can_retry?: boolean; published?: boolean; error?: string | null;
};
export type Activity = {
  pages: PageNow[]; splitting: { batch_id: string; file_name: string; status: string; page_total: number }[];
  eta_s: number | null; page_s: number; workers: number;
};
export type SearchResult = {
  q: string; uploads: Upload[];
  orders: { sor_no: string; status: string; customer_name: string | null; cpo_no: string | null; batch: string | null; uploads: UploadRef[]; thumb: string | null }[];
  files: { id: string; file_name: string; page_total: number; received_at: string; upload: UploadRef | null }[];
};
export type ScanDetail = { scan: Scan; pages: PageRow[]; orders: Orders; flags: Record<string, number>; upload: UploadRef | null };

export type FixField = {
  name: string; label: string; desc: string | null; value: string | null; box: Box; verdict: string;
  by: string | null; role: string | null; says: string; person: boolean;
};
export type FixRow = { i: number; key: string; text: string | null; box: Box; copied: string[]; cells: [string, string | null, boolean][] };
export type Unit = {
  id: string; block: string | null; i: number; s: number; e: number; text: string; tess: string | null;
  match: string; box: [number, number, number, number];
};
export type FixView = {
  image: string; type: string; fields: FixField[]; rows: FixRow[]; notes: string[]; customer: string | null;
  chain: string | null; units: Unit[]; lines: Record<string, string>; ready: boolean;
};
export type PageDetail = {
  scan: { id: string; file_name: string; page_total: number; status: string };
  upload: UploadRef | null;
  page: {
    page_no: number; status: string; doc_type: string | null; type_status: string | null; outcome: string | null;
    quality_flags: string[] | null; qr_text: string | null; upright_path: string | null; original_path: string | null;
    error: string | null;
  };
  fix: FixView | null;
  /** The page's type from a person (Ubah jenis, or the Label screen), and what the classifier said. */
  label: { label: string; labelled_by: string | null; labelled_at: string; note: string | null } | null;
  machine: { status: string | null; doc_type: string | null } | null;
  /** Why its type can't be changed now (its order is sent to Satellite; it is being processed), or null. */
  relabel_refused: string | null;
};

export type Lesson = {
  steps: { label: string; state: string }[]; headline: string; tip: string | null; final: boolean;
  why?: string | null; detail?: string | null;
};

export type OrderRow = {
  sor_no: string; status: string; customer_name: string | null; reviewed_by: string | null; docs: string[];
  thumb: string | null; issues: [string, "need" | "wait"][];
  /** the scan its FP (else its first document) is in: where its review opens; scans = how many files it came in */
  batch: string | null; scans: number;
  /** the upload batches its documents came in */
  uploads: UploadRef[];
  /** with a batch chosen: where it stands in that batch's step 4 */
  step?: OrderStep | null;
};
export type ReviewScan = { id: string; file_name: string; received_at: string; need: number };
export type Notice = { sor: string; batch: string; customer: string | null };
export type OrderList = {
  batch: string | null; upload: number | null; uploads: Upload[]; rows: OrderRow[]; scans: ReviewScan[]; fresh: Notice[];
  ready: number;
};

export type Suggest = [string, string];
export type FieldEntry = {
  name: string; label: string; value: string | null; why: string; ok: boolean; asked: boolean; suggest: Suggest[];
  page?: number; type?: string; box?: Box; approx?: boolean;
};
export type QtyFix = {
  page: number; i: number; key: string | null; line: number | null; desc: string | null; qty: string | null;
  uom: string | null; pieces: number | null; want: number | null; per: number | null; pack: boolean;
  issue: "unread" | "pack" | "differs" | "unpaired"; rejected: [number, string] | null; box?: Box;
};
export type OddRow = { page: number; i: number; desc: string; qty: string | null; uom: string | null; amount: number | null; box?: Box };
export type SoLine = { line_no: number; description: string; item_code: string };
export type OpenItem = {
  kind: "check" | "page" | "label" | "wait";
  title: string;
  page?: number;
  key?: string;
  plain?: string | null;
  status?: string;
  print?: string;
  accept?: boolean;
  gap?: number | null;
  allow?: number | null;
  tolakan?: string[];
  pair?: [string, number | null][];
  qty_lines?: { line_no: number; receipt: number | null; satellite: number }[];
  qty_bad?: { line_no: number }[];
  qty_fix?: QtyFix[];
  qty_missing?: { line_no: number; desc: string; satellite: number }[];
  tolakan_rows?: { line: number; pcs: number; why: string }[];
  suspect?: boolean;
  held_link?: string;
  fix?: FieldEntry[];
  fields?: FieldEntry[];
  odd_rows?: OddRow[];
  missing_lines?: SoLine[];
  ok_rows?: number;
};
export type Strip = { page: number; type: string; kind: string; first: boolean; thumb: string | null; img: string | null; flag: boolean };
export type Calibration = {
  chain: string; name: string; asks: { what: "allowance" | "receipt"; suggest?: string }[]; suggest: number | null;
  steps: number[];
};
export type DocRow = {
  i: number; key: string; row: Record<string, string | null>; match: { status: string }; line: SoLine | null;
  todo: { col: string; label: string; read: string | null; hints: Suggest[] }[]; bonus: boolean;
};
export type ReviewDoc = {
  page: number; type: string; kind: string; pages: number[]; outcome: string | null; head: FieldEntry[];
  kept: FieldEntry[]; rows: DocRow[];
};
/** A customer's row (PO or receipt) not paired with a line of SAMB's order yet; ai = the AI's suggested SO line. */
export type PairRow = {
  page: number; i: number; type: string; desc: string | null; code: string | null; qty: string | null; uom: string | null;
  ai: number | null; why: string | null;
};
/** The product matcher's run on one order (POST/GET /orders/{sor}/pair-proposals). */
export type PairRun = { state: "idle" | "running" | "done" | "failed"; rows?: number; proposed?: number; calls?: number; error?: string };
export type Order = {
  batch: string; sor: string;
  bundle: {
    id: number; sor_no: string; status: string; hold_reason: string | null; reviewed_by: string | null;
    reviewed_at: string | null; published_at: string | null;
    documents: { type: string; lines: number; confidence: number | null }[];
  };
  so: { customer_name?: string | null } | null;
  lines: SoLine[];
  checks: Record<string, { status: string; why: string }>;
  labels: Record<string, string>;
  documents: ReviewDoc[];
  can_approve: boolean;
  left: string[];
  accept_reasons: string[];
  none_reasons: string[];
  open_items: OpenItem[];
  strip: Strip[];
  passed: string[];
  calibration: Calibration | null;
  /** Each page of the order (numbered within the order) as its own scan and page; an order can span several scans. */
  where: Record<string, { batch: string; page: number; scan: string }>;
  multi: boolean;
  /** the upload batches its pages came in */
  uploads: UploadRef[];
  /** the customer's rows not paired with SAMB's lines yet, with the AI's suggestions */
  pairing: PairRow[];
};

export type PubRow = {
  sor_no: string; updated_at: string; page_count: number; version: number; source_batch: string;
  customer_name: string | null; total: number | null; pos: number; ttgs: number; uploads: UploadRef[];
};
export type PubField = { name: string; label: string; value: string | null; state: string; page: number | null; box: Box; approx: boolean };
export type PubDoc = {
  type: string; name: string; id: number; batch: string; page_ref: number[]; linked_by: string | null;
  pages: { n: number; img: string }[]; fields: PubField[]; cols: [string, string, string | null][];
  rows: { cells: (string | null)[]; line: number; page: number | null; box: Box }[];
  to_check: number; backed: number; filled: number;
};
export type PubTable = { type?: string; name: string; cols: [string, string, string | null][]; lines?: boolean; rows: (string | null)[][] };
export type Published = {
  sor: string; customer: string | null; docs: PubDoc[]; record: PubTable; tables: PubTable[];
  counts: Record<string, [number, number]>;
};

export type BundleDoc = {
  /** its own scan (an order's documents can come in several files), and that scan's upload batch */
  batch_id: string; scan: string; upload: string | null; uploaded_by?: string | null; doc_date?: string | null;
  type: string; page_from: number; page_to: number; pages: number[]; thumb: string | null; joined: string;
  evidence: string[] | null; why?: string; suggested_sor?: string | null;
  /** a held document's place in step 3: block (a person confirms its number) · wait (the system, SAP) · later */
  group?: "block" | "wait" | "later";
  confirm?: { field: string; read: string | null; value: string | null; done: unknown } | null;
};
export type Bundle = {
  sor: string; hold: string | null; folder: string | null; why: string | null; status: string; customer: string | null;
  documents: BundleDoc[];
  /** its documents came in more than one scan; batch = the scan its review opens from (the FP's) */
  many_scans: boolean; batch: string | null;
  uploads: UploadRef[];
};
export type Bundles = {
  batch: string | null; scans: Scan[]; upload: number | null; uploads: Upload[];
  view: { bundles: Bundle[]; held: BundleDoc[]; unplaced: { page: number; batch_id: string; scan: string; upload: string | null; type: string | null; thumb: string | null; why: string }[]; complete: number } | null;
};

export type LabelData = {
  batch: string | null;
  p?: { page_no: number; upright_path: string | null; original_path: string | null; quality_flags: string[] | null } | null;
  page?: number | null;
  total?: number;
  existing?: { label: string; customer: string | null; note: string | null; labelled_by: string | null } | null;
  near?: { page_no: number; thumb: string | null; full: string | null; type: string | null }[];
  types?: { key: string; name: string; what: string }[];
  customers?: string[];
  prog?: { unsure: number; unsure_done: number; labelled: number; practice: number; exam: number };
  /** opened from a batch's step 2: that batch, and how many of its pages are still unsure */
  upload?: UploadRef | null;
  left?: number | null;
};
