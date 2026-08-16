import { ExperimentDetailPage } from "./pages/ExperimentDetailPage";
import "./styles.css";

export function App() {
  const resultUrl =
    import.meta.env.VITE_EXPERIMENT_RESULT_URL ?? "/experiments/latest.json";
  return (
    <>
      <nav className="topbar">
        <span className="brand-mark">AI</span>
        <span className="brand-name">Atelier AI</span>
        <span className="nav-divider">/</span>
        <span className="nav-context">Quant Intelligence</span>
      </nav>
      <ExperimentDetailPage resultUrl={resultUrl} />
    </>
  );
}
