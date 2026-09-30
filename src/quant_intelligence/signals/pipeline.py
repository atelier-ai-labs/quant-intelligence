"""Point-in-time assembly of signal inputs for one ticker from EDGAR.

Supports a single latest 10-K pair (`build_inputs`) and an expanded multi-event
history (`build_event_inputs`) that walks consecutive annual 10-K pairs plus
10-Q Item 1A YoY / consecutive-period diffs when extractable.
"""

from .event_inputs import *  # noqa: F403
from .event_inputs import (  # noqa: F401 - re-export private helpers used by unit tests
    ALLOWED_FORMS,
    DEFAULT_CONFIG,
    PipelineError,
    PipelineReport,
    UniverseConfig,
    _prior_extractable,
    _yoy_prior_10q,
    build_event_inputs,
    build_inputs,
    load_universe,
    select_10k_pair,
    select_form4,
)
