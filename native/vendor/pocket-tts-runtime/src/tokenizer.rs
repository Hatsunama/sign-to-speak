//! Adapted from Kyutai's pinned Rust tokenizer: only the approved SentencePiece path.
use anyhow::{Result, bail};
use std::sync::Arc;

pub struct Tokenizer(sentencepiece::SentencePieceProcessor);
impl Tokenizer {
    pub fn load(n_bins: usize, tokenizer_path: &str, kind: &str) -> Result<Self> {
        if kind != "sentencepiece" || n_bins != 4000 { bail!("unexpected tokenizer configuration") }
        let sp = sentencepiece::SentencePieceProcessor::open(tokenizer_path)?;
        if sp.len() != n_bins { bail!("unexpected tokenizer vocabulary") }
        Ok(Self(sp))
    }
    pub fn encode(&self, text: &str) -> Result<Vec<u32>> {
        let pieces = self.0.encode(text)?;
        let unknown_id = self.0.unk_id();
        if pieces.iter().any(|piece| piece.id == unknown_id) {
            bail!("unknown SentencePiece token")
        }
        Ok(pieces.into_iter().map(|piece| piece.id).collect())
    }
    pub fn decode(&self, ids: &[u32]) -> Result<String> {
        Ok(self.0.decode_piece_ids(ids)?)
    }
}
pub struct SharedTokenizer(pub Arc<Tokenizer>);
impl ptts::Tokenizer for SharedTokenizer {
    fn encode(&self, text: &str) -> xn::Result<Vec<u32>> {
        self.0.encode(text).map_err(|e| xn::Error::msg(format!("{e:#}")))
    }
    fn decode(&self, tokens: &[u32]) -> xn::Result<String> {
        self.0.decode(tokens).map_err(|e| xn::Error::msg(format!("{e:#}")))
    }
}

#[cfg(test)]
mod tests {
    use super::Tokenizer;

    // Run only after hosted CI has downloaded and verified the pinned tokenizer.model:
    // TOKENIZER_MODEL=... cargo test --release --locked --offline diagnostic_tokenizer_contract -- --ignored --nocapture
    #[test]
    #[ignore = "requires the verified, hosted English 18M tokenizer.model"]
    fn diagnostic_tokenizer_contract() {
        let path = std::env::var("TOKENIZER_MODEL").expect("TOKENIZER_MODEL is required");
        let tokenizer = Tokenizer::load(4000, &path, "sentencepiece").unwrap();
        let sp = &tokenizer.0;
        eprintln!("unknown_id={}", sp.unk_id());
        for (label, text) in [
            ("noncharacter", "Hello \u{10ffff}."),
            ("accent", "Café."),
            ("CJK", "你好。"),
            ("emoji", "Hello 👋."),
            ("zero-width", "Hello\u{200b}world."),
        ] {
            let pieces = sp.encode(text).unwrap();
            let ids: Vec<_> = pieces.iter().map(|piece| piece.id).collect();
            eprintln!(
                "{label}: text={text:?} normalized={:?}",
                sp.normalize(text).unwrap()
            );
            eprintln!("{label}: pieces={pieces:?}");
            eprintln!(
                "{label}: decoded={:?} tokenized={:?}",
                sp.decode_piece_ids(&ids).unwrap(),
                tokenizer.encode(text)
            );
        }
    }
}
