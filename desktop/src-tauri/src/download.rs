use std::io::Write;
use std::path::Path;
use std::time::Duration;

pub fn validate_audio_url(url: &str, backend_url: &str) -> Result<reqwest::Url, String> {
    let parsed = reqwest::Url::parse(url).map_err(|_| "invalid audio URL".to_string())?;
    let backend =
        reqwest::Url::parse(backend_url).map_err(|_| "invalid backend URL".to_string())?;
    if parsed.origin() != backend.origin()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.fragment().is_some()
    {
        return Err("only the current backend's audio URLs are permitted".to_string());
    }
    let path = parsed.path().strip_prefix("/api/jobs/").unwrap_or("");
    let (id, resource) = path.split_once('/').unwrap_or(("", ""));
    let valid_id = id.len() == 12
        && id
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte));
    let valid_resource = matches!(
        resource,
        "chords.mid"
            | "chords.csv"
            | "midi-analysis.json"
            | "mixdown.wav"
            | "mixdown.mp3"
            | "mixdown.flac"
            | "stems/all.zip"
    ) || resource.strip_prefix("stems/").is_some_and(|file| {
        let (name, extension) = file.rsplit_once('.').unwrap_or(("", ""));
        matches!(
            name,
            "vocals" | "drums" | "bass" | "guitar" | "piano" | "other" | "original" | "mix"
        ) && matches!(extension, "wav" | "mp3")
    });
    if !valid_id || !valid_resource {
        return Err("not an audio export URL".to_string());
    }
    Ok(parsed)
}

pub async fn save_audio(url: reqwest::Url, dest: &Path) -> Result<(), String> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(300))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|error| format!("failed to build download client: {error}"))?;
    let mut response = client
        .get(url)
        .send()
        .await
        .map_err(|error| format!("fetch failed: {error}"))?;
    if !response.status().is_success() {
        return Err(format!("backend returned HTTP {}", response.status()));
    }
    let parent = dest.parent().ok_or("invalid destination folder")?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent)
        .map_err(|error| format!("failed to create download file: {error}"))?;
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|error| format!("read failed: {error}"))?
    {
        temporary
            .write_all(&chunk)
            .map_err(|error| format!("write failed: {error}"))?;
    }
    temporary
        .as_file()
        .sync_all()
        .map_err(|error| format!("flush failed: {error}"))?;
    temporary
        .persist(dest)
        .map_err(|error| format!("failed to save download: {error}"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{save_audio, validate_audio_url};
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;

    const BACKEND: &str = "http://127.0.0.1:8765";

    #[test]
    fn permits_current_backend_exports_with_query_parameters() {
        let url = format!("{BACKEND}/api/jobs/abcdefabcdef/mixdown.wav?stems=bass&gains=1");
        assert!(validate_audio_url(&url, BACKEND).is_ok());
    }

    #[test]
    fn rejects_other_services_credentials_and_unrelated_routes() {
        for url in [
            "http://127.0.0.1:9999/api/jobs/abcdefabcdef/stems/bass.wav",
            "https://attacker.example/api/jobs/abcdefabcdef/stems/bass.wav",
            "http://user:pass@127.0.0.1:8765/api/jobs/abcdefabcdef/stems/bass.wav",
            "http://127.0.0.1:8765/api/logs",
            "http://127.0.0.1:8765/api/jobs/invalid/stems/bass.wav",
        ] {
            assert!(validate_audio_url(url, BACKEND).is_err(), "accepted {url}");
        }
    }

    fn serve(response: Vec<u8>) -> (reqwest::Url, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = reqwest::Url::parse(&format!("http://{}/audio", listener.local_addr().unwrap()))
            .unwrap();
        let worker = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0; 4096];
            stream.read(&mut request).unwrap();
            stream.write_all(&response).unwrap();
        });
        (url, worker)
    }

    #[test]
    fn saves_atomically_without_overwriting_an_unrelated_temporary_file() {
        let folder = tempfile::tempdir().unwrap();
        let dest = folder.path().join("export.wav");
        let unrelated = folder.path().join("export.audio.download");
        std::fs::write(&dest, b"old audio").unwrap();
        std::fs::write(&unrelated, b"keep me").unwrap();
        let (url, worker) = serve(
            b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\nRIFF".to_vec(),
        );
        tauri::async_runtime::block_on(save_audio(url, &dest)).unwrap();
        worker.join().unwrap();
        assert_eq!(std::fs::read(dest).unwrap(), b"RIFF");
        assert_eq!(std::fs::read(unrelated).unwrap(), b"keep me");
        assert_eq!(std::fs::read_dir(folder.path()).unwrap().count(), 2);
    }

    #[test]
    fn failed_download_preserves_existing_audio_and_removes_temporary_file() {
        let folder = tempfile::tempdir().unwrap();
        let dest = folder.path().join("export.wav");
        std::fs::write(&dest, b"old audio").unwrap();
        let (url, worker) = serve(
            b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\nRIFF".to_vec(),
        );
        assert!(tauri::async_runtime::block_on(save_audio(url, &dest)).is_err());
        worker.join().unwrap();
        assert_eq!(std::fs::read(dest).unwrap(), b"old audio");
        assert_eq!(std::fs::read_dir(folder.path()).unwrap().count(), 1);
    }

    #[test]
    fn redirect_is_rejected_before_creating_a_file() {
        let folder = tempfile::tempdir().unwrap();
        let (url, worker) = serve(b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:9/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".to_vec());
        let result =
            tauri::async_runtime::block_on(save_audio(url, &folder.path().join("export.wav")));
        worker.join().unwrap();
        assert!(result.unwrap_err().contains("302"));
        assert_eq!(std::fs::read_dir(folder.path()).unwrap().count(), 0);
    }
}
