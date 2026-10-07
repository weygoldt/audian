"""Stub of wavetracker.pipeline: a tiny results directory, three progress
calls, a print (which must not reach the protocol channel)."""

import logging
import os
import time
from dataclasses import dataclass, field

import numpy as np

from .results import Results

log = logging.getLogger(__name__)


@dataclass
class Timings:
    read: float = 0.0
    total: float = 0.0


@dataclass
class DetectionOutput:
    results: Results
    timings: Timings = field(default_factory=Timings)


def detect(input_path, output_dir, cfg, start=0.0, duration=None, device="auto",
           progress=None):  # fmt: skip
    print("stub detect says hello on stdout")
    log.info("stub detect on %s", input_path)
    os.makedirs(output_dir, exist_ok=True)
    np.save(os.path.join(output_dir, "sparse_spectra.npy"), np.zeros((2, 2)))
    if os.environ.get("FAKE_WT_RAISE"):
        raise RuntimeError(os.environ["FAKE_WT_RAISE"])
    sleep = float(os.environ.get("FAKE_WT_SLEEP", "0"))
    for done in (10, 20, 30):
        if progress:
            progress(done, 30)
        time.sleep(sleep)
    n = 6
    res = Results(
        fund_v=np.linspace(500.0, 510.0, n),
        idx_v=np.arange(n, dtype=np.int64),
        sign_v=np.ones((n, 2), np.float32),
        ident_v=np.full(n, np.nan),
        times=np.arange(30) * 0.128 + 0.128,
        meta={
            "rate": 1000.0,
            "input": str(input_path),
            "start": start,
            "config": cfg.to_dict(),
        },  # fmt: skip
    )
    res.save(output_dir)
    return DetectionOutput(res, Timings(0.01, 0.02))


def track_results(results, cfg):
    results.ident_v = np.array([0.0, 0.0, 0.0, 1.0, 1.0, np.nan])
    return 0.001
