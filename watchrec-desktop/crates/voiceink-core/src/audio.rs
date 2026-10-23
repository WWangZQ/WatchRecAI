use anyhow::{ensure, Context, Result};
use rubato::{
    Resampler, SincFixedIn, SincInterpolationParameters, SincInterpolationType, WindowFunction,
};
use std::{fs::File, path::Path};
use symphonia::core::{
    audio::SampleBuffer,
    codecs::{Decoder, DecoderOptions},
    errors::Error,
    formats::{FormatOptions, FormatReader},
    io::MediaSourceStream,
    meta::MetadataOptions,
    probe::Hint,
};

// Fixed processing blocks make output independent of capture/decode callback sizes.
// Remove the filter delay and flush its tail, retaining exactly the source duration.
pub struct MonoConverter {
    rate: u32,
    channels: usize,
    resampler: Option<SincFixedIn<f32>>,
    buffer: Vec<f32>,
    output: Vec<Vec<f32>>,
    total_input: u64,
    total_output: u64,
    delay: usize,
    finished: bool,
}
impl MonoConverter {
    pub fn new(rate: u32, channels: usize) -> Result<Self> {
        ensure!(
            (8000..=384000).contains(&rate) && (1..=32).contains(&channels),
            "不支持的音频采样格式"
        );
        let resampler = if rate == 16000 {
            None
        } else {
            Some(SincFixedIn::new(
                16000.0 / rate as f64,
                1.0,
                SincInterpolationParameters {
                    sinc_len: 128,
                    f_cutoff: 0.95,
                    interpolation: SincInterpolationType::Cubic,
                    oversampling_factor: 128,
                    window: WindowFunction::BlackmanHarris2,
                },
                1024,
                1,
            )?)
        };
        let delay = resampler.as_ref().map_or(0, |r| r.output_delay());
        let output = resampler
            .as_ref()
            .map_or_else(|| vec![vec![]], |r| r.output_buffer_allocate(true));
        Ok(Self {
            rate,
            channels,
            resampler,
            buffer: Vec::with_capacity(1024),
            output,
            total_input: 0,
            total_output: 0,
            delay,
            finished: false,
        })
    }
    pub fn push(&mut self, interleaved: &[f32]) -> Result<Vec<u8>> {
        ensure!(
            !self.finished && interleaved.len() % self.channels == 0,
            "不完整的音频采样帧"
        );
        let mut pcm = Vec::new();
        for frame in interleaved.chunks_exact(self.channels) {
            let mono = frame
                .iter()
                .map(|v| if v.is_finite() { *v } else { 0.0 })
                .sum::<f32>()
                / self.channels as f32;
            self.total_input += 1;
            if self.resampler.is_none() {
                pcm.extend(sample(mono));
                self.total_output += 1;
            } else {
                self.buffer.push(mono);
                if self.buffer.len() == 1024 {
                    self.process(&mut pcm)?;
                }
            }
        }
        Ok(pcm)
    }
    fn process(&mut self, pcm: &mut Vec<u8>) -> Result<()> {
        let (_, frames) = self.resampler.as_mut().unwrap().process_into_buffer(
            &[&self.buffer],
            &mut self.output,
            None,
        )?;
        self.buffer.clear();
        for &value in &self.output[0][..frames] {
            if self.delay > 0 {
                self.delay -= 1;
                continue;
            }
            if self.total_output < self.total_input * 16000 / self.rate as u64 {
                pcm.extend(sample(value));
                self.total_output += 1;
            }
        }
        Ok(())
    }
    pub fn finish(&mut self) -> Result<Vec<u8>> {
        if self.finished {
            return Ok(vec![]);
        }
        self.finished = true;
        let mut pcm = vec![];
        if self.resampler.is_some() {
            while self.total_output < self.total_input * 16000 / self.rate as u64 {
                self.buffer.resize(1024, 0.0);
                self.process(&mut pcm)?;
            }
        }
        Ok(pcm)
    }
}
fn sample(value: f32) -> [u8; 2] {
    ((value * 32768.0).round().clamp(-32768.0, 32767.0) as i16).to_le_bytes()
}

pub struct AudioFile {
    format: Box<dyn FormatReader>,
    decoder: Box<dyn Decoder>,
    track: u32,
    converter: Option<MonoConverter>,
    rate: u32,
    channels: usize,
    finished: bool,
    pub duration: Option<f64>,
}
impl AudioFile {
    pub fn open(path: &Path) -> Result<Self> {
        let mut hint = Hint::new();
        if let Some(extension) = path.extension().and_then(|e| e.to_str()) {
            hint.with_extension(extension);
        }
        let stream = MediaSourceStream::new(Box::new(File::open(path)?), Default::default());
        let probed = symphonia::default::get_probe().format(
            &hint,
            stream,
            &FormatOptions::default(),
            &MetadataOptions::default(),
        )?;
        let format = probed.format;
        let track = format.default_track().context("文件没有可解码的音轨")?;
        let duration = track
            .codec_params
            .n_frames
            .zip(track.codec_params.sample_rate)
            .map(|(n, rate)| n as f64 / rate as f64);
        let decoder = symphonia::default::get_codecs()
            .make(&track.codec_params, &DecoderOptions::default())?;
        let id = track.id;
        Ok(Self {
            format,
            decoder,
            track: id,
            converter: None,
            rate: 0,
            channels: 0,
            finished: false,
            duration,
        })
    }
    pub fn read(&mut self) -> Result<Option<Vec<u8>>> {
        if self.finished {
            return Ok(None);
        }
        loop {
            let packet = match self.format.next_packet() {
                Ok(packet) => packet,
                Err(Error::IoError(e)) if e.kind() == std::io::ErrorKind::UnexpectedEof => {
                    self.finished = true;
                    let tail = if let Some(converter) = &mut self.converter {
                        converter.finish()?
                    } else {
                        vec![]
                    };
                    return Ok(if tail.is_empty() { None } else { Some(tail) });
                }
                Err(e) => return Err(e).context("无法完整读取音频，原文件副本仍保留"),
            };
            if packet.track_id() != self.track {
                continue;
            }
            let audio = self
                .decoder
                .decode(&packet)
                .context("音频解码失败，原文件副本仍保留")?;
            let spec = *audio.spec();
            if self.converter.is_none() {
                self.rate = spec.rate;
                self.channels = spec.channels.count();
                self.converter = Some(MonoConverter::new(self.rate, self.channels)?);
            }
            ensure!(
                self.rate == spec.rate && self.channels == spec.channels.count(),
                "音轨中途改变格式，未丢弃后续音频，请转换容器后重试"
            );
            let mut buffer = SampleBuffer::<f32>::new(audio.capacity() as u64, spec);
            buffer.copy_interleaved_ref(audio);
            let output = self.converter.as_mut().unwrap().push(buffer.samples())?;
            if !output.is_empty() {
                return Ok(Some(output));
            }
        }
    }
}

