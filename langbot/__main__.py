"""Main entry point for langbot CLI."""

import warnings

# Suppress requests dependency version warning (must be before any imports)
warnings.filterwarnings("ignore", message=".*urllib3.*chardet.*charset_normalizer.*doesn't match.*")

from langbot.cli import app

if __name__ == "__main__":
    app()
