"use client";
// "Scan [file ▾]": choosing another scan opens the same screen for it.
import { useRouter } from "next/navigation";

export default function ScanPicker({ path, value, options, all }: {
  path: string; value: string | null; options: { id: string; label: string }[]; all?: string;
}) {
  const router = useRouter();
  return (
    <label className="muted small">Scan{" "}
      <select className="scanpick" value={value ?? ""} onChange={(e) => router.push(e.target.value ? `${path}?batch=${encodeURIComponent(e.target.value)}` : path)}>
        {all !== undefined && <option value="">{all}</option>}
        {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>
    </label>
  );
}
