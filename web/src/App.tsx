import { Activity, BarChart3, Flame, Github } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { BenchmarkView } from "./components/BenchmarkView";
import { ReplayView } from "./components/ReplayView";
import type { BenchmarkReport, CatalogIndex, ScenarioDocument } from "./types";

type Tab = "replay" | "benchmark";

export default function App() {
  const [tab, setTab] = useState<Tab>("replay");
  const [catalog, setCatalog] = useState<CatalogIndex | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [caseId, setCaseId] = useState<string>("seed-3-b5");
  const [doc, setDoc] = useState<ScenarioDocument | null>(null);
  const [docLoading, setDocLoading] = useState(false);
  const [docError, setDocError] = useState<string | null>(null);

  const [benchmark, setBenchmark] = useState<BenchmarkReport | null>(null);
  const [benchLoading, setBenchLoading] = useState(false);
  const [benchError, setBenchError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/data/index.json")
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to load catalog (${res.status})`);
        return res.json() as Promise<CatalogIndex>;
      })
      .then((data) => {
        if (cancelled) return;
        setCatalog(data);
        setCatalogError(null);
        setCaseId((prev) =>
          data.scenarios.some((s) => s.case_id === prev) ? prev : (data.scenarios[0]?.case_id ?? prev),
        );
      })
      .catch((err: unknown) => {
        if (!cancelled) setCatalogError(err instanceof Error ? err.message : "Failed to load catalog");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setDocLoading(true);
    setDocError(null);
    fetch(`/data/scenarios/${caseId}.json`)
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to load scenario ${caseId} (${res.status})`);
        return res.json() as Promise<ScenarioDocument>;
      })
      .then((data) => {
        if (!cancelled) setDoc(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setDocError(err instanceof Error ? err.message : "Failed to load scenario");
      })
      .finally(() => {
        if (!cancelled) setDocLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [caseId]);

  useEffect(() => {
    if (tab !== "benchmark" || benchmark) return;
    let cancelled = false;
    setBenchLoading(true);
    setBenchError(null);
    fetch("/data/benchmark.json")
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to load benchmark (${res.status})`);
        return res.json() as Promise<BenchmarkReport>;
      })
      .then((data) => {
        if (!cancelled) setBenchmark(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setBenchError(err instanceof Error ? err.message : "Failed to load benchmark");
      })
      .finally(() => {
        if (!cancelled) setBenchLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tab, benchmark]);

  const replayableCaseIds = useMemo(
    () => new Set((catalog?.scenarios ?? []).map((s) => s.case_id)),
    [catalog],
  );

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="sticky top-0 z-20 border-b border-slate-800/90 bg-slate-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-emerald-500/30 bg-emerald-500/15 text-emerald-400">
              <Flame className="h-4 w-4" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-sm font-semibold tracking-tight text-white">Thermal Sentinel</h1>
                {catalog && (
                  <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                    v{catalog.version}
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-400">
                Paired evaluation of fixed vs. risk-adaptive inspection patrols
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <nav
              aria-label="Primary views"
              className="flex rounded-lg border border-slate-800 bg-slate-900 p-0.5 text-xs"
            >
              <button
                type="button"
                onClick={() => setTab("replay")}
                className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 font-medium ${
                  tab === "replay" ? "bg-slate-800 text-emerald-300" : "text-slate-400 hover:text-slate-200"
                }`}
              >
                <Activity className="h-3.5 w-3.5" />
                Replay
              </button>
              <button
                type="button"
                onClick={() => setTab("benchmark")}
                className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 font-medium ${
                  tab === "benchmark"
                    ? "bg-slate-800 text-emerald-300"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                <BarChart3 className="h-3.5 w-3.5" />
                700-Case Benchmark
              </button>
            </nav>

            <a
              href="https://github.com/aryanhood/thermal-sentinel"
              target="_blank"
              rel="noreferrer"
              aria-label="GitHub repository"
              className="rounded-lg border border-slate-800 bg-slate-900 p-2 text-slate-400 hover:border-slate-600 hover:text-white"
            >
              <Github className="h-4 w-4" />
            </a>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-5 sm:px-6">
        {tab === "replay" ? (
          <ReplayView
            catalog={catalog?.scenarios ?? []}
            caseId={caseId}
            onSelectCase={setCaseId}
            doc={doc}
            loading={docLoading}
            error={catalogError ?? docError}
          />
        ) : (
          <BenchmarkView
            report={benchmark}
            loading={benchLoading}
            error={benchError}
            replayableCaseIds={replayableCaseIds}
            onOpenReplay={(id) => {
              setCaseId(id);
              setTab("replay");
            }}
          />
        )}
      </main>

      <footer className="mx-auto max-w-7xl border-t border-slate-900 px-4 py-4 text-xs text-slate-500 sm:px-6">
        Deterministic client-side replay from pre-exported paired runs · Zero shared server state · MIT
        License
      </footer>
    </div>
  );
}
