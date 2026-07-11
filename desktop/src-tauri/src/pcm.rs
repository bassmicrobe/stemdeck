use hound::{SampleFormat, WavReader, WavSpec, WavWriter};
use serde::{Deserialize, Serialize};
use std::{fs, path::PathBuf};

const ENGINE: &str = "layerlab-rust-pcm-v1";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AudioAnalysis {
    pub path: String,
    pub sample_rate: u32,
    pub channels: u16,
    pub duration_seconds: f64,
    pub peak: f32,
    pub rms: f32,
    pub waveform_peaks: Vec<f32>,
    pub min_max_peaks: Vec<[f32; 2]>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FilePair {
    pub input: PathBuf,
    pub output: PathBuf,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FileResult {
    pub path: String,
    pub output: String,
    pub changed: bool,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "operation", rename_all = "snake_case")]
pub enum PcmCommand {
    Ping,
    Analyze {
        paths: Vec<PathBuf>,
        bins: Option<usize>,
    },
    Gate {
        files: Vec<FilePair>,
        threshold_db: f32,
        window_ms: u32,
        hold_ms: u32,
        attack_ms: u32,
        release_ms: u32,
    },
    Stabilize {
        files: Vec<FilePair>,
        peak: f32,
        dc_threshold: f32,
    },
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PcmResponse {
    pub ok: bool,
    pub engine: String,
    #[serde(skip_serializing_if = "Vec::is_empty", default)]
    pub files: Vec<FileResult>,
    #[serde(skip_serializing_if = "Vec::is_empty", default)]
    pub analyses: Vec<AudioAnalysis>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

impl PcmResponse {
    pub fn error(error: String) -> Self {
        Self {
            ok: false,
            engine: ENGINE.to_string(),
            files: Vec::new(),
            analyses: Vec::new(),
            error: Some(error),
        }
    }

    fn success(files: Vec<FileResult>, analyses: Vec<AudioAnalysis>) -> Self {
        Self {
            ok: true,
            engine: ENGINE.to_string(),
            files,
            analyses,
            error: None,
        }
    }
}

pub fn execute(command: PcmCommand) -> Result<PcmResponse, String> {
    match command {
        PcmCommand::Ping => Ok(PcmResponse::success(Vec::new(), Vec::new())),
        PcmCommand::Analyze { paths, bins } => {
            if paths.is_empty() {
                return Err("analyze requires at least one path".to_string());
            }
            let analyses = paths
                .iter()
                .map(|path| analyze_wav_file(path.clone(), bins))
                .collect::<Result<Vec<_>, _>>()?;
            Ok(PcmResponse::success(Vec::new(), analyses))
        }
        PcmCommand::Gate {
            files,
            threshold_db,
            window_ms,
            hold_ms,
            attack_ms,
            release_ms,
        } => {
            validate_pairs(&files)?;
            let results = files
                .iter()
                .map(|pair| {
                    let changed = gate_wav(
                        pair,
                        threshold_db,
                        window_ms,
                        hold_ms,
                        attack_ms,
                        release_ms,
                    )?;
                    Ok(file_result(pair, changed))
                })
                .collect::<Result<Vec<_>, String>>()?;
            Ok(PcmResponse::success(results, Vec::new()))
        }
        PcmCommand::Stabilize {
            files,
            peak,
            dc_threshold,
        } => {
            validate_pairs(&files)?;
            if !(0.0..=1.0).contains(&peak) || peak <= 0.0 {
                return Err("stabilize peak must be within (0, 1]".to_string());
            }
            let results = files
                .iter()
                .map(|pair| {
                    let changed = stabilize_wav(pair, peak, dc_threshold.max(0.0))?;
                    Ok(file_result(pair, changed))
                })
                .collect::<Result<Vec<_>, String>>()?;
            Ok(PcmResponse::success(results, Vec::new()))
        }
    }
}

fn file_result(pair: &FilePair, changed: bool) -> FileResult {
    FileResult {
        path: pair.input.display().to_string(),
        output: pair.output.display().to_string(),
        changed,
    }
}

fn validate_pairs(files: &[FilePair]) -> Result<(), String> {
    if files.is_empty() {
        return Err("operation requires at least one file".to_string());
    }
    for pair in files {
        if pair.input == pair.output {
            return Err("input and output paths must differ".to_string());
        }
        if !pair.input.is_file() {
            return Err(format!(
                "input WAV does not exist: {}",
                pair.input.display()
            ));
        }
    }
    Ok(())
}

pub fn analyze_wav_file(path: PathBuf, bins: Option<usize>) -> Result<AudioAnalysis, String> {
    let (spec, frames) = wav_metadata(&path)?;
    let bin_count = bins.unwrap_or(2048).clamp(1, 8192);
    let chunk = frames.div_ceil(bin_count as u64).max(1);
    let mut minima = vec![0.0_f32; bin_count];
    let mut maxima = vec![0.0_f32; bin_count];
    let mut waveform = vec![0.0_f32; bin_count];
    let mut peak = 0.0_f32;
    let mut squares = 0.0_f64;
    let mut sample_count = 0_u64;

    visit_samples(&path, |frame, _channel, sample| {
        let bin = ((frame as u64) / chunk).min((bin_count - 1) as u64) as usize;
        minima[bin] = minima[bin].min(sample);
        maxima[bin] = maxima[bin].max(sample);
        waveform[bin] = waveform[bin].max(sample.abs());
        peak = peak.max(sample.abs());
        squares += f64::from(sample) * f64::from(sample);
        sample_count += 1;
    })?;

    let used_bins = frames.div_ceil(chunk).min(bin_count as u64) as usize;
    minima.truncate(used_bins);
    maxima.truncate(used_bins);
    waveform.truncate(used_bins);
    let min_max_peaks = minima
        .into_iter()
        .zip(maxima)
        .map(|(min, max)| [min, max])
        .collect();
    let rms = if sample_count == 0 {
        0.0
    } else {
        (squares / sample_count as f64).sqrt() as f32
    };
    Ok(AudioAnalysis {
        path: path.display().to_string(),
        sample_rate: spec.sample_rate,
        channels: spec.channels,
        duration_seconds: frames as f64 / f64::from(spec.sample_rate),
        peak,
        rms,
        waveform_peaks: waveform,
        min_max_peaks,
    })
}

fn gate_wav(
    pair: &FilePair,
    threshold_db: f32,
    window_ms: u32,
    hold_ms: u32,
    attack_ms: u32,
    release_ms: u32,
) -> Result<bool, String> {
    let (spec, frames) = wav_metadata(&pair.input)?;
    if frames == 0 {
        return Ok(false);
    }
    let window_ms = window_ms.max(1);
    let frame_len = ((u64::from(spec.sample_rate) * u64::from(window_ms)) / 1000).max(64);
    let envelope_len = frames.div_ceil(frame_len) as usize;
    let mut sums = vec![0.0_f64; envelope_len];
    let mut counts = vec![0_u64; envelope_len];
    visit_samples(&pair.input, |frame, _channel, sample| {
        let index = ((frame as u64) / frame_len).min((envelope_len - 1) as u64) as usize;
        sums[index] += f64::from(sample) * f64::from(sample);
        counts[index] += 1;
    })?;
    let threshold = 10.0_f32.powf(threshold_db / 20.0);
    let active = sums
        .iter()
        .zip(counts)
        .map(|(sum, count)| (*sum / count.max(1) as f64).sqrt() as f32 >= threshold)
        .collect::<Vec<_>>();
    let gain = gate_gain(&active, window_ms, hold_ms, attack_ms, release_ms);
    if gain.iter().all(|value| *value >= 0.999) {
        return Ok(false);
    }
    write_transformed(&pair.input, &pair.output, |frame, _channel, sample| {
        let index = ((frame as u64) / frame_len).min((gain.len() - 1) as u64) as usize;
        sample * gain[index]
    })?;
    Ok(true)
}

fn gate_gain(
    active: &[bool],
    window_ms: u32,
    hold_ms: u32,
    attack_ms: u32,
    release_ms: u32,
) -> Vec<f32> {
    let mut expanded = active.to_vec();
    let hold = hold_ms.div_ceil(window_ms) as usize;
    for (start, end) in true_runs(active) {
        let from = start.saturating_sub(hold);
        let to = (end + hold).min(active.len());
        expanded[from..to].fill(true);
    }
    let attack = attack_ms.div_ceil(window_ms).max(1) as usize;
    let release = release_ms.div_ceil(window_ms).max(1) as usize;
    let mut gain = vec![0.0_f32; active.len()];
    for (start, end) in true_runs(&expanded) {
        gain[start..end].fill(1.0);
        let attack_start = start.saturating_sub(attack);
        let attack_len = start - attack_start;
        for offset in 0..attack_len {
            gain[attack_start + offset] =
                gain[attack_start + offset].max(offset as f32 / attack_len.max(1) as f32);
        }
        let release_end = (end + release).min(gain.len());
        let release_len = release_end - end;
        for offset in 0..release_len {
            gain[end + offset] =
                gain[end + offset].max(1.0 - (offset as f32 / release_len.max(1) as f32));
        }
    }
    gain
}

fn true_runs(mask: &[bool]) -> Vec<(usize, usize)> {
    let mut runs = Vec::new();
    let mut start = None;
    for (index, value) in mask
        .iter()
        .copied()
        .chain(std::iter::once(false))
        .enumerate()
    {
        match (start, value) {
            (None, true) => start = Some(index),
            (Some(run_start), false) => {
                runs.push((run_start, index));
                start = None;
            }
            _ => {}
        }
    }
    runs
}

fn stabilize_wav(pair: &FilePair, limit: f32, dc_threshold: f32) -> Result<bool, String> {
    let (spec, frames) = wav_metadata(&pair.input)?;
    if frames == 0 {
        return Ok(false);
    }
    let mut sums = vec![0.0_f64; usize::from(spec.channels)];
    let mut raw_peak = 0.0_f32;
    visit_samples(&pair.input, |_frame, channel, sample| {
        sums[channel] += f64::from(sample);
        raw_peak = raw_peak.max(sample.abs());
    })?;
    let dc = sums
        .iter()
        .map(|sum| (*sum / frames as f64) as f32)
        .collect::<Vec<_>>();
    let needs_dc = dc.iter().any(|value| value.abs() > dc_threshold);
    let peak = if needs_dc {
        let mut centered_peak = 0.0_f32;
        visit_samples(&pair.input, |_frame, channel, sample| {
            centered_peak = centered_peak.max((sample - dc[channel]).abs());
        })?;
        centered_peak
    } else {
        raw_peak
    };
    let gain = if peak > limit { limit / peak } else { 1.0 };
    if !needs_dc && gain >= 0.9999 {
        return Ok(false);
    }
    write_transformed(&pair.input, &pair.output, |_frame, channel, sample| {
        let centered = if needs_dc {
            sample - dc[channel]
        } else {
            sample
        };
        centered * gain
    })?;
    Ok(true)
}

fn wav_metadata(path: &PathBuf) -> Result<(WavSpec, u64), String> {
    let reader = WavReader::open(path)
        .map_err(|error| format!("failed to open WAV {}: {error}", path.display()))?;
    let spec = reader.spec();
    validate_spec(spec)?;
    Ok((spec, u64::from(reader.duration())))
}

fn validate_spec(spec: WavSpec) -> Result<(), String> {
    if spec.channels == 0 || spec.sample_rate == 0 {
        return Err("WAV channels and sample rate must be positive".to_string());
    }
    match (spec.sample_format, spec.bits_per_sample) {
        (SampleFormat::Float, 32) | (SampleFormat::Int, 16 | 24 | 32) => Ok(()),
        _ => Err(format!(
            "unsupported WAV format {:?}/{}-bit",
            spec.sample_format, spec.bits_per_sample
        )),
    }
}

fn visit_samples<F>(path: &PathBuf, mut visitor: F) -> Result<(), String>
where
    F: FnMut(usize, usize, f32),
{
    let mut reader = WavReader::open(path)
        .map_err(|error| format!("failed to open WAV {}: {error}", path.display()))?;
    let spec = reader.spec();
    validate_spec(spec)?;
    let channels = usize::from(spec.channels);
    match spec.sample_format {
        SampleFormat::Float => {
            for (index, sample) in reader.samples::<f32>().enumerate() {
                let value = sample.map_err(|error| format!("failed to read float WAV: {error}"))?;
                if !value.is_finite() {
                    return Err(format!("WAV contains a non-finite sample at index {index}"));
                }
                visitor(index / channels, index % channels, value);
            }
        }
        SampleFormat::Int if spec.bits_per_sample == 16 => {
            for (index, sample) in reader.samples::<i16>().enumerate() {
                let value = sample.map_err(|error| format!("failed to read PCM16 WAV: {error}"))?;
                visitor(
                    index / channels,
                    index % channels,
                    f32::from(value) / 32768.0,
                );
            }
        }
        SampleFormat::Int => {
            let scale = 2.0_f32.powi(i32::from(spec.bits_per_sample) - 1);
            for (index, sample) in reader.samples::<i32>().enumerate() {
                let value =
                    sample.map_err(|error| format!("failed to read integer WAV: {error}"))?;
                visitor(index / channels, index % channels, value as f32 / scale);
            }
        }
    }
    Ok(())
}

fn write_transformed<F>(input: &PathBuf, output: &PathBuf, mut transform: F) -> Result<(), String>
where
    F: FnMut(usize, usize, f32) -> f32,
{
    if let Some(parent) = output.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("failed to create output directory: {error}"))?;
    }
    let spec = WavReader::open(input)
        .map_err(|error| format!("failed to open WAV {}: {error}", input.display()))?
        .spec();
    validate_spec(spec)?;
    let channels = usize::from(spec.channels);
    let result = (|| {
        let mut writer = WavWriter::create(output, spec)
            .map_err(|error| format!("failed to create WAV {}: {error}", output.display()))?;
        let mut index = 0_usize;
        visit_samples(input, |frame, channel, sample| {
            let value = transform(frame, channel, sample);
            let write_result = match spec.sample_format {
                SampleFormat::Float => writer.write_sample(value),
                SampleFormat::Int if spec.bits_per_sample == 16 => {
                    let value = value.clamp(-1.0, 1.0);
                    let quantized = if value <= -1.0 {
                        i16::MIN
                    } else {
                        (value * f32::from(i16::MAX)).round() as i16
                    };
                    writer.write_sample(quantized)
                }
                SampleFormat::Int => {
                    let value = value.clamp(-1.0, 1.0);
                    let max = ((1_i64 << (spec.bits_per_sample - 1)) - 1) as f32;
                    let min = -(1_i64 << (spec.bits_per_sample - 1));
                    let quantized = if value <= -1.0 {
                        min as i32
                    } else {
                        (value * max).round() as i32
                    };
                    writer.write_sample(quantized)
                }
            };
            if write_result.is_err() {
                index = usize::MAX;
            } else if index != usize::MAX {
                index += 1;
            }
        })?;
        if index == usize::MAX {
            return Err("failed to write WAV sample".to_string());
        }
        if !index.is_multiple_of(channels) {
            return Err("WAV sample count is not channel-aligned".to_string());
        }
        writer
            .finalize()
            .map_err(|error| format!("failed to finalize WAV: {error}"))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(output);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
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

    #[test]
    fn analyze_preserves_stereo_rms_and_min_max_peaks() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("stereo.wav");
        write_float(&path, &[[0.5, -0.5]; 100], 100);

        let analysis = analyze_wav_file(path, Some(10)).unwrap();

        assert_eq!(analysis.channels, 2);
        assert!((analysis.duration_seconds - 1.0).abs() < 1e-6);
        assert!((analysis.rms - 0.5).abs() < 1e-6);
        assert_eq!(analysis.min_max_peaks[0], [-0.5, 0.5]);
    }

    #[test]
    fn stabilize_removes_dc_and_limits_peak_without_shortening() {
        let dir = tempdir().unwrap();
        let input = dir.path().join("input.wav");
        let output = dir.path().join("output.wav");
        let samples = (0..100)
            .map(|index| {
                let signal = if index % 2 == 0 { 1.2 } else { -0.8 };
                [signal, signal]
            })
            .collect::<Vec<_>>();
        write_float(&input, &samples, 100);
        let pair = FilePair {
            input,
            output: output.clone(),
        };

        assert!(stabilize_wav(&pair, 0.98, 1e-5).unwrap());
        let analysis = analyze_wav_file(output, Some(10)).unwrap();
        assert_eq!(analysis.duration_seconds, 1.0);
        assert!(analysis.peak <= 0.981);
        assert!((analysis.rms - 0.98).abs() < 1e-5);
        let mut sum = 0.0_f64;
        let mut count = 0_u64;
        visit_samples(&pair.output, |_frame, _channel, sample| {
            sum += f64::from(sample);
            count += 1;
        })
        .unwrap();
        assert!((sum / count as f64).abs() < 1e-6);
    }

    #[test]
    fn gate_keeps_timeline_and_mutes_silent_windows() {
        let dir = tempdir().unwrap();
        let input = dir.path().join("input.wav");
        let output = dir.path().join("output.wav");
        let samples = (0..200)
            .map(|index| {
                if index < 100 {
                    [0.0001, 0.0001]
                } else {
                    [0.5, 0.5]
                }
            })
            .collect::<Vec<_>>();
        write_float(&input, &samples, 100);
        let pair = FilePair {
            input,
            output: output.clone(),
        };

        assert!(gate_wav(&pair, -40.0, 100, 0, 1, 1).unwrap());
        let analysis = analyze_wav_file(output, Some(20)).unwrap();
        assert_eq!(analysis.duration_seconds, 2.0);
        assert!(analysis.min_max_peaks[0][1] < 1e-6);
        assert!(analysis.peak > 0.49);
    }

    #[test]
    fn gate_preserves_super_unity_float_samples_until_stabilize() {
        let dir = tempdir().unwrap();
        let input = dir.path().join("input.wav");
        let output = dir.path().join("output.wav");
        let samples = (0..200)
            .map(|index| {
                if index < 100 {
                    [0.0001, 0.0001]
                } else {
                    [1.25, -1.25]
                }
            })
            .collect::<Vec<_>>();
        write_float(&input, &samples, 100);
        let pair = FilePair {
            input,
            output: output.clone(),
        };

        assert!(gate_wav(&pair, -40.0, 100, 0, 1, 1).unwrap());
        let analysis = analyze_wav_file(output, Some(20)).unwrap();
        assert!((analysis.peak - 1.25).abs() < 1e-6);
    }

    #[test]
    fn gate_preserves_short_active_tail() {
        let dir = tempdir().unwrap();
        let input = dir.path().join("input.wav");
        let output = dir.path().join("output.wav");
        write_float(&input, &[[0.01, 0.01]], 44_100);
        let pair = FilePair { input, output };

        assert!(!gate_wav(&pair, -54.0, 20, 90, 8, 85).unwrap());
        assert!(!pair.output.exists());
    }

    #[test]
    fn stabilize_limits_peak_after_dc_removal() {
        let dir = tempdir().unwrap();
        let input = dir.path().join("input.wav");
        let output = dir.path().join("output.wav");
        write_float(
            &input,
            &[[-0.6, -0.6], [1.0, 1.0], [1.0, 1.0], [1.0, 1.0]],
            4,
        );
        let pair = FilePair {
            input,
            output: output.clone(),
        };

        assert!(stabilize_wav(&pair, 0.98, 1e-5).unwrap());
        let analysis = analyze_wav_file(output, Some(4)).unwrap();
        assert!(analysis.peak <= 0.981);
    }

    #[test]
    fn analyze_rejects_non_finite_float_samples() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("invalid.wav");
        write_float(&path, &[[f32::NAN, 0.0]], 44_100);

        assert!(analyze_wav_file(path, Some(1))
            .unwrap_err()
            .contains("non-finite sample"));
    }
}
