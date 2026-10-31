//! Sign to Speak diagnostic, separate from Kyutai's original Rust sources.
//! Uses the original tokenizer.rs implementation and its local path resolver.
#[path = "../tokenizer.rs"]
mod tokenizer;
#[path = "../download.rs"]
mod download;

use anyhow::{Context, Result};
use std::io::Read;

fn main() -> Result<()> {
    let model = std::env::args().nth(1).context("expected local tokenizer.model path")?;
    if std::env::args().count() != 2 {
        anyhow::bail!("expected exactly one tokenizer.model path");
    }
    let mut input = String::new();
    std::io::stdin().take(16 * 1024).read_to_string(&mut input)?;
    let texts: Vec<String> = serde_json::from_str(&input)?;
    if texts.len() != 8 || texts.iter().any(|text| text.is_empty() || text.len() > 256) {
        anyhow::bail!("expected eight short nonempty texts");
    }
    let tokenizer = tokenizer::Tokenizer::load(4000, &model, "sentencepiece")?;
    let ids: Vec<Vec<u32>> = texts.iter().map(|text| tokenizer.encode(text)).collect::<Result<_>>()?;
    println!("{}", serde_json::to_string(&ids)?);
    Ok(())
}
