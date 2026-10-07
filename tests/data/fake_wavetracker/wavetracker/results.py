"""Stub of wavetracker.results."""

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class Results:
    fund_v: np.ndarray
    idx_v: np.ndarray
    sign_v: np.ndarray
    ident_v: np.ndarray
    times: np.ndarray
    meta: dict = field(default_factory=dict)

    @property
    def n_ids(self):
        return int(np.unique(self.ident_v[~np.isnan(self.ident_v)]).size)

    def save(self, folder):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        for name in ("fund_v", "idx_v", "sign_v", "ident_v", "times"):
            np.save(folder / f"{name}.npy", getattr(self, name))
        (folder / "wavetracker.json").write_text(json.dumps(self.meta))
