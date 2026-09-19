"""Paper-trading domain and execution boundary."""

from .autonomous import AutonomousTrader
from .broker import PaperBroker
from .broker import BrokerAccount, BrokerOrderStatus
from .clock import FakeClock, SystemClock
from .cycle import TradingCycleService
from .models import SignalAction, TradingDecision
from .scheduler import IntervalScheduler
from .status import OperationalStatus, StatusStore
from .alpaca import AlpacaBroker, AlpacaConfig, AlpacaConfigurationError, AlpacaMarketDataProvider, client_order_id_for_cycle
from .alpaca_adapter import AlpacaTradingAdapter
from .robinhood_adapter import RobinhoodMCPClient, RobinhoodMCPTransport, RobinhoodTradingAdapter
from .reconciliation import BrokerReconciliation, ReconciliationResult
from .sessions import AlpacaMarketSessionProvider, FixtureMarketSessionProvider, MarketSession, MarketSessionProvider
from .persistence import JsonOperationalRepository, PersistenceConfigurationError, SqlAlchemyOperationalRepository, operational_repository
from .runtime import CriticalResetError, CriticalRiskResetService, PersistentTraderService, RuntimeConfigurationError, ServiceAlreadyRunning, ServiceConfig, build_persistent_service
from .risk import RiskConfig, RiskGate, RiskMode, RiskState, RiskStateService

__all__ = ["AlpacaBroker", "AlpacaConfig", "AlpacaConfigurationError", "AlpacaMarketDataProvider", "AlpacaMarketSessionProvider", "AlpacaTradingAdapter", "AutonomousTrader", "BrokerAccount", "BrokerOrderStatus", "BrokerReconciliation", "CriticalResetError", "CriticalRiskResetService", "FakeClock", "FixtureMarketSessionProvider", "IntervalScheduler", "JsonOperationalRepository", "MarketSession", "MarketSessionProvider", "OperationalStatus", "PaperBroker", "PersistenceConfigurationError", "PersistentTraderService", "ReconciliationResult", "RobinhoodMCPClient", "RobinhoodMCPTransport", "RobinhoodTradingAdapter", "RuntimeConfigurationError", "ServiceAlreadyRunning", "ServiceConfig", "SignalAction", "SqlAlchemyOperationalRepository", "StatusStore", "SystemClock", "TradingCycleService", "TradingDecision", "RiskConfig", "RiskGate", "RiskMode", "RiskState", "RiskStateService", "build_persistent_service", "client_order_id_for_cycle", "operational_repository"]
