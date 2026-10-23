
use std::{fs, path::{Path, PathBuf}};
use voiceink_core::{engine::Engine, storage, transcription::Job};
struct Temp(PathBuf);
impl Temp { fn new() -> Self { let p=std::env::temp_dir().join(format!("watchrec-test-{}",uuid::Uuid::new_v4()));fs::create_dir_all(&p).unwrap();Self(p) } }
impl Drop for Temp { fn drop(&mut self) { let _=fs::remove_dir_all(&self.0); } }
fn wav(path: &Path) {
    let n=32000u32;let mut b=Vec::new();b.extend(b"RIFF");b.extend((36+n).to_le_bytes());b.extend(b"WAVEfmt ");b.extend(16u32.to_le_bytes());b.extend(1u16.to_le_bytes());b.extend(1u16.to_le_bytes());b.extend(16000u32.to_le_bytes());b.extend(32000u32.to_le_bytes());b.extend(2u16.to_le_bytes());b.extend(16u16.to_le_bytes());b.extend(b"data");b.extend(n.to_le_bytes());b.resize(44+n as usize,0);fs::write(path,b).unwrap();
}
#[test]
fn legacy_import_retains_text_copies_audio_and_is_idempotent() {
    let dir=Temp::new();let old=dir.0.join("old");fs::create_dir(&old).unwrap();let audio=old.join("2026-01-01_10-00-00.wav");wav(&audio);
    storage::save(&audio.with_extension("json"), &serde_json::json!({"transcript":"已有原文", "full_text":"已有全文", "summary":"摘要", "title":"自定标题", "recorded_at":"2026-01-01T10:00:00+08:00"})).unwrap();
    wav(&old.join("pending.wav"));
    let data=dir.0.join("app");let engine=Engine::open(&data).unwrap();
    let report=engine.import_watchrec_fresh(&old).unwrap();assert_eq!(report.imported,2);assert!(report.errors.is_empty());
    let report=engine.import_watchrec_fresh(&old).unwrap();assert_eq!((report.imported,report.existing),(0,2));
    let jobs=engine.all_jobs();let done=jobs.iter().find(|j|j.finished()).unwrap();assert_eq!(done.checkpoint().raw,"已有原文");assert_eq!(done.info().custom_title.as_deref(),Some("自定标题"));
    let ai:serde_json::Value=storage::load(&done.directory.join("enrichment.json")).unwrap();assert_eq!(ai["full_text"],"已有全文");
    let pending=jobs.iter().find(|j|!j.finished()).unwrap();assert!(!pending.paused());assert_eq!(pending.info().provider,"local");
    fs::remove_dir_all(old).unwrap();for j in &jobs {assert!(j.directory.join(j.info().source_file).is_file());}
    drop(jobs);drop(engine);let restored=Engine::open(&data).unwrap();assert_eq!(restored.all_jobs().len(),2);assert_eq!(restored.snapshot(None).pending_jobs,1);
}
#[test]
fn raw_text_survives_retry_and_restart() {
    let dir=Temp::new();let audio=dir.0.join("source.wav");wav(&audio);let job=Job::create_file(&audio,false,"sample".into()).unwrap();
    job.finish_local("first".into(),1.0).unwrap();job.local_pending().unwrap();assert_eq!(job.checkpoint().raw,"first");
    job.finish_local("second".into(),1.0).unwrap();assert_eq!(fs::read_to_string(dir.0.join("上次识别原文.txt")).unwrap(),"first");
    let recovered=Job::recover(&dir.0).unwrap();assert!(recovered.finished());assert_eq!(recovered.progress().raw,"second");
}
#[test]
fn malformed_import_and_external_server_path_are_rejected() {
    let dir=Temp::new();let engine=Engine::open(&dir.0.join("app")).unwrap();let bad=dir.0.join("bad.wav");fs::write(&bad,b"invalid").unwrap();assert!(engine.add_file(&bad,false).is_err());assert!(engine.all_jobs().is_empty());
    let valid=dir.0.join("valid.wav");wav(&valid);fs::create_dir_all(engine.data.join("server-downloads")).unwrap();assert!(engine.add_server_file(&valid,"https://example.com",&serde_json::json!({"id":"one.wav"}),"local").is_err());
}
#[test]
fn server_admission_is_local_and_deduplicated() {
    let dir=Temp::new();let engine=Engine::open(&dir.0).unwrap();let downloads=dir.0.join("server-downloads");fs::create_dir_all(&downloads).unwrap();let file=downloads.join("one.wav");wav(&file);let item=serde_json::json!({"id":"one.wav", "size_bytes":fs::metadata(&file).unwrap().len()});
    let id=engine.add_server_file(&file,"https://example.com",&item,"local").unwrap();assert_eq!(engine.add_server_file(&file,"https://example.com/",&item,"local").unwrap(),id);assert_eq!(engine.all_jobs().len(),1);assert!(!engine.job(&id).unwrap().paused());
}
