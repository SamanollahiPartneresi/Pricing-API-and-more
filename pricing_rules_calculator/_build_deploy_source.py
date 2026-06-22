"""Build a deploy-ready, **self-contained** Streamlit source for the
"Service Pricing Tool" data app, written to `_deploy_source.py`.

This app is deployed by **pasting the source into the Keboola UI** (it has no git
repo). A UI paste ships the source *verbatim* — it does NOT substitute any
`{QUERY_DATA_FUNCTION}` placeholder. (That substitution is done only by the
`modify_streamlit_data_app` MCP tool, never by the platform/UI deploy.) So the
artifact this script produces must be **fully runnable as-is**:

1. **Inline the rule engine.** Replace the
   `# ### PRICING_ENGINE #### … # ### END_PRICING_ENGINE ####` block — which, for
   local dev, just imports from repo-root `pricing_engine.py` — with the full
   body of `pricing_engine.py`. Keeps `pricing_engine.py` the single source of
   truth (shared with the Flask API); the Streamlit copy is *generated*.
2. **Keep the real `query_data`.** The `# ### INJECTED_CODE #### … ####` block in
   `main.py` holds a complete `query_data` that reads `BRANCH_ID` / `WORKSPACE_ID`
   / `KBC_TOKEN` / `KBC_URL` (all present on the prod app as secrets / auto-
   injected). We leave it intact — NO placeholder.

⚠️  Do NOT reintroduce a `{QUERY_DATA_FUNCTION}` placeholder here: a UI paste of a
placeholder source deploys the literal text and crashes the app with
`NameError: name 'QUERY_DATA_FUNCTION' is not defined` (this took the live UI
down once). The placeholder form is correct ONLY when deploying through the
`modify_streamlit_data_app` MCP tool.
"""

import re
from pathlib import Path

here = Path(__file__).parent
repo_root = here.parent
src = (here / "main.py").read_text()


def _engine_body() -> str:
    """Return `pricing_engine.py`'s source, ready to be inlined mid-file.

    Strips the module docstring and ANY `from __future__` import (only legal as
    the first statement of a module — `main.py` already has one at the top that
    covers the whole deployed file). All other imports are kept; re-importing
    `re`/`math`/`pandas`/etc. mid-file is harmless.
    """
    text = (repo_root / "pricing_engine.py").read_text()
    text = re.sub(r'\A\s*""".*?"""\s*', "", text, count=1, flags=re.DOTALL)
    text = re.sub(r"^from __future__ import .*\n", "", text, flags=re.MULTILINE)
    assert "from __future__" not in text, "stray __future__ import survived inlining"
    return text.strip("\n")


# Inline the rule engine (function replacement so backslashes in the engine
# source — regex patterns like r"\d+" — are not treated as re template escapes).
engine_pattern = re.compile(
    r"# ### PRICING_ENGINE ####.*?# ### END_PRICING_ENGINE ####",
    re.DOTALL,
)
banner = (
    "# ### PRICING_ENGINE — inlined from pricing_engine.py (self-contained deploy) ####\n"
    "# Source of truth is repo-root pricing_engine.py — DO NOT edit this copy.\n"
)
_engine_replacement = banner + _engine_body()
# Sanity-check the inlined body is the real engine, not an empty/truncated strip.
for _needle in ("def calculate(", "def _calc_tat_totals(", "SERVICE_BASE_FEES"):
    assert _needle in _engine_replacement, f"engine body missing {_needle!r} — inline failed"
out, n_engine = engine_pattern.subn(lambda _m: _engine_replacement, src)
assert n_engine == 1, f"expected exactly 1 PRICING_ENGINE block, found {n_engine}"

# Self-contained guarantees: NO placeholder, a real query_data, no dev-only shims.
assert "{QUERY_DATA_FUNCTION}" not in out, (
    "self-contained build must NOT contain the placeholder — a UI paste would "
    "deploy the literal text and crash with NameError"
)
assert "def query_data(" in out, "real query_data must be present in the artifact"
assert "from pricing_engine import" not in out, "engine import leaked into deploy artifact"
assert "_sys.path.insert" not in out, "dev-only sys.path shim leaked into deploy artifact"
assert out.count("from __future__ import annotations") == 1, "expected exactly one __future__ import"

(here / "_deploy_source.py").write_text(out)
print(
    f"Wrote _deploy_source.py ({len(out)} chars): self-contained — "
    f"inlined pricing_engine.py ({n_engine} block), kept real query_data, no placeholder."
)
