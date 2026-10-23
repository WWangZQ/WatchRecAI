use anyhow::{ensure, Context, Result};
use serde::{de::DeserializeOwned, Serialize};
use std::{
    fs::{self, File, OpenOptions},
    io::Write,
    path::Path,
};

pub fn load<T: DeserializeOwned>(path: &Path) -> Result<T> {
    serde_json::from_slice(&fs::read(path).with_context(|| format!("无法读取 {}", path.display()))?)
        .context("本地记录格式错误")
}
pub fn save<T: Serialize>(path: &Path, value: &T) -> Result<()> {
    write(path, &serde_json::to_vec_pretty(value)?)
}
pub fn write(path: &Path, content: &[u8]) -> Result<()> {
    let parent = path.parent().context("Invalid storage path")?;
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(
        ".{}.{}.tmp",
        path.file_name().unwrap().to_string_lossy(),
        uuid::Uuid::new_v4()
    ));
    let outcome = (|| -> Result<()> {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        file.write_all(content)?;
        file.sync_all()?;
        drop(file);
        replace(&temporary, path)?;
        #[cfg(unix)]
        File::open(parent)?.sync_all()?;
        Ok(())
    })();
    if temporary.exists() {
        let _ = fs::remove_file(temporary);
    }
    outcome
}
#[cfg(not(windows))]
fn replace(from: &Path, to: &Path) -> Result<()> {
    fs::rename(from, to)?;
    Ok(())
}
#[cfg(windows)]
fn replace(from: &Path, to: &Path) -> Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };
    let source: Vec<u16> = from.as_os_str().encode_wide().chain(Some(0)).collect();
    let target: Vec<u16> = to.as_os_str().encode_wide().chain(Some(0)).collect();
    // A short reader (Python's sync helper, antivirus, preview) can temporarily
    // deny replacement on Windows. Keep the atomic write and retry those errors.
    let mut last_error;
    for attempt in 0..20 {
        if unsafe {
            MoveFileExW(
                source.as_ptr(),
                target.as_ptr(),
                MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
            )
        } != 0 { return Ok(()); }
        last_error = std::io::Error::last_os_error();
        if !matches!(last_error.raw_os_error(), Some(5 | 32 | 33)) || attempt == 19 {
            return Err(last_error).context("无法保存记录");
        }
        std::thread::sleep(std::time::Duration::from_millis(25));
    }
    unreachable!()
}
pub fn lock(path: &Path) -> Result<File> {
    fs::create_dir_all(path.parent().context("Invalid lock path")?)?;
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(path)?;
    fs2::FileExt::try_lock_exclusive(&file).context("该记录目录已有程序正在使用")?;
    Ok(file)
}
pub fn local_name(name: &str) -> Result<()> {
    ensure!(
        !name.is_empty()
            && Path::new(name).file_name().is_some_and(|n| n == name)
            && name != "."
            && name != "..",
        "Invalid local file name"
    );
    Ok(())
}
