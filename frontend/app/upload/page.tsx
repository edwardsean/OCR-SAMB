// Unggah batch: one PDF or many (each its own scan, all in one batch), any number of pages, unsorted.
import Link from "next/link";
import { need } from "@/lib/api";
import type { Scan } from "@/lib/types";
import ScanTable from "@/components/ScanTable";
import UploadForm from "./UploadForm";

export const metadata = { title: "Unggah batch" };

export default async function Upload() {
  const { scans } = await need<{ scans: Scan[] }>("/scans", { limit: 20 });
  return (
    <>
      <section className="head"><p className="crumbs"><Link href="/">Batch</Link> / unggah</p><h1>Unggah batch</h1></section>
      <UploadForm />
      <section>
        <h2>Scan terakhir</h2>
        <ScanTable scans={scans} />
      </section>
    </>
  );
}
