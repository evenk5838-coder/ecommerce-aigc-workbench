"""Run the retained 21-case director suite without replacing historical evidence."""
from pathlib import Path
import importlib.util
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("legacy_director", ROOT / "backend/tools_director_chat_tests.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory(prefix="v011-legacy-evidence-") as temporary, \
        patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")), \
        patch("socket.create_connection", side_effect=AssertionError("Network forbidden")):
    module.OUT = Path(temporary)
    raise SystemExit(module.run())
