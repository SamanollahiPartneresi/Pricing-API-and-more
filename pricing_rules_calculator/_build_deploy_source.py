"""Build a deploy-ready Streamlit source: replace the rendered query_data
block (between the INJECTED_CODE / END_OF_INJECTED_CODE markers) with the
`{QUERY_DATA_FUNCTION}` placeholder that modify_streamlit_data_app expects."""

import re
from pathlib import Path

here = Path(__file__).parent
src = (here / "main.py").read_text()

pattern = re.compile(
    r"# ### INJECTED_CODE ####.*?# ### END_OF_INJECTED_CODE ####",
    re.DOTALL,
)
out, n = pattern.subn("{QUERY_DATA_FUNCTION}", src)
assert n == 1, f"expected exactly 1 injected block, found {n}"
assert "{QUERY_DATA_FUNCTION}" in out

(here / "_deploy_source.py").write_text(out)
print(f"Wrote _deploy_source.py ({len(out)} chars, replaced {n} block).")
