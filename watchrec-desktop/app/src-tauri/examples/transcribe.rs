
//! cargo run -p voiceink --example transcribe -- DATA_DIR AUDIO PYTHON RESOURCE_DIR
//! Uses a new, isolated DATA_DIR and the same local queue as the desktop app.
#[allow(dead_code)]
#[path="../src/tasks.rs"] mod tasks;
use std::{path::PathBuf,time::{Duration,Instant}};
use voiceink_core::{engine::Engine,storage};
fn main() -> anyhow::Result<()> {
    let args:Vec<_>=std::env::args().skip(1).collect();anyhow::ensure!(args.len()==4,"DATA_DIR AUDIO PYTHON RESOURCE_DIR required");
    let data=PathBuf::from(&args[0]);anyhow::ensure!(!data.exists(),"Use a fresh test directory");
    let engine=Engine::open(&data)?;storage::save(&data.join("settings.json"),&tasks::Settings{python_path:args[2].clone(),..Default::default()})?;
    let queue=tasks::Tasks::open(engine.clone(),PathBuf::from(&args[3]));let id=engine.add_file(&PathBuf::from(&args[1]),false)?;queue.start();let until=Instant::now()+Duration::from_secs(900);
    loop { let j=engine.job(&id)?;if j.finished() {println!("{}",serde_json::to_string_pretty(&j.progress())?);break;}
        if j.paused()||Instant::now()>until {queue.shutdown();anyhow::bail!("{}",j.progress().status)}std::thread::sleep(Duration::from_millis(250)); }
    queue.shutdown();queue.wait_for_shutdown(Duration::from_secs(5));engine.save_for_shutdown(Duration::from_secs(3));Ok(())
}
