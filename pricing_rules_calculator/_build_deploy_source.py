"""Build a deploy-ready Streamlit source for the "Service Pricing Tool" data app.

The Streamlit app ships as a single inline source file (no git repo), so unlike
the Flask API it cannot `import pricing_engine` at runtime. This build step makes
the deploy artifact self-contained by performing two substitutions on `main.py`
and writing the result to `_deploy_source.py`:

1. **Inline the rule engine.** Replace the
   `# ### PRICING_ENGINE #### … # ### END_PRICING_ENGINE ####` block — which, for
   local dev, just imports from the repo-root `pricing_engine.py` — with the full
   body of `pricing_engine.py`. This keeps `pricing_engine.py` the single source
   of truth (shared with the Flask API); the Streamlit copy is *generated*, never
   hand-maintained, so the two engines can no longer drift.
2. **Inject query_data.** Replace the
   `# ### INJECTED_CODE #### … # ### END_OF_INJECTED_CODE ####` block with the
   `{QUERY_DATA_FUNCTION}` placeholder that `modify_streamlit_data_app` expects
   (Keboola wires in a platform-managed `query_data` at deploy time).
"""

import re
from pathlib import Path

here = Path(__file__).parent
repo_root = here.parent
src = (here / "main.py").read_text()


def _engine_body() -> str:
    """Return `pricing_engine.py`'s source, ready to be inlined mid-file.

    Strips the module docstring and the `from __future__ import annotations`
    line (only legal as the first statement of a module — `main.py` already has
    it at the top, which covers the whole file). All other imports are kept;
    re-importing `re`/`math`/`pandas`/etc. mid-file is harmless.
    """
    text = (repo_root / "pricing_engine.py").read_text()
    # Drop the leading module docstring.
    text = re.sub(r'\A\s*""".*?"""\s*', "", text, count=1, flags=re.DOTALL)
    # Drop ANY `from __future__` import(s): they are only legal as the first
    # statement of a module, and `main.py` already has one at the top that covers
    # the whole deployed file. Match regardless of which features are imported
    # (so editing pricing_engine.py's future line can't smuggle one mid-file).
    text = re.sub(r"^from __future__ import .*\n", "", text, flags=re.MULTILINE)
    assert "from __future__" not in text, "stray __future__ import survived inlining"
    return text.strip("\n")


# 1. Inline the rule engine.
engine_pattern = re.compile(
    r"# ### PRICING_ENGINE ####.*?# ### END_PRICING_ENGINE ####",
    re.DOTALL,
)
banner = (
    "# ### PRICING_ENGINE — inlined from pricing_engine.py by "
    "_build_deploy_source.py ####\n"
    "# Source of truth is repo-root pricing_engine.py — DO NOT edit this copy.\n"
)
# Use a function replacement so backslashes in the engine source (regex
# patterns like r"\d+") are NOT interpreted as re template escapes.
_engine_replacement = banner + _engine_body()
# Sanity-check the inlined body is the real engine, not an empty/truncated strip,
# so a botched _engine_body() can never yield a green build that breaks only at
# runtime (this is the safety net ARCHITECTURE.md §11.3 relies on).
for _needle in ("def calculate(", "def _calc_tat_totals(", "SERVICE_BASE_FEES"):
    assert _needle in _engine_replacement, f"engine body missing {_needle!r} — inline failed"
out, n_engine = engine_pattern.subn(lambda _m: _engine_replacement, src)
assert n_engine == 1, f"expected exactly 1 PRICING_ENGINE block, found {n_engine}"

# 2. Inject the query_data placeholder.
query_pattern = re.compile(
    r"# ### INJECTED_CODE ####.*?# ### END_OF_INJECTED_CODE ####",
    re.DOTALL,
)
out, n_query = query_pattern.subn("{QUERY_DATA_FUNCTION}", out)
assert n_query == 1, f"expected exactly 1 injected query_data block, found {n_query}"
assert "{QUERY_DATA_FUNCTION}" in out
assert "from pricing_engine import" not in out, "engine import leaked into deploy artifact"

(here / "_deploy_source.py").write_text(out)
print(
    f"Wrote _deploy_source.py ({len(out)} chars): inlined pricing_engine.py "
    f"({n_engine} block), injected query_data ({n_query} block)."
)
