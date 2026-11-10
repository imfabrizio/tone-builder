"""Punto di ingresso dell'eseguibile (PyInstaller)."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from tonebuilder.server import main

    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
