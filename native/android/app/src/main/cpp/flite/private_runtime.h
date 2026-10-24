#ifndef PRIVATE_RUNTIME_H
#define PRIVATE_RUNTIME_H
#include "private_tts.h"
void stts_checkpoint(void);
unsigned stts_noise_bit(void);
int stts_checked_atoi(const char *text);
double stts_checked_atof(const char *text);
int stts_checked_float_int(double value);
short stts_checked_sample(double value);
__attribute__((noreturn)) void stts_fail(int status);
#endif
