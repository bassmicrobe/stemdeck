use super::{
    gate_gain, visit_samples, wav_metadata, write_transformed, AudioAnalysis, FilePair, FileResult,
    GateSettings, StabilizeSettings,
};
use std::fs;

struct GatedStatistics {
    sums: Vec<f64>,
    squares: Vec<f64>,
    counts: Vec<u64>,
    minima: Vec<f32>,
    maxima: Vec<f32>,
    bin_minima: Vec<f32>,
    bin_maxima: Vec<f32>,
    bins: usize,
    chunk: u64,
}

impl GatedStatistics {
    fn new(channels: usize, frames: u64, bins: Option<usize>) -> Self {
        let bins = bins.unwrap_or(2048).clamp(1, 8192);
        let chunk = frames.div_ceil(bins as u64).max(1);
        Self {
            sums: vec![0.0; channels],
            squares: vec![0.0; channels],
            counts: vec![0; channels],
            minima: vec![f32::INFINITY; channels],
            maxima: vec![f32::NEG_INFINITY; channels],
            bin_minima: vec![f32::INFINITY; bins * channels],
            bin_maxima: vec![f32::NEG_INFINITY; bins * channels],
            bins,
            chunk,
        }
    }

    fn observe(&mut self, frame: usize, channel: usize, channels: usize, sample: f32) {
        self.sums[channel] += f64::from(sample);
        self.squares[channel] += f64::from(sample) * f64::from(sample);
        self.counts[channel] += 1;
        self.minima[channel] = self.minima[channel].min(sample);
        self.maxima[channel] = self.maxima[channel].max(sample);
        let bin = ((frame as u64) / self.chunk).min((self.bins - 1) as u64) as usize;
        let index = (bin * channels) + channel;
        self.bin_minima[index] = self.bin_minima[index].min(sample);
        self.bin_maxima[index] = self.bin_maxima[index].max(sample);
    }
}

fn gate_envelope(
    pair: &FilePair,
    sample_rate: u32,
    frames: u64,
    settings: &GateSettings,
) -> Result<(Vec<f32>, u64), String> {
    let frame_len = ((u64::from(sample_rate) * u64::from(settings.window_ms)) / 1000).max(64);
    let envelope_len = frames.div_ceil(frame_len) as usize;
    let mut sums = vec![0.0_f64; envelope_len];
    let mut counts = vec![0_u64; envelope_len];
    visit_samples(&pair.input, |frame, _channel, sample| {
        let index = ((frame as u64) / frame_len).min((envelope_len - 1) as u64) as usize;
        sums[index] += f64::from(sample) * f64::from(sample);
        counts[index] += 1;
    })?;
    let threshold = 10.0_f32.powf(settings.threshold_db / 20.0);
    let active = sums
        .iter()
        .zip(counts)
        .map(|(sum, count)| (*sum / count.max(1) as f64).sqrt() as f32 >= threshold)
        .collect::<Vec<_>>();
    Ok((
        gate_gain(
            &active,
            settings.window_ms,
            settings.hold_ms,
            settings.attack_ms,
            settings.release_ms,
        ),
        frame_len,
    ))
}

fn frame_gain(gains: &[f32], frame_len: u64, frame: usize) -> f32 {
    let index = ((frame as u64) / frame_len).min((gains.len() - 1) as u64) as usize;
    gains[index]
}

fn final_analysis(
    pair: &FilePair,
    sample_rate: u32,
    channels: u16,
    frames: u64,
    stats: &GatedStatistics,
    dc: &[f32],
    gain: f32,
) -> AudioAnalysis {
    let channel_count = usize::from(channels);
    let used_bins = frames.div_ceil(stats.chunk).min(stats.bins as u64) as usize;
    let mut min_max_peaks = Vec::with_capacity(used_bins);
    let mut waveform_peaks = Vec::with_capacity(used_bins);
    let mut peak = 0.0_f32;
    for bin in 0..used_bins {
        let mut minimum = 0.0_f32;
        let mut maximum = 0.0_f32;
        for (channel, dc_value) in dc.iter().copied().enumerate().take(channel_count) {
            let index = (bin * channel_count) + channel;
            if stats.bin_minima[index].is_finite() {
                minimum = minimum.min((stats.bin_minima[index] - dc_value) * gain);
                maximum = maximum.max((stats.bin_maxima[index] - dc_value) * gain);
            }
        }
        peak = peak.max(minimum.abs()).max(maximum.abs());
        waveform_peaks.push(minimum.abs().max(maximum.abs()));
        min_max_peaks.push([minimum, maximum]);
    }

    let total_squares = (0..channel_count)
        .map(|channel| {
            let centered = stats.squares[channel]
                - (2.0 * f64::from(dc[channel]) * stats.sums[channel])
                + (stats.counts[channel] as f64 * f64::from(dc[channel]).powi(2));
            centered.max(0.0) * f64::from(gain).powi(2)
        })
        .sum::<f64>();
    let sample_count = stats.counts.iter().sum::<u64>();
    let rms = if sample_count == 0 {
        0.0
    } else {
        (total_squares / sample_count as f64).sqrt() as f32
    };
    AudioAnalysis {
        path: pair.input.display().to_string(),
        sample_rate,
        channels,
        duration_seconds: frames as f64 / f64::from(sample_rate),
        peak,
        rms,
        waveform_peaks,
        min_max_peaks,
    }
}

pub(super) fn process_wav(
    pair: &FilePair,
    gate: Option<&GateSettings>,
    stabilize: Option<&StabilizeSettings>,
    bins: Option<usize>,
) -> Result<(FileResult, AudioAnalysis), String> {
    let (spec, frames) = wav_metadata(&pair.input)?;
    if frames == 0 {
        return Ok((
            FileResult {
                path: pair.input.display().to_string(),
                output: pair.output.display().to_string(),
                changed: false,
                gate_applied: false,
                stabilized: false,
            },
            super::analyze_wav_file(pair.input.clone(), bins)?,
        ));
    }

    let (gate_gains, gate_frame_len) = if let Some(settings) = gate {
        gate_envelope(pair, spec.sample_rate, frames, settings)?
    } else {
        (vec![1.0], frames.max(1))
    };
    let gate_applied = gate_gains.iter().any(|value| *value < 0.999);
    let channels = usize::from(spec.channels);
    let mut stats = GatedStatistics::new(channels, frames, bins);
    visit_samples(&pair.input, |frame, channel, sample| {
        let gated = sample * frame_gain(&gate_gains, gate_frame_len, frame);
        stats.observe(frame, channel, channels, gated);
    })?;

    let measured_dc = stats
        .sums
        .iter()
        .zip(&stats.counts)
        .map(|(sum, count)| (*sum / (*count).max(1) as f64) as f32)
        .collect::<Vec<_>>();
    let needs_dc = stabilize.is_some_and(|settings| {
        measured_dc
            .iter()
            .any(|value| value.abs() > settings.dc_threshold)
    });
    let dc = if needs_dc {
        measured_dc
    } else {
        vec![0.0; channels]
    };
    let centered_peak = (0..channels).fold(0.0_f32, |peak, channel| {
        peak.max((stats.minima[channel] - dc[channel]).abs())
            .max((stats.maxima[channel] - dc[channel]).abs())
    });
    let gain = stabilize
        .filter(|settings| centered_peak > settings.peak)
        .map_or(1.0, |settings| settings.peak / centered_peak);
    let stabilized = needs_dc || gain < 0.9999;
    let changed = gate_applied || stabilized;
    if changed {
        write_transformed(&pair.input, &pair.output, |frame, channel, sample| {
            let gated = sample * frame_gain(&gate_gains, gate_frame_len, frame);
            (gated - dc[channel]) * gain
        })?;
    } else if let Err(error) = fs::remove_file(&pair.output) {
        if error.kind() != std::io::ErrorKind::NotFound {
            return Err(format!("failed to remove stale PCM output: {error}"));
        }
    }

    let analysis = final_analysis(
        pair,
        spec.sample_rate,
        spec.channels,
        frames,
        &stats,
        &dc,
        gain,
    );
    Ok((
        FileResult {
            path: pair.input.display().to_string(),
            output: pair.output.display().to_string(),
            changed,
            gate_applied,
            stabilized,
        },
        analysis,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pcm::{analyze_wav_file, GateSettings, StabilizeSettings};
    use hound::{SampleFormat, WavSpec, WavWriter};
    use std::path::PathBuf;
    use tempfile::tempdir;

    fn write_float(path: &PathBuf, samples: &[[f32; 2]], sample_rate: u32) {
        let spec = WavSpec {
            channels: 2,
            sample_rate,
            bits_per_sample: 32,
            sample_format: SampleFormat::Float,
        };
        let mut writer = WavWriter::create(path, spec).unwrap();
        for frame in samples {
            writer.write_sample(frame[0]).unwrap();
            writer.write_sample(frame[1]).unwrap();
        }
        writer.finalize().unwrap();
    }

    fn write_integer(path: &PathBuf, bits: u16) {
        let spec = WavSpec {
            channels: 1,
            sample_rate: 8_000,
            bits_per_sample: bits,
            sample_format: SampleFormat::Int,
        };
        let mut writer = WavWriter::create(path, spec).unwrap();
        if bits == 16 {
            writer.write_sample(i16::MAX / 2).unwrap();
            writer.write_sample(i16::MIN / 2).unwrap();
        } else {
            let amplitude = (1_i32 << (bits - 2)) - 1;
            writer.write_sample(amplitude).unwrap();
            writer.write_sample(-amplitude).unwrap();
        }
        writer.finalize().unwrap();
    }

    #[test]
    fn process_fuses_gate_stabilize_and_analysis_in_one_output() {
        let dir = tempdir().unwrap();
        let input = dir.path().join("input.wav");
        let output = dir.path().join("output.wav");
        let samples = (0..200)
            .map(|index| {
                if index < 100 {
                    [0.0001, 0.0001]
                } else if index % 2 == 0 {
                    [1.3, 1.3]
                } else {
                    [-0.7, -0.7]
                }
            })
            .collect::<Vec<_>>();
        write_float(&input, &samples, 100);
        let pair = FilePair {
            input: input.clone(),
            output: output.clone(),
        };
        let gate = GateSettings {
            threshold_db: -40.0,
            window_ms: 100,
            hold_ms: 0,
            attack_ms: 1,
            release_ms: 1,
        };
        let stabilize = StabilizeSettings {
            peak: 0.98,
            dc_threshold: 1e-5,
        };

        let (result, analysis) =
            process_wav(&pair, Some(&gate), Some(&stabilize), Some(20)).unwrap();

        assert!(result.changed);
        assert!(result.gate_applied);
        assert!(result.stabilized);
        assert_eq!(analysis.path, input.display().to_string());
        assert_eq!(analysis.duration_seconds, 2.0);
        assert!(analysis.peak <= 0.981);
        assert!(analysis.min_max_peaks[0][1] < 1e-6);
        assert!(output.is_file());
    }

    #[test]
    fn analyze_supports_pcm_16_24_and_32_bit_inputs() {
        let dir = tempdir().unwrap();
        for bits in [16, 24, 32] {
            let path = dir.path().join(format!("pcm-{bits}.wav"));
            write_integer(&path, bits);

            let analysis = analyze_wav_file(path, Some(1)).unwrap();

            assert_eq!(analysis.channels, 1);
            assert!((analysis.peak - 0.5).abs() < 0.001);
        }
    }

    #[test]
    fn process_write_failure_preserves_input() {
        let dir = tempdir().unwrap();
        let input = dir.path().join("input.wav");
        let blocked_parent = dir.path().join("not-a-directory");
        write_float(&input, &[[1.2, -1.2]; 10], 100);
        fs::write(&blocked_parent, b"file").unwrap();
        let pair = FilePair {
            input: input.clone(),
            output: blocked_parent.join("output.wav"),
        };
        let stabilize = StabilizeSettings {
            peak: 0.98,
            dc_threshold: 1e-5,
        };

        let error = process_wav(&pair, None, Some(&stabilize), Some(1)).unwrap_err();

        assert!(error.contains("output directory") || error.contains("create WAV"));
        assert!(input.is_file());
        assert!((analyze_wav_file(input, Some(1)).unwrap().peak - 1.2).abs() < 1e-6);
    }
}
