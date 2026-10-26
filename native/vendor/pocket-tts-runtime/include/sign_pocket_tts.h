/* Sign to Speak local Alba 18M PCM component. Apache-2.0; adapted code MIT. */
#ifndef SIGN_POCKET_TTS_H
#define SIGN_POCKET_TTS_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef enum sts_pocket_status {
  STS_POCKET_OK = 0,
  STS_POCKET_CANCELLED = 1,
  STS_POCKET_INVALID_INPUT = 2,
  STS_POCKET_ASSETS = 3,
  STS_POCKET_MODEL = 4,
  STS_POCKET_SINK = 5,
  STS_POCKET_FAULT = 6,
  STS_POCKET_UNKNOWN_REQUEST = 7,
  STS_POCKET_BUSY = 8
} sts_pocket_status;

/* Callback receives at most 1920 samples of signed 16-bit mono PCM at 24000 Hz.
   Buffer ownership stays with the component and ends when the callback returns.
   Return false to abort. The callback runs synchronously on the synthesize caller's thread. */
typedef bool (*sts_pocket_pcm_sink)(uint64_t request_id,
                                    const int16_t *samples,
                                    size_t sample_count,
                                    void *user);

/* UTF-8 absolute directory containing precisely named model.safetensors,
   tokenizer.model, alba.safetensors. Creation registers a cheap cancellable ID;
   the three pinned asset hashes are verified at synthesize time. */
sts_pocket_status sts_pocket_create(const uint8_t *root, size_t root_len,
                                    uint64_t *out_id);
/* UTF-8 reviewed text, 1..512 bytes, at most 128 prepared SentencePiece IDs.
   This call blocks, loads a private model instance, and streams PCM synchronously.
   A given ID permits one synthesize call at a time. */
sts_pocket_status sts_pocket_synthesize(uint64_t request_id,
                                        const uint8_t *text, size_t text_len,
                                        sts_pocket_pcm_sink sink, void *user);
sts_pocket_status sts_pocket_cancel(uint64_t request_id);
/* Idempotent. Cancels an active synthesis; its retained Arc remains live until
   that call returns. Caller must not unload the library before all calls return. */
sts_pocket_status sts_pocket_close(uint64_t request_id);

#ifdef __cplusplus
}
#endif
#endif
