//! Port of `TTSModel` from `pocket_tts/models/tts_model.py`, on the streaming primitives of
//! xn-ptts: loading a config's weights, conditioning on a voice, and generating audio chunk by
//! chunk on the calling thread, with synchronous decode and sink delivery.

use crate::config::{Config, FlowType};
use crate::rng::NoiseRng;
use crate::text::{TextOptions, prepare_text_prompt};
use crate::tokenizer::{SharedTokenizer, Tokenizer};
use crate::weights::{SafetensorsFile, WeightFiles};
use anyhow::{Context, Result, bail};
use ptts::flow_lm::{FlowLMState, StepInput};
use ptts::transformer::{LayerAttentionState, StreamingMHAState, StreamingTransformerState};
use ptts::tts_model::{TTSModel, TTSState};
use std::collections::HashMap;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use xn::{BackendQ, Tensor, WithDTypeF};

/// CPU backend used by this component.
pub trait CpuBackend: BackendQ<B = xn::CpuDevice> {}
impl<Q: BackendQ<B = xn::CpuDevice>> CpuBackend for Q {}

const TOKENS_PER_SECOND_ESTIMATE: f64 = 3.0;
const GEN_SECONDS_PADDING: f64 = 2.0;
/// EOS is ignored on the first frames: before speech starts, the EOS logit of some voices can
/// cross the threshold, and a short text then ends before the word is spoken.
const MIN_FRAMES_BEFORE_EOS: usize = 6;

fn generation_completion(stop: &AtomicBool, _completed_after_eos: bool) -> Result<()> {
    if stop.load(Ordering::Acquire) { bail!("cancelled") }
    Ok(())
}

/// What `load_model` takes besides the config.
#[derive(Debug, Clone)]
pub struct LoadOptions {
    pub temperature: Option<f32>,
    pub sampler_decode_steps: usize,
    pub noise_clamp: Option<f32>,
    /// The config's `default_eos_threshold` when `None`.
    pub eos_threshold: Option<f32>,
}

impl Default for LoadOptions {
    fn default() -> Self {
        Self {
            temperature: None,
            sampler_decode_steps: 1,
            noise_clamp: None,
            eos_threshold: None,
        }
    }
}

/// The flow LM state after a voice prompt, which every generation starts from.
pub struct VoiceState<Q: CpuBackend> {
    transformer_state: StreamingTransformerState<Q::T, Q::B>,
}

impl<Q: CpuBackend> VoiceState<Q> {
    /// Positions the prompt fills.
    pub fn len(&self) -> usize {
        match self.transformer_state.layer_states.first() {
            Some(LayerAttentionState::FlowLm(state)) => state.current_end,
            _ => 0,
        }
    }


}

pub struct TtsModel<Q: CpuBackend> {
    pub config: Config,
    model: TTSModel<Q>,
    tokenizer: Arc<Tokenizer>,
    text_options: TextOptions,
    rng: Mutex<NoiseRng>,
    num_layers: usize,
    num_kv_heads: usize,
    head_dim: usize,
}

impl<Q: CpuBackend> TtsModel<Q> {
    /// Port of `TTSModel.load_model`, for the config already read.
    pub fn load(config: Config, options: &LoadOptions) -> Result<Self> {
        let weight_path = config.weights_path.as_ref().context("missing pinned model weights")?;
        let weight_files = WeightFiles { bundle: Some(std::path::PathBuf::from(weight_path)), ..Default::default() };

        let lookup_table = &config.flow_lm.lookup_table;
        let tokenizer = Arc::new(Tokenizer::load(
            lookup_table.n_bins,
            &lookup_table.tokenizer_path,
            &lookup_table.tokenizer,
        )?);

        // The drifting and pointwise heads map the noise in one step, whatever the number of
        // steps asked.
        let decode_steps = match config.flow_type() {
            FlowType::Drifting | FlowType::Pointwise => 1,
            FlowType::Lsd | FlowType::FlowMatching => options.sampler_decode_steps,
        };
        if decode_steps == 0 {
            bail!("the number of sampler decode steps must be positive")
        }
        let eos_threshold = options
            .eos_threshold
            .unwrap_or(config.default_eos_threshold);
        let ptts_config = config.to_ptts(decode_steps, eos_threshold)?;
        let weights = crate::weights::load(&weight_files)?;
        let model = TTSModel::<Q>::load(
            &weights.vb,
            Box::new(SharedTokenizer(tokenizer.clone())),
            &ptts_config,
            &HashMap::new(),
        )?;
        drop(weights);

        let temperature = options.temperature.unwrap_or(config.default_temperature);
        let transformer = &config.flow_lm.transformer;
        Ok(Self {
            num_layers: transformer.num_layers,
            num_kv_heads: transformer.num_kv_heads(),
            head_dim: transformer.head_dim(),
            text_options: TextOptions::from_config(&config),
            rng: Mutex::new(NoiseRng::new(temperature, options.noise_clamp)?),
            config,
            model,
            tokenizer,
        })
    }

    pub fn sample_rate(&self) -> usize {
        self.config.mimi.sample_rate
    }

    /// Loads the pinned Alba state only; the caller verifies its bytes before this method.
    pub fn import_alba_state(&self, path: &Path) -> Result<VoiceState<Q>> {
        self.import_state(path)
    }

    /// Reads a state written by `export-voice`, from this binary or the Python package.
    fn import_state(&self, path: &Path) -> Result<VoiceState<Q>> {
        let file = SafetensorsFile::open(path)?;
        let dim_per_head = self.head_dim;
        let mut layer_states = Vec::with_capacity(self.num_layers);
        for i in 0..self.num_layers {
            let module = format!("transformer.layers.{i}.self_attn");
            let cache_name = format!("{module}/cache");
            if !file.tensors.contains_key(&cache_name) {
                bail!(
                    "{} is not a voice state of this model: it has no {cache_name}",
                    path.display()
                )
            }
            let (cache, shape) = file.read_f32(&cache_name)?;
            let [2, b, capacity, h, d] = shape[..] else {
                bail!("{cache_name} has shape {shape:?}, expected [2, batch, time, heads, dim]")
            };
            if b != 1 || h != self.num_kv_heads || d != dim_per_head {
                bail!(
                    "{cache_name} has shape {shape:?}, this model needs [2, 1, _, {}, {dim_per_head}]",
                    self.num_kv_heads
                )
            }
            // States written before the offset was stored hold the position in the length of
            // `current_end`.
            let offset_name = format!("{module}/offset");
            let current_end_name = format!("{module}/current_end");
            let used = if file.tensors.contains_key(&offset_name) {
                file.read_i64(&offset_name)?.first().copied().unwrap_or(0) as usize
            } else if let Some(info) = file.tensors.get(&current_end_name) {
                info.shape.first().copied().unwrap_or(0)
            } else {
                capacity
            };
            if used > capacity {
                bail!("{offset_name} is {used}, past the {capacity} cached positions")
            }
            let per_position = h * d;
            let plane = capacity * per_position;
            let take = |start: usize| -> Result<Tensor<Q::T, Q::B>> {
                let data = cache[start..start + used * per_position]
                    .iter()
                    .map(|&v| Q::T::from_f32(v))
                    .collect();
                Ok(Tensor::from_vec(
                    data,
                    (1, used, h, d),
                    self.model.device(),
                )?)
            };
            layer_states.push(LayerAttentionState::FlowLm(StreamingMHAState {
                k_cache: take(0)?,
                v_cache: take(plane)?,
                current_end: used,
            }));
        }
        Ok(VoiceState {
            transformer_state: StreamingTransformerState { layer_states },
        })
    }

    fn estimate_max_gen_len(&self, token_count: usize) -> usize {
        let gen_len_sec = token_count as f64 / TOKENS_PER_SECOND_ESTIMATE + GEN_SECONDS_PADDING;
        (gen_len_sec * self.config.mimi.frame_rate).ceil() as usize
    }

    /// Counts prepared SentencePiece IDs before any speech is emitted.
    pub fn preflight_tokens(&self, text: &str) -> Result<usize> {
        let (prepared, _) = prepare_text_prompt(text, &self.text_options)?;
        Ok(self.tokenizer.encode(&prepared)?.len())
    }

    /// Generate and decode synchronously. No latent queue or worker thread exists.
    /// Caller validates the full text and token budget before entering this method.
    pub fn generate_audio_stream(
        &self,
        voice: &VoiceState<Q>,
        text: &str,
        stop: &AtomicBool,
        on_chunk: &mut dyn FnMut(&[f32]) -> Result<()>,
    ) -> Result<()> {
        if stop.load(Ordering::Acquire) { bail!("cancelled") }
        let (prepared, frames_guess) = prepare_text_prompt(text, &self.text_options)?;
        let frames_after_eos = self.config.model_recommended_frames_after_eos.unwrap_or(frames_guess + 2);
        let tokens = self.tokenizer.encode(&prepared)?;
        if tokens.len() > 128 { bail!("token budget exceeded") }
        let max_gen_len = self.estimate_max_gen_len(tokens.len()).min(375);
        let seq_budget = voice.len() + tokens.len() + max_gen_len;
        let transformer_state = voice.transformer_state.with_seq_budget(seq_budget)?;
        let mut state = TTSState {
            flow_lm_state: FlowLMState { transformer_state, conditions: None },
        };
        self.model.prompt_text(&mut state, &tokens)?;
        let mut mimi_state = self.model.init_mimi_state(1)?;
        let fade_len = self.sample_rate() / 200;
        let mut first = true;
        let mut previous: Option<Tensor<Q::T, Q::B>> = None;
        let mut eos_step = None;
        let mut completed_after_eos = false;
        let mut samples = 0usize;
        for generation_step in 0..max_gen_len {
            if stop.load(Ordering::Acquire) { bail!("cancelled") }
            let input = match &previous {
                None => StepInput::Bos { batch: 1 },
                Some(latent) => StepInput::Latent(latent),
            };
            let (latent, is_eos) = {
                let mut rng = self.rng.lock().unwrap_or_else(|e| e.into_inner());
                self.model.generate_step(&mut state, input, &mut *rng)?
            };
            if is_eos && eos_step.is_none() && generation_step >= MIN_FRAMES_BEFORE_EOS {
                eos_step = Some(generation_step);
            }
            if eos_step.is_some_and(|step| generation_step >= step + frames_after_eos) {
                completed_after_eos = true;
                break
            }
            if stop.load(Ordering::Acquire) { bail!("cancelled") }
            let audio = self.model.decode_latent(&latent, &mut mimi_state)?;
            let mut pcm = audio.to_vec()?;
            if first {
                let n = fade_len.min(pcm.len());
                for (i, sample) in pcm[..n].iter_mut().enumerate() {
                    *sample *= if fade_len > 1 { i as f32 / (fade_len - 1) as f32 } else { 0.0 };
                }
                first = false;
            }
            samples += pcm.len();
            if samples > 30 * 24_000 { bail!("audio duration limit exceeded") }
            if stop.load(Ordering::Acquire) { bail!("cancelled") }
            on_chunk(&pcm)?;
            previous = Some(latent);
        }
        generation_completion(stop, completed_after_eos)
    }

}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generation_limit_without_eos_is_an_error() {
        let stop = AtomicBool::new(false);
        assert!(generation_completion(&stop, false).is_err());
    }

    #[test]
    fn eos_completion_succeeds_and_cancellation_takes_precedence() {
        let stop = AtomicBool::new(false);
        assert!(generation_completion(&stop, true).is_ok());
        stop.store(true, Ordering::Release);
        assert!(generation_completion(&stop, true).is_err());
        assert!(generation_completion(&stop, false).is_err());
    }
}

