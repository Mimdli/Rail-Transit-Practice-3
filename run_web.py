"""Launcher for Web ATS — works around src/signal shadowing stdlib signal on Windows."""
import sys
import os

# Remove the src directory from sys.path so that src/signal doesn't shadow stdlib signal
project_root = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.join(project_root, "src")
# Filter out src_dir and project_root from path to avoid the signal shadow
sys.path = [p for p in sys.path if os.path.abspath(p) != src_dir]
# Now insert project_root and src at the end so they're lower priority than stdlib
if project_root not in sys.path:
    sys.path.append(project_root)
if src_dir not in sys.path:
    sys.path.append(src_dir)

import uvicorn

if __name__ == "__main__":
    uvicorn.run("src.web.app:app", host="127.0.0.1", port=8000, reload=False)
