// Unggah scan: one PDF, any number of pages, unsorted.
import { need } from "@/lib/api";
import type { Scan } from "@/lib/types";
import ScanTable from "@/components/ScanTable";
import UploadForm from "./UploadForm";

export const metadata = { title: "Unggah scan" };

export default async function Upload() {
  const { scans } = await need<{ scans: Scan[] }>("/scans", { limit: 20 });
  return (
    <>
      <section className="head"><h1>Unggah scan</h1></section>
      <UploadForm />
      <section>
        <h2>Scan terakhir</h2>
        <ScanTable scans={scans} />
      </section>
    </>
  );
}
