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
        if pieces.iter().any(|piece| piece.id == 0) { bail!("unknown SentencePiece token") }
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
