"""Re-init-safe qlib.init for long-lived processes.

qlib is designed to be initialised once per process. The ops UI, though, trains
and exports across multiple universes — each with its own provider_uri
(us_data, in_data, crypto_data, …) — inside one long-running process, so it
MUST re-init qlib repeatedly (and a single onboarding run does so twice: once
to train, once to export signals).

qlib refuses to re-init while an experiment is still "active":
``RecorderWrapper.register`` raises ``RecorderInitializationError`` ("Please
don't reinitialize Qlib if QlibRecorder is already activated"). A completed
``with R.start()`` block ends the recorder but does NOT reset
``exp_manager.active_experiment`` — qlib's ``end_exp`` only clears
``_active_exp_uri`` — so the stale reference survives and trips the next init.
We clear it ourselves before delegating to ``qlib.init``.
"""

from __future__ import annotations

import contextlib
from typing import Any


def safe_qlib_init(**kwargs: Any) -> None:
    """``qlib.init`` that tolerates repeated calls across universes in one process."""
    import qlib

    # On the very first init nothing is registered yet, so this whole block is a
    # no-op (suppressed). On later inits it drops the stale active experiment.
    with contextlib.suppress(Exception):
        from qlib.workflow import R

        expm = getattr(R, "exp_manager", None)
        if expm is not None and getattr(expm, "active_experiment", None) is not None:
            # End cleanly if we can (flushes the mlflow run), then drop the
            # stale reference so register() won't raise on the next init.
            with contextlib.suppress(Exception):
                R.end_exp()
            expm.active_experiment = None

    qlib.init(**kwargs)
