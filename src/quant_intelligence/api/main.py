import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from quant_intelligence import __version__
from quant_intelligence.application import ExperimentService
from quant_intelligence.experiments.store import ExperimentStore
from .routes.experiments import router as experiments_router
from .routes.trader import router as trader_router

def create_app(experiment_dir: str | None = None, trader_audit_dir: str | None = None, trader_broker_state: str | None = None) -> FastAPI:
    app = FastAPI(title="Quant Intelligence API", version=__version__)
    store = ExperimentStore(experiment_dir or os.getenv("QI_EXPERIMENT_DIR", "experiments"))
    app.state.experiment_store = store
    app.state.experiment_service = ExperimentService(store)
    audit_dir = trader_audit_dir or os.getenv("QI_PAPER_AUDIT_DIR", "paper_audit")
    broker_state = trader_broker_state or os.getenv("QI_PAPER_BROKER_STATE", str(os.path.join(audit_dir, "broker_state.json")))
    from quant_intelligence.trading.audit import TradingAuditStore
    from quant_intelligence.trading.status import StatusStore
    app.state.trader_audit_store = TradingAuditStore(audit_dir)
    app.state.trader_status_store = StatusStore(os.path.join(audit_dir, "status.json"))
    app.state.trader_broker_state_path = broker_state
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "quant-intelligence"}

    app.include_router(experiments_router)
    app.include_router(trader_router)
    return app

app = create_app()
