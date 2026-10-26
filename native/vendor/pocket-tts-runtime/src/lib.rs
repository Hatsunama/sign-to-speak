//! Sign to Speak's isolated, local-only Alba/18M C ABI. Original code Apache 2.0;
//! adapted Kyutai modules retain their MIT notice in KYUTAI-MIT-LICENSE.
mod config;
mod model;
mod rng;
mod text;
mod tokenizer;
mod weights;

use config::Config;
use model::{LoadOptions, TtsModel};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

type Backend = xn::Unquantized<f32, xn::CpuDevice>;
type PcmSink = extern "C" fn(u64, *const i16, usize, *mut std::ffi::c_void) -> bool;
const MODEL_SHA: &str = "599329dcea2f2a8d17b575e1c6e11b78822453c9073040d55c26faadf4883188";
const TOKEN_SHA: &str = "d461765ae179566678c93091c5fa6f2984c31bbe990bf1aa62d92c64d91bc3f6";
const VOICE_SHA: &str = "11554cbe9f3d3a3e10722932df9952bb44928e534f7af3200524659166b5627a";
const MAX_TEXT_BYTES: usize = 512;
const MAX_TOKENS: usize = 128;
const PCM_CHUNK: usize = 1920;

#[repr(i32)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Ok = 0, Cancelled = 1, InvalidInput = 2, Assets = 3,
    Model = 4, Sink = 5, Fault = 6, UnknownRequest = 7, Busy = 8,
}

struct Request { root: PathBuf, cancelled: AtomicBool, active: AtomicBool }
static REQUESTS: OnceLock<Mutex<HashMap<u64, Arc<Request>>>> = OnceLock::new();
static NEXT: AtomicU64 = AtomicU64::new(1);
fn registry() -> &'static Mutex<HashMap<u64, Arc<Request>>> {
    REQUESTS.get_or_init(|| Mutex::new(HashMap::new()))
}
fn request(id: u64) -> Option<Arc<Request>> {
    registry().lock().unwrap_or_else(|p| p.into_inner()).get(&id).cloned()
}
fn boundary(f: impl FnOnce() -> Status + std::panic::UnwindSafe) -> Status {
    std::panic::catch_unwind(f).unwrap_or(Status::Fault)
}

/// Registers a request before expensive model initialization. `root` has exactly the three
/// approved files at its top level; neither the path nor user text is logged.
#[unsafe(no_mangle)]
pub extern "C" fn sts_pocket_create(root: *const u8, root_len: usize, out_id: *mut u64) -> Status {
    boundary(|| {
        if root.is_null() || out_id.is_null() || root_len == 0 || root_len > 4096 { return Status::InvalidInput }
        let bytes = unsafe { std::slice::from_raw_parts(root, root_len) };
        let Ok(value) = std::str::from_utf8(bytes) else { return Status::InvalidInput };
        if value.contains('\0') || value.contains("://") { return Status::InvalidInput }
        let path = PathBuf::from(value);
        if !path.is_absolute() { return Status::InvalidInput }
        let Ok(meta) = fs::symlink_metadata(&path) else { return Status::Assets };
        if !meta.is_dir() || meta.file_type().is_symlink() { return Status::Assets }
        let Ok(root) = path.canonicalize() else { return Status::Assets };
        let id = NEXT.fetch_add(1, Ordering::Relaxed);
        if id == 0 || id == u64::MAX { return Status::Fault }
        registry().lock().unwrap_or_else(|p| p.into_inner()).insert(id, Arc::new(Request {
            root, cancelled: AtomicBool::new(false), active: AtomicBool::new(false)
        }));
        unsafe { *out_id = id };
        Status::Ok
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn sts_pocket_cancel(id: u64) -> Status {
    boundary(|| match request(id) {
        Some(req) => { req.cancelled.store(true, Ordering::Release); Status::Ok }
        None => Status::UnknownRequest,
    })
}

/// Close is idempotent. An in-flight synthesis owns an Arc and observes cancellation.
#[unsafe(no_mangle)]
pub extern "C" fn sts_pocket_close(id: u64) -> Status {
    boundary(|| {
        if let Some(req) = registry().lock().unwrap_or_else(|p| p.into_inner()).remove(&id) {
            req.cancelled.store(true, Ordering::Release);
        }
        Status::Ok
    })
}

struct OwnedText(Vec<u8>);
fn wipe_bytes(bytes: &mut [u8]) {
    for byte in bytes { unsafe { std::ptr::write_volatile(byte, 0) } }
    std::sync::atomic::compiler_fence(Ordering::SeqCst);
}
fn wipe_pcm(samples: &mut [i16]) {
    for sample in samples { unsafe { std::ptr::write_volatile(sample, 0) } }
    std::sync::atomic::compiler_fence(Ordering::SeqCst);
}
impl Drop for OwnedText { fn drop(&mut self) { wipe_bytes(&mut self.0); } }
struct Active<'a>(&'a AtomicBool);
impl Drop for Active<'_> { fn drop(&mut self) { self.0.store(false, Ordering::Release); } }

fn validated_text(bytes: &[u8]) -> Result<&str, Status> {
    if bytes.is_empty() || bytes.len() > MAX_TEXT_BYTES { return Err(Status::InvalidInput) }
    let value = std::str::from_utf8(bytes).map_err(|_| Status::InvalidInput)?;
    if value.contains('\0') || value.trim().is_empty() || value.chars().any(char::is_control) {
        return Err(Status::InvalidInput)
    }
    Ok(value)
}

struct PcmWriter {
    id: u64,
    sink: PcmSink,
    user: *mut std::ffi::c_void,
    buffer: [i16; PCM_CHUNK],
    used: usize,
    samples: usize,
    failed_sink: bool,
}
impl PcmWriter {
    fn new(id: u64, sink: PcmSink, user: *mut std::ffi::c_void) -> Self {
        Self { id, sink, user, buffer: [0; PCM_CHUNK], used: 0, samples: 0, failed_sink: false }
    }
    fn push(&mut self, samples: &[f32], cancelled: &AtomicBool) -> Result<(), Status> {
        for &sample in samples {
            if cancelled.load(Ordering::Acquire) { return Err(Status::Cancelled) }
            if !sample.is_finite() || self.samples >= 30 * 24_000 { return Err(Status::Model) }
            self.buffer[self.used] = (sample.clamp(-1.0, 1.0) * 32767.0).round() as i16;
            self.used += 1;
            self.samples += 1;
            if self.used == PCM_CHUNK { self.flush(cancelled)? }
        }
        Ok(())
    }
    fn flush(&mut self, cancelled: &AtomicBool) -> Result<(), Status> {
        if cancelled.load(Ordering::Acquire) { return Err(Status::Cancelled) }
        if self.used > 0 && !(self.sink)(self.id, self.buffer.as_ptr(), self.used, self.user) {
            self.failed_sink = true;
            return Err(Status::Sink)
        }
        wipe_pcm(&mut self.buffer);
        self.used = 0;
        Ok(())
    }
}
impl Drop for PcmWriter { fn drop(&mut self) { wipe_pcm(&mut self.buffer); } }

#[unsafe(no_mangle)]
pub extern "C" fn sts_pocket_synthesize(
    id: u64, text: *const u8, text_len: usize, sink: Option<PcmSink>, user: *mut std::ffi::c_void
) -> Status {
    boundary(|| {
        let Some(req) = request(id) else { return Status::UnknownRequest };
        if req.cancelled.load(Ordering::Acquire) { return Status::Cancelled }
        if text.is_null() || text_len == 0 || text_len > MAX_TEXT_BYTES || sink.is_none() {
            return Status::InvalidInput
        }
        if req.active.compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire).is_err() {
            return Status::Busy
        }
        let _active = Active(&req.active);
        let input = OwnedText(unsafe { std::slice::from_raw_parts(text, text_len) }.to_vec());
        let Ok(content) = validated_text(&input.0) else { return Status::InvalidInput };
        synthesize(&req, id, content, sink.unwrap(), user)
    })
}

fn checked_file(root: &Path, name: &str, size: u64, sha: &str, cancel: &AtomicBool) -> Result<PathBuf, Status> {
    if cancel.load(Ordering::Acquire) { return Err(Status::Cancelled) }
    let path = root.join(name);
    let meta = fs::symlink_metadata(&path).map_err(|_| Status::Assets)?;
    if !meta.is_file() || meta.file_type().is_symlink() || meta.len() != size { return Err(Status::Assets) }
    if path.canonicalize().map_err(|_| Status::Assets)?.parent() != Some(root) { return Err(Status::Assets) }
    let mut file = File::open(&path).map_err(|_| Status::Assets)?;
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 65536];
    loop {
        if cancel.load(Ordering::Acquire) { return Err(Status::Cancelled) }
        let count = file.read(&mut buffer).map_err(|_| Status::Assets)?;
        if count == 0 { break }
        digest.update(&buffer[..count]);
    }
    if format!("{:x}", digest.finalize()) != sha { return Err(Status::Assets) }
    Ok(path)
}

fn synthesize(req: &Request, id: u64, content: &str, sink: PcmSink, user: *mut std::ffi::c_void) -> Status {
    let assets = (|| {
        let weights = checked_file(&req.root, "model.safetensors", 111_728_528, MODEL_SHA, &req.cancelled)?;
        let tokens = checked_file(&req.root, "tokenizer.model", 59_339, TOKEN_SHA, &req.cancelled)?;
        let voice = checked_file(&req.root, "alba.safetensors", 4_163_992, VOICE_SHA, &req.cancelled)?;
        Ok::<_, Status>((weights, tokens, voice))
    })();
    let (weights, tokens, voice) = match assets { Ok(v) => v, Err(status) => return status };
    if req.cancelled.load(Ordering::Acquire) { return Status::Cancelled }
    let Ok(mut config) = Config::parse(include_str!("../english_18m_2026-10.upstream.yaml")) else {
        return Status::Fault
    };
    config.weights_path = Some(weights.to_string_lossy().into_owned());
    config.weights_path_without_voice_cloning = None;
    config.flow_lm.lookup_table.tokenizer_path = tokens.to_string_lossy().into_owned();
    let Ok(engine) = TtsModel::<Backend>::load(config, &LoadOptions::default()) else {
        return if req.cancelled.load(Ordering::Acquire) { Status::Cancelled } else { Status::Model }
    };
    if engine.sample_rate() != 24_000 { return Status::Fault }
    if req.cancelled.load(Ordering::Acquire) { return Status::Cancelled }
    let Ok(voice) = engine.import_alba_state(&voice) else { return Status::Model };
    if req.cancelled.load(Ordering::Acquire) { return Status::Cancelled }
    let Ok(token_count) = engine.preflight_tokens(content) else { return Status::InvalidInput };
    if token_count == 0 || token_count > MAX_TOKENS { return Status::InvalidInput }
    let mut pcm = PcmWriter::new(id, sink, user);
    let result = engine.generate_audio_stream(&voice, content, &req.cancelled, &mut |chunk| {
        pcm.push(chunk, &req.cancelled).map_err(|status| anyhow::anyhow!("PCM callback stopped: {status:?}"))
    });
    let finish = if result.is_ok() { pcm.flush(&req.cancelled) } else { Ok(()) };
    completion_status(&req.cancelled, &pcm, result.is_ok(), finish.is_ok())
}

fn completion_status(cancelled: &AtomicBool, pcm: &PcmWriter, generated: bool, flushed: bool) -> Status {
    if cancelled.load(Ordering::Acquire) { Status::Cancelled }
    else if pcm.failed_sink { Status::Sink }
    else if !generated || !flushed { Status::Model }
    else { Status::Ok }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static CALLBACKS: AtomicUsize = AtomicUsize::new(0);
    extern "C" fn accepting(_id: u64, data: *const i16, count: usize, _user: *mut std::ffi::c_void) -> bool {
        assert!(!data.is_null());
        assert!(count > 0 && count <= PCM_CHUNK);
        CALLBACKS.fetch_add(1, Ordering::SeqCst);
        true
    }
    extern "C" fn rejecting(_id: u64, _data: *const i16, _count: usize, _user: *mut std::ffi::c_void) -> bool {
        false
    }
    extern "C" fn counting(_id: u64, _data: *const i16, _count: usize, user: *mut std::ffi::c_void) -> bool {
        let calls = unsafe { &*(user as *const AtomicUsize) };
        calls.fetch_add(1, Ordering::SeqCst);
        true
    }
    #[test]
    fn text_rejects_invalid_utf8_nul_controls_and_bounds() {
        assert_eq!(validated_text(&[0xff]), Err(Status::InvalidInput));
        assert_eq!(validated_text(b"a\0b"), Err(Status::InvalidInput));
        assert_eq!(validated_text(b"a\nb"), Err(Status::InvalidInput));
        assert_eq!(validated_text(b"  "), Err(Status::InvalidInput));
        assert_eq!(validated_text(&vec![b'a'; MAX_TEXT_BYTES + 1]), Err(Status::InvalidInput));
        assert_eq!(validated_text("Café — hello".as_bytes()), Ok("Café — hello"));
    }
    #[test]
    fn pcm_is_finite_and_bounded() {
        CALLBACKS.store(0, Ordering::SeqCst);
        let cancelled = AtomicBool::new(false);
        let mut pcm = PcmWriter::new(11, accepting, std::ptr::null_mut());
        pcm.push(&[1.0, -1.0, 0.0], &cancelled).unwrap();
        assert_eq!(&pcm.buffer[..3], &[32767, -32767, 0]);
        assert_eq!(pcm.push(&[f32::NAN], &cancelled), Err(Status::Model));
        pcm.push(&vec![0.25; PCM_CHUNK - 3], &cancelled).unwrap();
        assert_eq!(CALLBACKS.load(Ordering::SeqCst), 1);
        assert_eq!(pcm.used, 0);
        pcm.flush(&cancelled).unwrap();
    }
    fn assert_invalid_frame_is_atomic(invalid: f32) {
        let cancelled = AtomicBool::new(false);
        let calls = AtomicUsize::new(0);
        let mut pcm = PcmWriter::new(13, counting, (&calls as *const AtomicUsize).cast_mut().cast());
        pcm.push(&[0.25], &cancelled).unwrap();
        let before = pcm.buffer;
        let mut frame = vec![0.0; PCM_CHUNK];
        frame[PCM_CHUNK - 1] = invalid;
        assert_eq!(pcm.push(&frame, &cancelled), Err(Status::Model));
        assert_eq!(calls.load(Ordering::SeqCst), 0, "invalid frame reached the sink");
        assert_eq!(pcm.used, 1, "invalid frame changed the pending sample count");
        assert_eq!(pcm.samples, 1, "invalid frame changed the sample budget");
        assert_eq!(pcm.buffer, before, "invalid frame changed pending PCM");
    }
    #[test]
    fn out_of_range_frame_is_rejected_atomically() {
        assert_invalid_frame_is_atomic(1.01);
    }
    #[test]
    fn nonfinite_frame_is_rejected_atomically() {
        assert_invalid_frame_is_atomic(f32::NAN);
    }
    #[test]
    fn completed_generation_without_pcm_is_model_error() {
        let cancelled = AtomicBool::new(false);
        let mut pcm = PcmWriter::new(14, accepting, std::ptr::null_mut());
        pcm.flush(&cancelled).unwrap();
        assert_eq!(pcm.samples, 0);
        assert_eq!(completion_status(&cancelled, &pcm, true, true), Status::Model);
    }
    #[test]
    fn completion_preserves_cancel_and_sink_precedence() {
        let cancelled = AtomicBool::new(false);
        let mut pcm = PcmWriter::new(15, rejecting, std::ptr::null_mut());
        assert_eq!(pcm.push(&vec![0.0; PCM_CHUNK], &cancelled), Err(Status::Sink));
        assert_eq!(completion_status(&cancelled, &pcm, false, true), Status::Sink);
        cancelled.store(true, Ordering::Release);
        assert_eq!(completion_status(&cancelled, &pcm, false, true), Status::Cancelled);
    }
    #[test]
    fn sink_failure_and_cancel_abort_synchronously() {
        let cancelled = AtomicBool::new(false);
        let mut pcm = PcmWriter::new(12, rejecting, std::ptr::null_mut());
        assert_eq!(pcm.push(&vec![0.0; PCM_CHUNK], &cancelled), Err(Status::Sink));
        assert!(pcm.failed_sink);
        cancelled.store(true, Ordering::Release);
        assert_eq!(pcm.push(&[0.0], &cancelled), Err(Status::Cancelled));
    }
    #[test]
    fn close_retains_inflight_owner_and_is_idempotent() {
        let id = NEXT.fetch_add(1, Ordering::Relaxed);
        let req = Arc::new(Request {
            root: PathBuf::from("/unused"),
            cancelled: AtomicBool::new(false), active: AtomicBool::new(true)
        });
        registry().lock().unwrap().insert(id, req.clone());
        let owner = request(id).unwrap();
        assert_eq!(sts_pocket_cancel(id), Status::Ok);
        assert!(owner.cancelled.load(Ordering::Acquire));
        assert_eq!(sts_pocket_close(id), Status::Ok);
        assert_eq!(sts_pocket_close(id), Status::Ok);
        assert!(request(id).is_none());
        assert!(Arc::strong_count(&owner) >= 2);
    }
    #[test]
    fn missing_asset_cannot_enter_model_loader() {
        let cancel = AtomicBool::new(false);
        assert_eq!(checked_file(Path::new("/missing"), "model.safetensors", 1, "0", &cancel).err(), Some(Status::Assets));
        cancel.store(true, Ordering::Release);
        assert_eq!(checked_file(Path::new("/missing"), "model.safetensors", 1, "0", &cancel).err(), Some(Status::Cancelled));
    }
}
