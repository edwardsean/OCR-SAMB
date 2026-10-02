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
  const fpage = Number(one(sp.fpage)) || null;
  return <OrderReview key={`${v.batch}/${sor}`} v={v} pub={pub} fixed={fpage ? { page: fpage, field: one(sp.fixed) ?? "" } : null} />;
}
