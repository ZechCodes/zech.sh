"""Repeat image trust tests through production Squid's existing OpenAI allowlist."""
import subprocess
import sys

subprocess.run([sys.executable, "-S", "/audit/verify_trust.py", "--runtime"], check=True)
