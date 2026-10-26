//! Prompt preparation rules adapted from `pocket_tts/models/text_chunking.py`.

use anyhow::{Result, bail};
use std::collections::BTreeMap;

/// The text options of a model config.
#[derive(Debug, Clone)]
pub struct TextOptions {
    pub pad_with_spaces_for_short_inputs: bool,
    pub remove_semicolons: bool,
    pub append_terminal_punctuation: bool,
    pub capitalize_first_letter: bool,
    pub replace_characters: BTreeMap<char, String>,
}

impl TextOptions {
    pub fn from_config(config: &crate::config::Config) -> Self {
        let replace_characters = config
            .replace_characters
            .iter()
            .map(|(key, value)| {
                (
                    key.chars().next().unwrap(),
                    value.clone().unwrap_or_default(),
                )
            })
            .collect();
        Self {
            pad_with_spaces_for_short_inputs: config.pad_with_spaces_for_short_inputs,
            remove_semicolons: config.remove_semicolons,
            append_terminal_punctuation: config.append_terminal_punctuation,
            capitalize_first_letter: config.capitalize_first_letter,
            replace_characters,
        }
    }
}

/// Python's `" ".join(text.split())`.
fn collapse_whitespace(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Python's `re.sub(r"([.!?…])\s*[,;:]", r"\1", text)`: deleted quotes leave
/// '"Hi?", she said' as 'Hi?, she said', which reads as a sentence end followed by a stray comma;
/// keep the sentence mark only.
fn drop_punctuation_after_sentence_end(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        out.push(c);
        i += 1;
        if matches!(c, '.' | '!' | '?' | '\u{2026}') {
            let mut j = i;
            while j < chars.len() && chars[j].is_whitespace() {
                j += 1;
            }
            if j < chars.len() && matches!(chars[j], ',' | ';' | ':') {
                i = j + 1;
            }
        }
    }
    out
}

/// Returns the prepared text and a guess of the frames to generate after the end of speech.
pub fn prepare_text_prompt(text: &str, options: &TextOptions) -> Result<(String, usize)> {
    let mut text = text.trim().to_string();
    if !options.replace_characters.is_empty() {
        let translated: String = text
            .chars()
            .map(|c| match options.replace_characters.get(&c) {
                Some(replacement) => replacement.clone(),
                None => c.to_string(),
            })
            .collect();
        text = drop_punctuation_after_sentence_end(&collapse_whitespace(&translated));
    }
    if text.is_empty() {
        bail!("Text prompt cannot be empty")
    }
    text = text.replace(['\n', '\r'], " ").replace("  ", " ");
    if options.remove_semicolons {
        text = text.replace(';', ",");
    }
    let number_of_words = text.split_whitespace().count();
    let frames_after_eos_guess = if number_of_words <= 4 { 3 } else { 1 };

    // Make sure it starts with an uppercase letter. Only meaningful for orthographies that have
    // case; see the config's capitalize_first_letter.
    if options.capitalize_first_letter {
        let mut chars = text.chars();
        let first = chars.next().unwrap();
        if !first.is_uppercase() {
            text = first.to_uppercase().chain(chars).collect();
        }
    }

    if options.append_terminal_punctuation {
        text = ensure_terminal_punctuation(&text);
    }

    // The model does not perform well when there are very few tokens, so we can add empty spaces
    // at the beginning to increase the token count.
    if options.pad_with_spaces_for_short_inputs && text.split_whitespace().count() < 5 {
        text = format!("{}{text}", " ".repeat(8));
    }

    Ok((text, frames_after_eos_guess))
}

const TERMINAL_PUNCTUATION: &str = ".!?\u{2026}";
const WEAK_PUNCTUATION: &str = ",;:-\u{2013}\u{2014}";
const CLOSERS: &str = "\"'\u{201d}\u{2019})]\u{bb}";

/// Make sure the prompt ends with sentence-final punctuation.
///
/// The model is trained on sentences that end with a period, a question mark or an exclamation
/// mark. Without one, the last word is often mispronounced or repeated. Text that already ends
/// with one, possibly followed by closing quotes or brackets, is left alone. A trailing comma,
/// colon or dash is replaced by a period. Anything else gets a period appended after the closing
/// quote or bracket.
fn ensure_terminal_punctuation(text: &str) -> String {
    let core = text.trim_end_matches(|c| CLOSERS.contains(c) || c == ' ');
    let closers = text[core.len()..].trim();
    match core.chars().last() {
        None => text.to_string(),
        Some(last) if TERMINAL_PUNCTUATION.contains(last) => text.to_string(),
        Some(last) if WEAK_PUNCTUATION.contains(last) => {
            let core = core.trim_end_matches(|c| WEAK_PUNCTUATION.contains(c) || c == ' ');
            format!("{core}.{closers}")
        }
        Some(_) => format!("{text}."),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn preserves_pinned_preparation_rules() {
        let opts = TextOptions {
            pad_with_spaces_for_short_inputs: false,
            remove_semicolons: false,
            append_terminal_punctuation: true,
            capitalize_first_letter: true,
            replace_characters: [('—', ",".to_string())].into(),
        };
        assert_eq!(prepare_text_prompt("hello — world", &opts).unwrap().0, "Hello , world.");
        assert_eq!(prepare_text_prompt("hello", &opts).unwrap().0, "Hello.");
        assert!(prepare_text_prompt(" ", &opts).is_err());
    }
}
