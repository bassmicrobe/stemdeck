# Music-analysis OSS review

LayerLab reviewed the following GitHub projects before adding or excluding
music-analysis dependencies. This is a practical compatibility record, not
legal advice. Final distributors must still preserve the exact license files
and notices from the versions they ship.

## Integrated

| Project | Purpose in LayerLab | License | Integration |
| --- | --- | --- | --- |
| [Beat This!](https://github.com/CPJKU/beat_this) | Neural beat and downbeat detection | MIT for source code and published model weights | All quality profiles use the accurate `final0` checkpoint. Failure or offline setup falls back to librosa. |
| [music21](https://github.com/cuthbertLab/music21) | Standard MIDI validation and harmonic analysis | BSD-3-Clause | Produces key, pitch-range, duration, confidence, and Roman-numeral summaries in `midi-analysis.json`. |
| [mir_eval](https://github.com/mir-evaluation/mir_eval) | Ground-truth chord benchmark metrics | MIT | Development-only dependency. Reports root, major/minor, triad, and tetrad WCSR plus segmentation metrics from labelled `.lab` files; it is not bundled in release runtimes. |

MIT and BSD-3-Clause are permissive licenses compatible with this repository's
Apache-2.0 distribution when their copyright and license notices are retained.
The projects remain copyrighted and licensed by their respective authors.

## Runtime audio infrastructure

| Project | Purpose in LayerLab | License | Integration |
| --- | --- | --- | --- |
| [Hound](https://github.com/ruuda/hound) | WAV/PCM reading and writing | Apache-2.0 | The bundled Rust `layerlab-pcm` sidecar performs timeline-preserving gating, float32 DC/peak stabilization, RMS analysis, and waveform peak generation. Python/soundfile remains the fallback. |

The release license generator reads the exact Cargo lockfile and includes
Hound's Apache-2.0 license text and version in the generated third-party bundle.

## Evaluated but not integrated

| Project | License / constraint | Decision |
| --- | --- | --- |
| [chord-extractor](https://github.com/ohollo/chord-extractor), which wraps Chordino | GPL-2.0 | Not embedded because distributing a combined application would add reciprocal GPL obligations that do not match the current packaging plan. |
| [Sheet Sage](https://github.com/chrisdonahue/sheetsage) | Code is MIT, but released transcription model weights are CC BY-NC-SA 3.0 | Not integrated because the published models restrict commercial use. |
| [Basic Pitch](https://github.com/spotify/basic-pitch) | Apache-2.0 | License-compatible, but its current TensorFlow/CoreML dependency matrix does not support LayerLab's full Python 3.10-3.13 desktop runtime cleanly. Revisit as an isolated optional worker. |
| [madmom](https://github.com/CPJKU/madmom) | BSD-3-Clause code; bundled data/models are CC BY-NC-SA 4.0 | Not integrated because its published model/data terms are not suitable for a commercial-capable default runtime. |
| [Omnizart](https://github.com/Music-and-Culture-Technology-Lab/omnizart) | MIT | Not integrated because upstream documents no ARM macOS support, which excludes LayerLab's primary Apple Silicon target. |
| [lv-chordia](https://github.com/openmirlab/lv-chordia) | MIT | A real-audio trial was slower than the current chroma path and returned no usable chords, so it was not added as a quality upgrade. |
| [BTC-ISMIR19](https://github.com/jayg996/BTC-ISMIR19) | MIT | The architecture is useful research, but the public repository does not provide a stable, desktop-ready checkpoint distribution path for reproducible packaging. No unverified or mock checkpoint is bundled. |

## Runtime behavior

- `STEMDECK_BEAT_TRACKER=auto` enables Beat This! `final0` for all profiles.
- `STEMDECK_BEAT_TRACKER=beat_this` forces neural beat tracking for all
  profiles.
- `STEMDECK_BEAT_TRACKER=librosa` disables neural tracking.
- `STEMDECK_BEAT_THIS_MODEL=<name-or-path>` overrides the profile-selected
  Beat This! checkpoint.
- Model download or inference failure never fails the audio job; LayerLab logs
  the reason and uses the existing librosa beat tracker.

The Beat This! authors license their code and published weights under MIT, but
also note that some training files were copyrighted or had limited Creative
Commons terms. Commercial distributors should include that upstream caveat in
their own legal review rather than treating the weight license as a warranty.

## Demucs code and pretrained weights

Demucs source code is MIT-licensed. Its published pretrained weights are a
separate artifact. In official repository discussions, a maintainer states that
the weights are not covered by MIT and are provided for personal/research use
because the MUSDB training data is research-restricted:

- https://github.com/facebookresearch/demucs/issues/327#issuecomment-1134828611
- https://github.com/facebookresearch/demucs/issues/384#issuecomment-1262197483

LayerLab does not bundle the weights, but the normal first separation downloads
them. A commercial distributor must not treat the code's MIT license as a
commercial license for those weights. Obtain suitable permission, replace the
model, or disable the default weight download.
