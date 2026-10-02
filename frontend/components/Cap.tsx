// An order's state as a rubber stamp (DESIGN.md: .cap, the only uppercase); .besar only in an order's own header.
const TONE: Record<string, string> = {
  needs_review: "merah", grouping: "kuning", auto_ok: "hijau", reviewed: "hijau", published: "biru", hold: "kuning",
};

export default function Cap({ status, children, big }: { status: string; children: React.ReactNode; big?: boolean }) {
  return <span className={`cap ${TONE[status] ?? status}${big ? " besar" : ""}`}>{children}</span>;
}
