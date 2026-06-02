"""Alpha158Plus — Alpha158 with LLM-proposed factor expressions appended.

Qlib's `init_instance_by_config({"class": ..., "module_path": ..., "kwargs": ...})`
machinery instantiates this; the kwargs come from our task dict. The handler
needs to be importable from a module path that the Qlib process can resolve —
since we call Qlib's Python API directly (no qrun subprocess), our normal
package import path works.
"""

from __future__ import annotations

from typing import Any

from qlib.contrib.data.handler import Alpha158


class Alpha158Plus(Alpha158):
    """Append extra factor expressions to Alpha158's base feature set.

    The extra factors are evaluated alongside Alpha158's 158 expressions; the
    resulting dataset has 158 + len(extra_factors) columns. Downstream
    preprocessing (RobustZScoreNorm, Fillna) applies uniformly.
    """

    def __init__(self, *args: Any, extra_factors: list[dict] | None = None, **kwargs: Any) -> None:
        # Store BEFORE super().__init__ because Alpha158 calls get_feature_config
        # during init.
        self._extra_factors = list(extra_factors or [])
        super().__init__(*args, **kwargs)

    def get_feature_config(self) -> tuple[list[str], list[str]]:
        exprs, names = super().get_feature_config()
        exprs = list(exprs)
        names = list(names)
        for f in self._extra_factors:
            exprs.append(f["expression"])
            names.append(f["name"])
        return exprs, names
