"""Stub of wavetracker.config: two sections, unknown keys rejected."""

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class SpectrogramConfig:
    nfft: int = 256
    overlap_frac: float = 0.5


@dataclass
class HarmonicGroupsConfig:
    min_freq: float = 80.0
    max_freq: float = 2400.0


@dataclass
class Config:
    spectrogram: SpectrogramConfig = field(default_factory=SpectrogramConfig)
    harmonic_groups: HarmonicGroupsConfig = field(default_factory=HarmonicGroupsConfig)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        data = data or {}
        sections = {f.name: f for f in fields(cls)}
        unknown = set(data) - set(sections)
        if unknown:
            raise ValueError(f"Unknown config section(s): {sorted(unknown)}")
        kwargs = {}
        for name, f in sections.items():
            section_cls = f.default_factory
            values = data.get(name) or {}
            bad = set(values) - {sf.name for sf in fields(section_cls)}
            if bad:
                raise ValueError(f"Unknown key(s) in '{name}': {sorted(bad)}")
            kwargs[name] = section_cls(**values)
        return cls(**kwargs)

    @classmethod
    def load(cls, path=None):
        if path is None:
            return cls()
        return cls.from_dict(json.loads(Path(path).read_text()))
