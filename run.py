"""Start with: python run.py (Python 3.10+)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'vendor'))
from server.web import main

if __name__ == '__main__':
    main()
