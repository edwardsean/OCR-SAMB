// One order: what is left to decide (one card per decision), beside the order's pages; then everything else, folded.
import { get, need, one, type SearchParams } from "@/lib/api";
import type { Order, Published } from "@/lib/types";
import OrderReview from "./OrderReview";

export async function generateMetadata({ params }: { params: Promise<{ sor: string }> }) {
  return { title: `Periksa ${(await params).sor}` };
}

export default async function OrderPage({ params, searchParams }: { params: Promise<{ sor: string }>; searchParams: SearchParams }) {
  const { sor } = await params;
  const sp = await searchParams;
  const v = await need<Order>(`/orders/${sor}`, { batch: one(sp.batch) });
  const pub = v.bundle.status === "published" ? await get<Published>(`/published/${sor}`) : null;
  const raw = Number(one(sp.fpage)) || null;
  // the page viewer sends back its scan's page number; the order numbers its pages across scans (Order.where)
  const fpage = raw === null ? null
    : Number(Object.entries(v.where ?? {}).find(([, w]) => w.batch === v.batch && w.page === raw)?.[0] ?? raw);
  // the batch the order returns to: the one it was opened from, else the first its documents came in
  const back = v.uploads?.find((u) => String(u.id) === one(sp.upload)) ?? v.uploads?.[0] ?? null;
  return <OrderReview key={`${v.batch}/${sor}`} v={v} pub={pub} back={back}
                      fixed={fpage ? { page: fpage, field: one(sp.fixed) ?? "" } : null} />;
}
