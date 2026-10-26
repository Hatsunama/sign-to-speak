//! Adapted from Kyutai's pinned Rust tokenizer: only the approved SentencePiece path.
use anyhow::{Result, bail};
use sentencepiece::PieceWithId;
use std::sync::Arc;

// SentencePiece's byte fallback uses reserved piece spellings, not the unknown ID.
// Match the complete <0xHH> spelling so ordinary pieces are left alone.
fn is_byte_fallback_piece(piece: &str) -> bool {
    let bytes = piece.as_bytes();
    bytes.len() == 6
        && bytes.starts_with(b"<0x")
        && bytes[5] == b'>'
        && bytes[3..5].iter().all(u8::is_ascii_hexdigit)
}

fn validate_pieces(pieces: &[PieceWithId], unknown_id: u32) -> Result<()> {
    if pieces.iter().any(|piece| piece.id == unknown_id) {
        bail!("unknown SentencePiece token")
    }
    if pieces.iter().any(|piece| is_byte_fallback_piece(&piece.piece)) {
        bail!("unsupported SentencePiece byte fallback")
    }
    Ok(())
}

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
        validate_pieces(&pieces, unknown_id)?;
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
    use super::{Tokenizer, validate_pieces};
    use sentencepiece::PieceWithId;

    #[test]
    fn byte_fallback_piece_validation() {
        let piece = |id, text: &str| PieceWithId {
            id,
            piece: text.to_owned(),
            span: (0, text.len() as u32),
        };
        assert!(validate_pieces(&[piece(745, "é"), piece(42, "▁Caf")], 0).is_ok());
        for text in ["<0xF4>", "<0x8F>", "<0xBF>", "<0x00>"] {
            assert!(validate_pieces(&[piece(248, text)], 0).is_err(), "{text}");
        }
        assert!(validate_pieces(&[piece(0, "<unk>")], 0).is_err());
        for text in ["<0xF>", "<0xGG>", "<0xF4>>", "<0xfour>"] {
            assert!(validate_pieces(&[piece(248, text)], 0).is_ok(), "{text}");
        }
    }

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
            let tokenized = tokenizer.encode(text);
            eprintln!("{label}: decoded={:?} tokenized={tokenized:?}", sp.decode_piece_ids(&ids).unwrap());
            if label == "accent" {
                assert!(tokenized.is_ok(), "ordinary accented word must remain supported");
            } else {
                assert!(tokenized.is_err(), "unsupported byte fallback must be rejected");
            }
        }
    }
}
