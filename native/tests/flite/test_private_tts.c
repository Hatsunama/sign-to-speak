/* Synthetic inputs only. This tests the actual source-built engine, not playback. */
#include "private_tts.h"
#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <sched.h>
#include <limits.h>

extern void stts_test_fail_allocation(size_t number);
extern size_t stts_test_live_allocations(void);
extern size_t stts_test_wipe_checks(void);
extern size_t stts_test_checkpoints(void);
extern void stts_test_fail_output(int fail);
extern void stts_test_exhaust_limit(int limit);
extern void stts_test_force_duration(int enabled);
extern int stts_test_integer(const char *text, int *out);
extern int stts_test_float(const char *text, double *out);
extern void stts_test_pause_checkpoint(size_t number);
extern int stts_test_checkpoint_paused(void);
extern void stts_test_resume_checkpoint(void);

static unsigned checks;
#define CHECK(x) do { ++checks; assert(x); } while (0)
static const uint8_t phrase[] = "Hello. This is a local speech test.";
static stts_status speak(stts_cancel_token *token, stts_pcm *pcm) {
    return stts_synthesize(phrase, sizeof(phrase) - 1, token, pcm);
}
static void zero_pcm(const stts_pcm *pcm) {
    CHECK(!pcm->samples && !pcm->count && !pcm->sample_rate && !pcm->channels);
}
static void success(void) {
    stts_pcm pcm = {0};
    CHECK(speak(NULL, &pcm) == STTS_OK);
    CHECK(pcm.samples && pcm.count > 0 && pcm.count <= STTS_MAX_PCM_SAMPLES);
    CHECK(pcm.sample_rate == 16000 && pcm.channels == 1);
    int nonzero = 0;
    for (size_t i = 0; i < pcm.count; ++i) nonzero |= pcm.samples[i] != 0;
    CHECK(nonzero);
    CHECK(stts_test_live_allocations() == 0);
    int16_t *original = pcm.samples;
    CHECK(speak(NULL, &pcm) == STTS_INPUT);
    CHECK(pcm.samples == original);
    stts_pcm_release(&pcm);
    zero_pcm(&pcm);
    stts_pcm_release(&pcm);
    zero_pcm(&pcm);
}
typedef struct { stts_cancel_token token; stts_pcm pcm; stts_status status; } work;
static void *worker(void *raw) {
    work *w = raw;
    w->status = speak(&w->token, &w->pcm);
    return NULL;
}
static void exercise(const uint8_t *text, size_t length, int expected, int alternative) {
    static int synthetic_case;
    ++synthetic_case;
    stts_pcm pcm = {0};
    stts_status status = stts_synthesize(text, length, NULL, &pcm);
    if (status != expected && status != alternative)
        fprintf(stderr, "Synthetic case %d returned status %d; expected %d or %d\n", synthetic_case, status, expected, alternative);
    CHECK(status == expected || status == alternative);
    if (status == STTS_OK) {
        CHECK(pcm.samples && pcm.count && pcm.count <= STTS_MAX_PCM_SAMPLES);
        CHECK(pcm.sample_rate == 16000 && pcm.channels == 1);
        stts_pcm_release(&pcm);
    }
    zero_pcm(&pcm);
    CHECK(stts_test_live_allocations() == 0);
    success();
}
int main(void) {
    stts_pcm pcm = {0};
    uint8_t oversized[STTS_MAX_TEXT_BYTES + 1]; memset(oversized, 'a', sizeof(oversized));
    CHECK(stts_synthesize(NULL, 0, NULL, &pcm) == STTS_INPUT);
    CHECK(stts_synthesize((const uint8_t *)"", 0, NULL, &pcm) == STTS_INPUT);
    CHECK(stts_synthesize((const uint8_t *)" \t\r\n", 4, NULL, &pcm) == STTS_INPUT);
    CHECK(stts_synthesize(oversized, sizeof(oversized), NULL, &pcm) == STTS_INPUT);
    CHECK(stts_synthesize((const uint8_t *)"a\0b", 3, NULL, &pcm) == STTS_INPUT);
    CHECK(stts_synthesize((const uint8_t *)"\xc3\xa9", 2, NULL, &pcm) == STTS_INPUT);
    CHECK(stts_synthesize(phrase, sizeof(phrase) - 1, NULL, NULL) == STTS_INPUT);
    zero_pcm(&pcm);
    stts_cancel_token canceled = {0}; stts_cancel(&canceled);
    CHECK(speak(&canceled, &pcm) == STTS_CANCELLED); zero_pcm(&pcm);
    CHECK(stts_test_live_allocations() == 0);
    success();
    int parsed = 0;
    const char *integers[] = {"2147483647", "-2147483648", "00000000000000000012", "12tail", "12e3", "0x10", "+", "-", " \t\n\r\f\v+42"};
    int expected[] = {INT_MAX, INT_MIN, 12, 12, 12, 0, 0, 0, 42};
    for (size_t i = 0; i < sizeof(expected)/sizeof(expected[0]); ++i) {
        CHECK(stts_test_integer(integers[i], &parsed) == STTS_OK); CHECK(parsed == expected[i]);
    }
    CHECK(stts_test_integer("2147483648", &parsed) == STTS_INPUT);
    CHECK(stts_test_integer("-2147483649", &parsed) == STTS_INPUT);
    CHECK(stts_test_integer("99999999999999999999999", &parsed) == STTS_INPUT);
    CHECK(stts_test_integer(NULL, &parsed) == STTS_INPUT);
    double floating = 0;
    CHECK(stts_test_float("1.5tail", &floating) == STTS_OK); CHECK(floating == 1.5);
    const char *invalid_float[] = {"1e9999", "1e-9999", "nan", "inf", "3.5e38"};
    for (size_t i = 0; i < sizeof(invalid_float)/sizeof(invalid_float[0]); ++i)
        CHECK(stts_test_float(invalid_float[i], &floating) == STTS_INPUT);
    uint8_t maximum[256]; memset(maximum, ' ', sizeof(maximum)); memcpy(maximum, "Hello.", 6);
    exercise(maximum, sizeof(maximum), STTS_OK, STTS_OK);
    const char *fractions[] = {"1/2", "2/3", "0/0", "1/0", "3/2", "22-23 November", "1998-1999", "1--2"};
    for (size_t i = 0; i < sizeof(fractions)/sizeof(fractions[0]); ++i)
        exercise((const uint8_t *)fractions[i], strlen(fractions[i]), STTS_OK, STTS_OK);
    const char *overflow[] = {"2147483648/2", "1/2147483648", "2147483648-2147483649"};
    for (size_t i = 0; i < sizeof(overflow)/sizeof(overflow[0]); ++i)
        exercise((const uint8_t *)overflow[i], strlen(overflow[i]), STTS_INPUT, STTS_INPUT);
    const char *punctuation[] = {"!!!", "...", "--", "/", "$", "%"};
    for (size_t i = 0; i < sizeof(punctuation)/sizeof(punctuation[0]); ++i)
        exercise((const uint8_t *)punctuation[i], strlen(punctuation[i]), STTS_OK, STTS_FORMAT);
    uint8_t long_token[256]; memset(long_token, '9', sizeof(long_token));
    exercise(long_token, sizeof(long_token), STTS_MEMORY, STTS_FORMAT);
    memset(long_token, 'a', sizeof(long_token));
    exercise(long_token, sizeof(long_token), STTS_OK, STTS_FORMAT);
    uint8_t long_speech[200];
    for (size_t i = 0; i < sizeof(long_speech); i += 2) { long_speech[i] = 'W'; long_speech[i + 1] = ' '; }
    exercise(long_speech, sizeof(long_speech), STTS_FORMAT, STTS_MEMORY);
    stts_test_force_duration(1);
    CHECK(speak(NULL, &pcm) == STTS_FORMAT); zero_pcm(&pcm);
    CHECK(stts_test_live_allocations() == 0);
    stts_test_force_duration(0); success();
    for (int limit = 1; limit <= 2; ++limit) {
        stts_test_exhaust_limit(limit);
        CHECK(speak(NULL, &pcm) == STTS_MEMORY); zero_pcm(&pcm);
        CHECK(stts_test_live_allocations() == 0);
        stts_test_exhaust_limit(0); success();
    }
    /* Each fault must unwind without exit(), text logging or stale global voice. */
    for (size_t fault = 1; fault <= 1024; fault *= 4) {
        stts_test_fail_allocation(fault);
        CHECK(speak(NULL, &pcm) == STTS_MEMORY); zero_pcm(&pcm);
        CHECK(stts_test_live_allocations() == 0);
        stts_test_fail_allocation(0);
        success();
    }
    /* Deterministically cancel an in-flight real synthesis at an allocation
     * checkpoint, and reject a concurrent call without touching its output. */
    stts_test_fail_output(1);
    CHECK(speak(NULL, &pcm) == STTS_MEMORY); zero_pcm(&pcm);
    CHECK(stts_test_live_allocations() == 0);
    stts_test_fail_output(0);
    success();
    size_t last_checkpoint = stts_test_checkpoints();
    CHECK(last_checkpoint > 256);
    size_t boundaries[] = {256, last_checkpoint};
    for (size_t i = 0; i < sizeof(boundaries) / sizeof(boundaries[0]); ++i) {
        work w = {0}; pthread_t thread;
        stts_test_pause_checkpoint(boundaries[i]);
        CHECK(pthread_create(&thread, NULL, worker, &w) == 0);
        while (!stts_test_checkpoint_paused()) sched_yield();
        CHECK(speak(NULL, &pcm) == STTS_BUSY); zero_pcm(&pcm);
        stts_cancel(&w.token);
        stts_test_resume_checkpoint();
        CHECK(pthread_join(thread, NULL) == 0);
        CHECK(w.status == STTS_CANCELLED); zero_pcm(&w.pcm);
        CHECK(stts_test_live_allocations() == 0);
        stts_test_pause_checkpoint(0);
        success();
    }
    CHECK(stts_test_wipe_checks() > 100);
    printf("PASS: %u boundary/recovery/ownership assertions; tracked frees checked after zeroing; synthetic PCM only, no playback\n", checks);
    return 0;
}
