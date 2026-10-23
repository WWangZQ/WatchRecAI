//! WebKit media playback via loopback HTTP ranges. Only explicitly registered
//! audio files are reachable, behind an unguessable per-process URL token.
use std::{collections::HashMap, fs::File, io::{BufRead, BufReader, Read, Seek, SeekFrom, Write},
    net::{TcpListener, TcpStream}, path::{Path, PathBuf}, sync::{Arc, Mutex}, time::Duration};
use sha2::{Digest, Sha256};

pub struct Media {
    port: u16,
    secret: [u8; 32],
    files: Arc<Mutex<HashMap<String, PathBuf>>>,
}

impl Media {
    pub fn start() -> std::io::Result<Self> {
        let listener = TcpListener::bind(("127.0.0.1", 0))?;
        let port = listener.local_addr()?.port();
        let mut secret = [0; 32];
        File::open("/dev/urandom")?.read_exact(&mut secret)?;
        let files = Arc::new(Mutex::new(HashMap::<String, PathBuf>::new()));
        let registry = files.clone();
        std::thread::spawn(move || {
            for stream in listener.incoming().flatten() {
                let registry = registry.clone();
                std::thread::spawn(move || { let _ = serve(stream, &registry); });
            }
        });
        Ok(Self { port, secret, files })
    }
    pub fn url(&self, path: &Path) -> String {
        let mut hash = Sha256::new();
        hash.update(self.secret);
        hash.update(path.to_string_lossy().as_bytes());
        let key = format!("/{:x}", hash.finalize());
        self.files.lock().unwrap().insert(key.clone(), path.to_owned());
        format!("http://127.0.0.1:{}{key}", self.port)
    }
}

fn range(value: &str, len: u64) -> Option<(u64, u64)> {
    if len == 0 { return None; }
    let (start, end) = value.strip_prefix("bytes=")?.split_once('-')?;
    if start.is_empty() {
        let suffix: u64 = end.parse().ok()?;
        return (suffix > 0).then_some((len.saturating_sub(suffix), len - 1));
    }
    let start: u64 = start.parse().ok()?;
    let end: u64 = if end.is_empty() { len - 1 } else { end.parse().ok()? };
    (start < len && end >= start).then_some((start, end.min(len - 1)))
}

fn serve(mut stream: TcpStream, registry: &Mutex<HashMap<String, PathBuf>>) -> std::io::Result<()> {
    stream.set_read_timeout(Some(Duration::from_secs(5)))?;
    stream.set_write_timeout(Some(Duration::from_secs(10)))?;
    let mut request = Vec::new();
    let mut reader = BufReader::new(stream.try_clone()?.take(16384));
    while request.len() < 16384 {
        let size = reader.read_until(b'\n', &mut request)?;
        if size == 0 || request.ends_with(b"\r\n\r\n") { break; }
    }
    if request.len() >= 16384 { return Ok(()); }
    let header = String::from_utf8_lossy(&request);
    let mut lines = header.lines();
    let mut first = lines.next().unwrap_or("").split_whitespace();
    let method = first.next().unwrap_or("");
    let path = first.next().unwrap_or("");
    let file = registry.lock().unwrap().get(path).cloned();
    if !matches!(method, "GET" | "HEAD") || file.is_none() {
        return stream.write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
    }
    let file_path = file.unwrap();
    let mut file = File::open(&file_path)?;
    let len = file.metadata()?.len();
    let requested = lines.filter_map(|l| l.split_once(':')).find(|(k, _)| k.eq_ignore_ascii_case("range"));
    let (status, start, end) = if let Some((_, value)) = requested {
        match range(value.trim(), len) {
            Some((start, end)) => ("206 Partial Content", start, end),
            None => return write!(stream, "HTTP/1.1 416 Range Not Satisfiable\r\nContent-Range: bytes */{len}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"),
        }
    } else { ("200 OK", 0, len.saturating_sub(1)) };
    let size = if len == 0 { 0 } else { end - start + 1 };
    let content_type = match file_path.extension().and_then(|s|s.to_str()).unwrap_or("").to_ascii_lowercase().as_str() {
        "m4a" | "mp4" => "audio/mp4", "mp3" => "audio/mpeg", "wav" => "audio/wav",
        "aac" => "audio/aac", "flac" => "audio/flac", "ogg" => "audio/ogg", "webm" => "audio/webm",
        _ => "application/octet-stream",
    };
    write!(stream, "HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {size}\r\nAccept-Ranges: bytes\r\nCache-Control: no-store\r\nConnection: close\r\n")?;
    if requested.is_some() { write!(stream, "Content-Range: bytes {start}-{end}/{len}\r\n")?; }
    stream.write_all(b"\r\n")?;
    if method == "GET" {
        file.seek(SeekFrom::Start(start))?;
        std::io::copy(&mut file.take(size), &mut stream)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ranges_support_webkit_seek_and_reject_invalid_ranges() {
        assert_eq!(range("bytes=0-1", 10), Some((0, 1)));
        assert_eq!(range("bytes=5-", 10), Some((5, 9)));
        assert_eq!(range("bytes=-3", 10), Some((7, 9)));
        assert_eq!(range("bytes=2-100", 10), Some((2, 9)));
        for invalid in ["bytes=10-", "bytes=5-2", "bytes=0-1,5-8", "bytes=-0"] {
            assert_eq!(range(invalid, 10), None);
        }
    }
    #[test]
    fn serves_only_registered_files_and_returns_exact_range() {
        let media = Media::start().unwrap();
        let file = std::env::temp_dir().join(format!("watchrec-media-{}.wav", media.port));
        std::fs::write(&file, b"0123456789").unwrap();
        let url = media.url(&file);
        let path = url.split_once(&format!(":{}", media.port)).unwrap().1;
        let fetch = |path: &str| {
            let mut stream = TcpStream::connect(("127.0.0.1", media.port)).unwrap();
            write!(stream, "GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nRange: bytes=2-5\r\n\r\n").unwrap();
            let mut response = String::new(); stream.read_to_string(&mut response).unwrap(); response
        };
        let response = fetch(path);
        assert!(response.starts_with("HTTP/1.1 206"));
        assert!(response.ends_with("\r\n\r\n2345"));
        assert!(fetch("/etc/passwd").starts_with("HTTP/1.1 404"));
        std::fs::remove_file(file).unwrap();
    }
}
