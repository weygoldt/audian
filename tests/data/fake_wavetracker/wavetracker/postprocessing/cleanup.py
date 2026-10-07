"""Stub of wavetracker.postprocessing.cleanup.  ``FAKE_WT_CLEANUP_BREAK``
makes it change fund_v (which the runner must refuse); it reports progress
the way wavetracker's does (``progress(stage, fraction)``)."""

import os

import numpy as np

show_results = True


def main(folder, n_fish=None, progress=None, **params):
    if show_results:
        raise RuntimeError("the runner must switch show_results off")
    if progress is not None:
        progress("window 1/1", 0.0)
        progress("window 1/1", 0.25)  # twice the same per mille: sent once
        progress("window 1/1", 0.25)
        progress("joining overlapping tracks", 0.6)
    n = 2 if n_fish is None else n_fish
    ident = np.load(os.path.join(folder, "ident_v.npy"))
    fund = np.load(os.path.join(folder, "fund_v.npy"))
    if os.environ.get("FAKE_WT_CLEANUP_BREAK"):
        fund = fund + 1.0
    print("cleanup prints a lot")
    np.save(
        os.path.join(folder, f"ident_v_cleaned_n{n}.npy"),
        np.where(ident > 0, 0.0, ident),
    )
    np.save(os.path.join(folder, f"fund_v_cleaned_n{n}.npy"), fund)
    np.save(
        os.path.join(folder, f"idx_v_cleaned_n{n}.npy"),
        np.load(os.path.join(folder, "idx_v.npy")),
    )
    if progress is not None:
        progress("done", 1.0)
