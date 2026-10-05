import type { Metadata } from "next";
import type { ReactNode } from "react";
import { get, getWords } from "@/lib/api";
import type { Session } from "@/lib/types";
import TopBar from "@/components/TopBar";
import { WordsProvider } from "@/components/Words";
import "./globals.css";             // the design: frontend/DESIGN.md

export const metadata: Metadata = {
  title: { template: "%s · SAMB Rekonsiliasi AR", default: "Batch · SAMB Rekonsiliasi AR" },
  icons: {
    icon: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='2' fill='%231A1D22'/%3E%3Ctext x='16' y='22' font-family='Arial' font-weight='800' font-size='17' text-anchor='middle' fill='white'%3ES%3C/text%3E%3C/svg%3E",
  },
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  let session: Session | null = null, words = null, down: string | null = null;
  try {
    [session, words] = await Promise.all([get<Session>("/session"), getWords()]);
  } catch (e) {
    down = (e as Error).message;
  }
  return (
    <html lang="id">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" />
      </head>
      <body>
        <a className="skip" href="#isi">Langsung ke isi</a>
        <TopBar initial={session} />
        <main id="isi">
          {down || !words ? (
            <section className="alert bad">
              <strong>Sistem belum bisa dihubungi.</strong> Coba muat ulang halaman ini sebentar lagi; bila tetap
              begini, hubungi tim IT.
              <details className="small"><summary>Detail untuk tim IT</summary><code>{down}</code></details>
            </section>
          ) : (
            <WordsProvider words={words}>{children}</WordsProvider>
          )}
        </main>
      </body>
    </html>
  );
}
