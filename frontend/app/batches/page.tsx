// Riwayat batch became the Batch register on the home page (2026-10-05): old links land there.
import { redirect } from "next/navigation";

export default function Batches() {
  redirect("/");
}
