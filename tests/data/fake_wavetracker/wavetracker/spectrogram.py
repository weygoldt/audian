"""Stub of wavetracker.spectrogram."""


def step_size(nfft, overlap_frac):
    return max(1, int(nfft * (1.0 - overlap_frac)))
