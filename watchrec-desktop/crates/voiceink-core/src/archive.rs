use crate::storage;
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use std::{
    fs::{File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::Mutex,
};

pub const PCM_RATE: u64 = 32000;
pub const WAV_HEADER: u64 = 44;
#[derive(Clone, Serialize, Deserialize)]
pub struct Part {
    pub file: String,
    pub bytes: u64,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct Index {
    pub version: u32,
    pub first_file: String,
    pub segment_bytes: u64,
    pub closed: bool,
    #[serde(default)]
    pub recovered_after_interruption: bool,
    pub error: Option<String>,
    pub parts: Vec<Part>,
}
struct State {
    index: Index,
    writer: Option<File>,
    captured: u64,
    committed: u64,
}
pub struct Archive {
    pub directory: PathBuf,
    state: Mutex<State>,
}
pub enum ReadState {
    Samples(Vec<u8>),
    Waiting,
    End,
}
impl Archive {
    pub fn create(directory: &Path) -> Result<Self> {
        Self::create_with_segment(directory, PCM_RATE * 1800)
    }
    pub fn create_with_segment(directory: &Path, segment_bytes: u64) -> Result<Self> {
        ensure!(
            segment_bytes >= 2
                && segment_bytes % 2 == 0
                && segment_bytes < u32::MAX as u64 - WAV_HEADER,
            "Invalid segment size"
        );
        std::fs::create_dir_all(directory)?;
        ensure!(
            !directory.join("recording.json").exists(),
            "录音已存在，不能覆盖"
        );
        let archive = Self {
            directory: directory.to_owned(),
            state: Mutex::new(State {
                index: Index {
                    version: 1,
                    first_file: "麦克风录音.wav".into(),
                    segment_bytes,
                    closed: false,
                    recovered_after_interruption: false,
                    error: None,
                    parts: vec![],
                },
                writer: None,
                captured: 0,
                committed: 0,
            }),
        };
        archive.new_part(&mut archive.state.lock().unwrap())?;
        Ok(archive)
    }
    pub fn recover(directory: &Path) -> Result<Self> {
        let mut index: Index = storage::load(&directory.join("recording.json"))?;
        ensure!(
            index.version == 1 && !index.parts.is_empty(),
            "录音记录格式错误"
        );
        let mut captured = 0;
        for part in &mut index.parts {
            storage::local_name(&part.file)?;
            let mut file = OpenOptions::new()
                .read(true)
                .write(true)
                .open(directory.join(&part.file))?;
            let mut header = [0u8; 44];
            file.read_exact(&mut header)?;
            ensure!(
                &header[0..4] == b"RIFF"
                    && &header[8..16] == b"WAVEfmt "
                    && &header[36..40] == b"data"
                    && u32::from_le_bytes(header[24..28].try_into().unwrap()) == 16000
                    && u16::from_le_bytes(header[22..24].try_into().unwrap()) == 1
                    && u16::from_le_bytes(header[34..36].try_into().unwrap()) == 16,
                "录音分段格式异常"
            );
            let bytes = (file.metadata()?.len() - WAV_HEADER) / 2 * 2;
            ensure!(
                bytes >= part.bytes && bytes <= index.segment_bytes,
                "录音分段长度异常"
            );
            if !index.closed || bytes != part.bytes {
                file.set_len(WAV_HEADER + bytes)?;
                write_header(&mut file, bytes)?;
                file.sync_all()?;
            }
            part.bytes = bytes;
            captured += bytes;
        }
        if !index.closed {
            index.closed = true;
            index.recovered_after_interruption = true;
        }
        storage::save(&directory.join("recording.json"), &index)?;
        Ok(Self {
            directory: directory.to_owned(),
            state: Mutex::new(State {
                index,
                writer: None,
                captured,
                committed: captured,
            }),
        })
    }
    fn new_part(&self, state: &mut State) -> Result<()> {
        let name = if state.index.parts.is_empty() {
            state.index.first_file.clone()
        } else {
            format!("麦克风录音-{:04}.wav", state.index.parts.len() + 1)
        };
        let mut writer = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(self.directory.join(&name))?;
        write_header(&mut writer, 0)?;
        writer.sync_all()?;
        state.writer = Some(writer);
        state.index.parts.push(Part {
            file: name,
            bytes: 0,
        });
        storage::save(&self.directory.join("recording.json"), &state.index)
    }
    fn commit(&self, state: &mut State) -> Result<()> {
        if let Some(writer) = &mut state.writer {
            write_header(writer, state.index.parts.last().unwrap().bytes)?;
            writer.sync_all()?;
        }
        state.committed = state.captured;
        storage::save(&self.directory.join("recording.json"), &state.index)
    }
    pub fn append(&self, pcm: &[u8]) -> Result<()> {
        if pcm.is_empty() {
            return Ok(());
        }
        ensure!(pcm.len() % 2 == 0, "PCM sample is truncated");
        let mut state = self.state.lock().unwrap();
        ensure!(!state.index.closed, "录音已经结束");
        let mut offset = 0;
        while offset < pcm.len() {
            if state.index.parts.last().unwrap().bytes == state.index.segment_bytes {
                self.commit(&mut state)?;
                state.writer.take();
                self.new_part(&mut state)?;
            }
            let count = (pcm.len() - offset).min(
                (state.index.segment_bytes - state.index.parts.last().unwrap().bytes) as usize,
            );
            state
                .writer
                .as_mut()
                .unwrap()
                .write_all(&pcm[offset..offset + count])?;
            state.index.parts.last_mut().unwrap().bytes += count as u64;
            state.captured += count as u64;
            offset += count;
        }
        if state.captured - state.committed >= PCM_RATE / 5 {
            self.commit(&mut state)?;
        }
        Ok(())
    }
    pub fn flush(&self) -> Result<()> {
        self.commit(&mut self.state.lock().unwrap())
    }
    pub fn complete(&self, error: Option<String>) -> Result<()> {
        let mut state = self.state.lock().unwrap();
        if state.index.closed {
            return Ok(());
        }
        state.index.error = error;
        state.index.closed = true;
        let result = self.commit(&mut state);
        state.writer.take();
        result
    }
    pub fn info(&self) -> Index {
        self.state.lock().unwrap().index.clone()
    }
    pub fn captured(&self) -> u64 {
        self.state.lock().unwrap().captured
    }
    pub fn closed(&self) -> bool {
        self.state.lock().unwrap().index.closed
    }
    pub fn read_at(&self, position: u64) -> Result<ReadState> {
        let (path, offset, count) = {
            let state = self.state.lock().unwrap();
            if position >= state.committed {
                return Ok(if state.index.closed {
                    ReadState::End
                } else {
                    ReadState::Waiting
                });
            }
            let mut start = 0;
            let mut found = None;
            for part in &state.index.parts {
                if position < start + part.bytes {
                    let offset = position - start;
                    let count = PCM_RATE
                        .min(state.committed - position)
                        .min(part.bytes - offset) as usize;
                    found = Some((self.directory.join(&part.file), offset, count));
                    break;
                }
                start += part.bytes;
            }
            found.ok_or_else(|| anyhow::anyhow!("录音索引超出分段范围"))?
        };
        let mut file = File::open(path)?;
        file.seek(SeekFrom::Start(WAV_HEADER + offset))?;
        let mut pcm = vec![0; count];
        file.read_exact(&mut pcm)?;
        Ok(ReadState::Samples(pcm))
    }
}
fn write_header(file: &mut File, bytes: u64) -> Result<()> {
    let mut header = Vec::with_capacity(44);
    header.extend(b"RIFF");
    header.extend(((36 + bytes) as u32).to_le_bytes());
    header.extend(b"WAVEfmt ");
    header.extend(16u32.to_le_bytes());
    header.extend(1u16.to_le_bytes());
    header.extend(1u16.to_le_bytes());
    header.extend(16000u32.to_le_bytes());
    header.extend(32000u32.to_le_bytes());
    header.extend(2u16.to_le_bytes());
    header.extend(16u16.to_le_bytes());
    header.extend(b"data");
    header.extend((bytes as u32).to_le_bytes());
    file.seek(SeekFrom::Start(0))?;
    file.write_all(&header)?;
    file.seek(SeekFrom::Start(WAV_HEADER + bytes))?;
    Ok(())
}
impl Drop for Archive {
    fn drop(&mut self) {
        let _ = self.complete(None);
    }
}
