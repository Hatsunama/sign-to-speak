#ifndef PRIVATE_TTS_H
#define PRIVATE_TTS_H
#include <stddef.h>
#include <stdint.h>

#define STTS_MAX_TEXT_BYTES 256
#define STTS_MAX_PCM_SAMPLES (16000 * 30)

/* Candidate scope: bounded ASCII English; one synthesis at a time. No playback. */
typedef enum {
    STTS_OK = 0, STTS_INPUT = 1, STTS_BUSY = 2, STTS_CANCELLED = 3,
    STTS_MEMORY = 4, STTS_ENGINE = 5, STTS_FORMAT = 6
} stts_status;
typedef struct { unsigned requested; } stts_cancel_token;
typedef struct {
    int16_t *samples;
    size_t count;
    unsigned sample_rate;
    unsigned channels;
} stts_pcm;

#if defined(__GNUC__)
#define STTS_API __attribute__((visibility("default")))
#else
#define STTS_API
#endif

#ifdef __cplusplus
extern "C" {
#endif
/* out and token must be zero-initialized and caller-owned. token must live until
 * return, and may only be changed through stts_cancel while in flight. The input
 * must remain alive and immutable until return; the caller retains/wipes it.
 * Success returns separately owned PCM; release it
 * exactly once (repeat release on the same cleared struct is harmless).
 * Do not modify PCM pointer/count/rate/channel metadata until release. Cancellation
 * may race with final publication, so consumers must recheck their session/request
 * generation before playback or any other use, even after a successful return.
 * Cancellation is cooperative, checked inside synthesis; it never longjmps on
 * the requesting thread. Callers must not free token/input/out while in flight.
 */
STTS_API stts_status stts_synthesize(const uint8_t *text, size_t length,
                                    const stts_cancel_token *token, stts_pcm *out);
STTS_API void stts_cancel(stts_cancel_token *token);
STTS_API void stts_pcm_release(stts_pcm *pcm);
#ifdef __cplusplus
}
#endif
#endif
