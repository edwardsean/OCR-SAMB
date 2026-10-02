// Riwayat scan: every scan, newest first.
import Link from "next/link";
import { need } from "@/lib/api";
import type { Scan } from "@/lib/types";
import ScanTable from "@/components/ScanTable";

export const metadata = { title: "Riwayat scan" };

export default async function Batches() {
  const { scans } = await need<{ scans: Scan[] }>("/scans", { limit: 100 });
  return (
    <>
      <section className="head head-row">
        <h1>Riwayat scan</h1>
        <Link className="btn" href="/upload">Unggah scan baru</Link>
      </section>
      <section><ScanTable scans={scans} /></section>
    </>
  );
}
