"""A stub of `wavetracker` for the runner tests: no torch, no numba.

Behaviour is steered by environment variables read at call time:

* ``FAKE_WT_SLEEP``: seconds to sleep after each progress call in `detect`;
* ``FAKE_WT_RAISE``: `detect` raises RuntimeError with this message;
* ``FAKE_WT_PROTOCOL``: not read here; see the runner tests.
"""

__version__ = "0.0.0-stub"
