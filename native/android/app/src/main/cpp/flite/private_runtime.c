/* Isolated candidate wrapper. Upstream files remain separately attributed.
 * All engine allocations are scoped to one serialized request and zeroed when
 * released. This is not a claim to erase upstream stack copies or CPU registers.
 */
#include "private_runtime.h"
#include "flite.h"
#include <setjmp.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include <errno.h>
#include <float.h>
#include <math.h>

extern cst_voice *register_cmu_us_slt(const char *);
extern cst_voice *cmu_us_slt_cg;
static atomic_flag busy = ATOMIC_FLAG_INIT;
static jmp_buf failure;
static int failure_status;
static int16_t *unpublished_pcm;
static size_t unpublished_count;
static const stts_cancel_token *cancel_token;
typedef union block block;
union block {
    max_align_t alignment;
    struct { block *next, *previous; size_t size; } meta;
};
static block *allocations;
static size_t live_bytes, live_count;
static uint32_t noise_state;
#define ARENA_LIMIT (32u * 1024u * 1024u)
#define ALLOCATION_LIMIT 100000u

#ifdef STTS_TESTING
static size_t allocation_attempt, fault_at, wipe_checks, checkpoint_count;
static int fail_output;
static int exhaust_limit;
static int force_duration;
static _Atomic size_t pause_at;
static _Atomic int paused, resume_checkpoint;
void stts_test_fail_allocation(size_t n) { fault_at = n; }
size_t stts_test_live_allocations(void) { return live_count + (unpublished_pcm != NULL); }
size_t stts_test_wipe_checks(void) { return wipe_checks; }
size_t stts_test_checkpoints(void) { return checkpoint_count; }
void stts_test_fail_output(int fail) { fail_output = fail; }
void stts_test_exhaust_limit(int limit) { exhaust_limit = limit; }
void stts_test_force_duration(int enabled) { force_duration = enabled; }
void stts_test_pause_checkpoint(size_t n) {
    atomic_store(&paused, 0); atomic_store(&resume_checkpoint, 0); atomic_store(&pause_at, n);
}
int stts_test_checkpoint_paused(void) { return atomic_load(&paused); }
void stts_test_resume_checkpoint(void) { atomic_store(&resume_checkpoint, 1); }
#endif

static void wipe(void *memory, size_t size) {
    volatile unsigned char *p = memory;
#if !defined(STTS_TESTING) || !defined(STTS_NEGATIVE_SKIP_WIPE)
    for (size_t i = 0; i < size; ++i) p[i] = 0;
#endif
#ifdef STTS_TESTING
    for (size_t i = 0; i < size; ++i) if (p[i]) __builtin_trap();
    ++wipe_checks;
#endif
}
void stts_fail(int status) { failure_status = status; longjmp(failure, 1); }
int stts_checked_atoi(const char *text) {
    if (!text) stts_fail(STTS_INPUT);
    const unsigned char *p = (const unsigned char *)text;
    while (*p == ' ' || (*p >= '\t' && *p <= '\r')) ++p;
    int negative = *p == '-';
    if (*p == '+' || *p == '-') ++p;
    unsigned value = 0, limit = (unsigned)INT_MAX + (unsigned)negative;
    while (*p >= '0' && *p <= '9') {
        unsigned digit = *p++ - '0';
        if (value > (limit - digit) / 10u) stts_fail(STTS_INPUT);
        value = value * 10u + digit;
    }
    if (!negative) return (int)value;
    return value == (unsigned)INT_MAX + 1u ? INT_MIN : -(int)value;
}
double stts_checked_atof(const char *text) {
    if (!text) stts_fail(STTS_INPUT);
    errno = 0;
    double value = strtod(text, NULL);
    if (errno == ERANGE || !isfinite(value) || fabs(value) > FLT_MAX) stts_fail(STTS_INPUT);
    return value;
}
int stts_checked_float_int(double value) {
    if (!isfinite(value) || value < INT_MIN || value > INT_MAX) stts_fail(STTS_INPUT);
    return (int)value;
}
short stts_checked_sample(double value) {
    if (!isfinite(value) || value < SHRT_MIN || value > SHRT_MAX) stts_fail(STTS_FORMAT);
    return (short)value;
}
#ifdef STTS_TESTING
int stts_test_integer(const char *text, int *out) {
    failure_status = 0;
    if (setjmp(failure) == 0) *out = stts_checked_atoi(text);
    return failure_status;
}
int stts_test_float(const char *text, double *out) {
    failure_status = 0;
    if (setjmp(failure) == 0) *out = stts_checked_atof(text);
    return failure_status;
}
#endif
unsigned stts_noise_bit(void) {
    /* Per-request xorshift32 excitation sign; no process-global libc rand state. */
    noise_state ^= noise_state << 13; noise_state ^= noise_state >> 17; noise_state ^= noise_state << 5;
    return noise_state >> 31;
}
void stts_checkpoint(void) {
#ifdef STTS_TESTING
    if (++checkpoint_count == atomic_load(&pause_at)) {
        atomic_store(&paused, 1);
        while (!atomic_load(&resume_checkpoint)) { /* test-only deterministic race boundary */ }
    }
#endif
    if (cancel_token && __atomic_load_n(&cancel_token->requested, __ATOMIC_ACQUIRE))
        stts_fail(STTS_CANCELLED);
}

void *cst_safe_alloc(int requested) {
    stts_checkpoint();
#ifdef STTS_TESTING
    if (++allocation_attempt == fault_at) stts_fail(STTS_MEMORY);
#endif
    if (requested < 0) stts_fail(STTS_MEMORY);
    size_t size = requested ? (size_t)requested : 1;
    if (size > ARENA_LIMIT - live_bytes || live_count >= ALLOCATION_LIMIT) stts_fail(STTS_MEMORY);
    block *b = calloc(1, sizeof(*b) + size);
    if (!b) stts_fail(STTS_MEMORY);
    b->meta.size = size; b->meta.next = allocations;
    if (allocations) allocations->meta.previous = b;
    allocations = b; live_bytes += size; ++live_count;
    return b + 1;
}
void *cst_safe_calloc(int size) { return cst_safe_alloc(size); }
void cst_free(void *memory) {
    if (!memory) return;
    block *b = (block *)memory - 1;
    if (b->meta.previous) b->meta.previous->meta.next = b->meta.next;
    else allocations = b->meta.next;
    if (b->meta.next) b->meta.next->meta.previous = b->meta.previous;
    size_t size = b->meta.size;
    live_bytes -= size; --live_count;
    wipe(b, sizeof(*b) + size); free(b);
}
void *cst_safe_realloc(void *memory, int requested) {
    if (!memory) return cst_safe_alloc(requested);
    block *old = (block *)memory - 1;
    void *replacement = cst_safe_alloc(requested);
    size_t size = requested ? (size_t)requested : 1;
    memcpy(replacement, memory, old->meta.size < size ? old->meta.size : size);
    cst_free(memory);
    return replacement;
}
/* Never format or log the message or variadic input. Fatal callers separately
 * invoke the guarded cst_error macro; nonfatal diagnostics remain silent. */
int cst_errmsg(const char *format, ...) { (void)format; return 0; }

void stts_cancel(stts_cancel_token *token) {
    if (token) __atomic_store_n(&token->requested, 1, __ATOMIC_RELEASE);
}
void stts_pcm_release(stts_pcm *pcm) {
    if (!pcm) return;
    if (pcm->samples) { wipe(pcm->samples, pcm->count * sizeof(*pcm->samples)); free(pcm->samples); }
    wipe(pcm, sizeof(*pcm));
}

stts_status stts_synthesize(const uint8_t *text, size_t length,
                           const stts_cancel_token *token, stts_pcm *out) {
    if (!out || out->samples || out->count || out->sample_rate || out->channels ||
        !text || !length || length > STTS_MAX_TEXT_BYTES) return STTS_INPUT;
    int content = 0;
    for (size_t i = 0; i < length; ++i) {
        if (text[i] > 126 || (text[i] < 32 && text[i] != '\n' && text[i] != '\r' && text[i] != '\t'))
            return STTS_INPUT;
        content |= text[i] > 32;
    }
    if (!content) return STTS_INPUT;
    if (atomic_flag_test_and_set_explicit(&busy, memory_order_acquire)) return STTS_BUSY;
    char owned_text[STTS_MAX_TEXT_BYTES + 1] = {0};
    memcpy(owned_text, text, length);
    cancel_token = token;
    noise_state = 1;
    failure_status = 0;
#ifdef STTS_TESTING
    allocation_attempt = checkpoint_count = 0;
#endif
    if (setjmp(failure) == 0) {
        stts_checkpoint();
#ifdef STTS_TESTING
        if (exhaust_limit == 1) { cst_safe_alloc(ARENA_LIMIT); cst_safe_alloc(1); }
        if (exhaust_limit == 2) {
            for (size_t i = 0; i <= ALLOCATION_LIMIT; ++i) cst_safe_alloc(1);
        }
#endif
        if (flite_init()) stts_fail(STTS_ENGINE);
        cst_voice *voice = register_cmu_us_slt(NULL);
        if (!voice || feat_present(voice->features, "streaming_info")) stts_fail(STTS_ENGINE);
#ifdef STTS_TESTING
        if (force_duration) feat_set_float(voice->features, "duration_stretch", 100.0f);
#endif
        cst_wave *wave = flite_text_to_wave(owned_text, voice);
        stts_checkpoint();
        if (!wave || !wave->samples || wave->num_samples <= 0 ||
            wave->num_samples > STTS_MAX_PCM_SAMPLES || wave->sample_rate != 16000 ||
            wave->num_channels != 1 || sizeof(short) != sizeof(int16_t)) stts_fail(STTS_FORMAT);
        /* Publish only after every failing/cancelable operation. This independent
         * result survives request-arena cleanup and belongs solely to the caller. */
        unpublished_count = (size_t)wave->num_samples;
#ifdef STTS_TESTING
        if (fail_output) stts_fail(STTS_MEMORY);
#endif
        unpublished_pcm = malloc(unpublished_count * sizeof(int16_t));
        if (!unpublished_pcm) stts_fail(STTS_MEMORY);
        memcpy(unpublished_pcm, wave->samples, unpublished_count * sizeof(int16_t));
        stts_checkpoint();
        out->samples = unpublished_pcm; out->count = unpublished_count;
        out->sample_rate = 16000; out->channels = 1;
        unpublished_pcm = NULL; unpublished_count = 0;
    }
    /* No persistent voice survives success, cancellation or fatal error, so
     * skipped reference-count decrements cannot corrupt a later request. */
    cmu_us_slt_cg = NULL;
    while (allocations) cst_free(allocations + 1);
    if (unpublished_pcm) {
        wipe(unpublished_pcm, unpublished_count * sizeof(int16_t)); free(unpublished_pcm);
        unpublished_pcm = NULL;
    }
    unpublished_count = 0;
    cancel_token = NULL;
    noise_state = 0;
    wipe(owned_text, sizeof(owned_text));
    int status = failure_status;
    atomic_flag_clear_explicit(&busy, memory_order_release);
    return (stts_status)status;
}
