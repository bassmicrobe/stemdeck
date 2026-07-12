use layerlab::pcm::{execute, PcmCommand, PcmResponse};
use std::io::{self, Read};

fn main() {
    let result = run();
    match result {
        Ok(response) => {
            println!(
                "{}",
                serde_json::to_string(&response).expect("serialize PCM response")
            );
        }
        Err(error) => {
            let response = PcmResponse::error(error);
            println!(
                "{}",
                serde_json::to_string(&response).expect("serialize PCM error")
            );
            std::process::exit(1);
        }
    }
}

fn run() -> Result<PcmResponse, String> {
    let mut input = String::new();
    io::stdin()
        .read_to_string(&mut input)
        .map_err(|error| format!("failed to read command: {error}"))?;
    let command: PcmCommand =
        serde_json::from_str(&input).map_err(|error| format!("invalid command JSON: {error}"))?;
    execute(command)
}
