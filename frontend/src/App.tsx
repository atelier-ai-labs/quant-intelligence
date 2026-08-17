import { useEffect, useState } from "react";
import { fetchExperimentSummaries } from "./data/result";
import { ExperimentDetailPage } from "./pages/ExperimentDetailPage";
import { TraderDashboardPage } from "./pages/TraderDashboardPage";
import "./styles.css";

export function App() {
  const [view, setView] = useState<"research" | "trader">(() =>
    window.location.hash === "#trader" ? "trader" : "research",
  );
  const navigate = (next: "research" | "trader") => {
    window.location.hash = next === "trader" ? "trader" : "research";
    setView(next);
  };
  return (
    <>
      <nav className="topbar">
        <span className="brand-mark">AI</span>
        <span className="brand-name">Atelier AI</span>
        <span className="nav-divider">/</span>
        <span className="nav-context">Quant Intelligence</span>
        <div className="nav-actions">
          <button
            className={view === "research" ? "nav-button active" : "nav-button"}
            onClick={() => navigate("research")}
          >
            Research
          </button>
          <button
            className={view === "trader" ? "nav-button active" : "nav-button"}
            onClick={() => navigate("trader")}
          >
            Trader
          </button>
        </div>
      </nav>
      {view === "trader" ? <TraderDashboardPage /> : <ExperimentRoute />}
    </>
  );
}

function ExperimentRoute() {
  const [experimentId, setExperimentId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    fetchExperimentSummaries()
      .then((experiments) => {
        setExperimentId(experiments[0]?.experiment_id ?? null);
        setLoading(false);
      })
      .catch((reason: unknown) => {
        setError(
          reason instanceof Error
            ? reason.message
            : "Unable to load experiments",
        );
        setLoading(false);
      });
  }, []);
  if (loading)
    return (
      <main className="page">
        <div className="state-panel">
          <span className="eyebrow">Quant Intelligence</span>
          <h1>Loading experiments</h1>
          <div className="loading-bar" />
        </div>
      </main>
    );
  if (error)
    return (
      <main className="page">
        <div className="state-panel">
          <span className="eyebrow">Experiment service unavailable</span>
          <h1>Could not load experiments</h1>
          <p>{error}</p>
          <p className="muted">
            Start the FastAPI server and confirm it is serving{" "}
            <code>/api/experiments</code>.
          </p>
        </div>
      </main>
    );
  if (!experimentId)
    return (
      <main className="page">
        <div className="state-panel">
          <span className="eyebrow">No persisted experiments</span>
          <h1>Run an experiment to begin</h1>
          <p className="muted">
            The API is available, but its filesystem-backed experiment store is
            empty.
          </p>
        </div>
      </main>
    );
  return (
    <ExperimentDetailPage resultUrl={`/api/experiments/${experimentId}`} />
  );
}
