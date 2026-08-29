#!/usr/bin/env bash
# Capture the acceptance matrix for the Qt6 migration.
#
# Runs the offscreen smoke test across the configurations that exercise
# distinct paint and layout paths, writing one screenshot each.  Called once
# on PyQt5 to record the baseline and again on PySide6 to compare.
#
#   scripts/baseline_matrix.sh <prefix>
#
# e.g. scripts/baseline_matrix.sh .devshots/baseline
#      scripts/baseline_matrix.sh .devshots/qt6
set -u

PREFIX=${1:?usage: baseline_matrix.sh <output-prefix>}
PY=${PY:-.venv/bin/python}
WAV=${WAV:-data/Gryllus_campestris.wav}
export QT_QPA_PLATFORM=offscreen

fail=0

run() {
    name=$1
    shift
    echo "### $name"
    if timeout 900 "$PY" scripts/smoke_test.py "$@" 2>&1 |
        grep -vE '^opened audio' | tail -8; then
        :
    else
        echo "  FAILED"
        fail=1
    fi
}

run "dark + interact + census" "$WAV" --interact --census -o "$PREFIX-dark.png"
run "spectrogram" "$WAV" --spectrogram -o "$PREFIX-spec.png"
run "light theme" "$WAV" --theme light -o "$PREFIX-light.png"
run "activity overview" "$WAV" --activity -o "$PREFIX-activity.png"
run "audio pair" "$WAV" --audio-pair -o "$PREFIX-audiopair.png"
run "goto + window" "$WAV" --goto 5.0 --window 1.0 -o "$PREFIX-zoom.png"
run "empty" --empty -o "$PREFIX-empty.png"

# Qt5 only scales when told to; Qt6 always scales, from the screen's own DPI.
# This machine reports DPR 1.0, so the difference is invisible here and
# invisible to the offscreen suite -- which is exactly why it needs its own
# run.  Every measured pixel constant in the lane layout is downstream of it.
QT_SCALE_FACTOR=2 run "dpr 2" "$WAV" --interact -o "$PREFIX-dpr2.png"

echo "matrix exit=$fail"
exit $fail
