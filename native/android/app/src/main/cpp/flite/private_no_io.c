/* Memory formatting is retained. Every file operation fails inside the request
 * guard, without accessing a path, descriptor, socket, device or log sink. */
#include "private_runtime.h"
#include "cst_file.h"
#include <stdarg.h>
#include <stdio.h>
int cst_sprintf(char *s, const char *format, ...) {
    va_list args; va_start(args, format); int result = vsprintf(s, format, args); va_end(args); return result;
}
cst_file cst_fopen(const char *p, int m) { (void)p; (void)m; stts_fail(STTS_ENGINE); }
long cst_fwrite(cst_file f, const void *b, long s, long n) { (void)f;(void)b;(void)s;(void)n;stts_fail(STTS_ENGINE); }
long cst_fread(cst_file f, void *b, long s, long n) { (void)f;(void)b;(void)s;(void)n;stts_fail(STTS_ENGINE); }
int cst_fprintf(cst_file f, const char *s, ...) { (void)f;(void)s;stts_fail(STTS_ENGINE); }
int cst_fclose(cst_file f) { (void)f;stts_fail(STTS_ENGINE); }
int cst_fgetc(cst_file f) { (void)f;stts_fail(STTS_ENGINE); }
long cst_ftell(cst_file f) { (void)f;stts_fail(STTS_ENGINE); }
long cst_fseek(cst_file f, long p, int w) { (void)f;(void)p;(void)w;stts_fail(STTS_ENGINE); }
long cst_filesize(cst_file f) { (void)f;stts_fail(STTS_ENGINE); }
cst_filemap *cst_mmap_file(const char *p) { (void)p;stts_fail(STTS_ENGINE); }
int cst_munmap_file(cst_filemap *m) { (void)m;stts_fail(STTS_ENGINE); }
cst_filemap *cst_read_whole_file(const char *p) { (void)p;stts_fail(STTS_ENGINE); }
int cst_free_whole_file(cst_filemap *m) { (void)m;stts_fail(STTS_ENGINE); }
cst_filemap *cst_read_part_file(const char *p) { (void)p;stts_fail(STTS_ENGINE); }
int cst_free_part_file(cst_filemap *m) { (void)m;stts_fail(STTS_ENGINE); }
int cst_urlp(const char *p) { (void)p;stts_fail(STTS_ENGINE); }
cst_file cst_url_open(const char *p) { (void)p;stts_fail(STTS_ENGINE); }
