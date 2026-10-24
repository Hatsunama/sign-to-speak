"""Verify the approved source inventory and generate reviewable derivative files.

This script has no network access. Original source and notices stay unchanged.
"""
import difflib
import hashlib
import json
from pathlib import Path
import sys

if len(sys.argv) > 2 or (len(sys.argv) == 2 and sys.argv[1] != '--check'):
    raise SystemExit('usage: prepare_sources.py [--check]')
CHECK = len(sys.argv) == 2
ROOT = Path(__file__).resolve().parent
approved = ROOT / 'source-manifest.json'
if hashlib.sha256(approved.read_bytes()).hexdigest() != 'e0969bcb5a34628e66f93517e9cbdb092252988c5920007e18c46f595dc54cd8':
    raise ValueError('Approved source manifest identity changed')
approved_files = json.loads(approved.read_text())['files']
manifest = json.loads((ROOT / 'source-receipt.json').read_text())
if [{k: v for k, v in f.items() if k != 'sha256'} for f in manifest['files']] != approved_files:
    raise ValueError('Source receipt differs from approved inventory')
source = ROOT / 'upstream'
for entry in manifest['files']:
    data = (source / entry['path']).read_bytes()
    if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
        raise ValueError('Unreviewed source: ' + entry['path'])
    if hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() != entry['git_blob_sha1']:
        raise ValueError('Approved Git blob mismatch: ' + entry['path'])
generated = ROOT / 'generated'
if not CHECK:
    (generated / 'include').mkdir(parents=True, exist_ok=True)
expected_paths = set()
drift = []
def write_changed(path, data):
    expected_paths.add(path)
    if CHECK:
        if not path.is_file():
            drift.append('Missing: ' + path.relative_to(ROOT).as_posix())
        elif path.read_bytes() != data:
            drift.append('Changed: ' + path.relative_to(ROOT).as_posix())
    elif not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)
for header in sorted((source / 'include').glob('*.h')):
    # The modified error header is emitted below; do not first touch its mtime.
    if header.name != 'cst_error.h':
        write_changed(generated / 'include' / header.name, header.read_bytes())
patches = []


def modify(path, replacements, output):
    old = (source / path).read_text()
    new = old
    for before, after in replacements:
        if new.count(before) != 1:
            raise ValueError('Patch anchor changed: ' + path)
        new = new.replace(before, after)
    new = '/* Modified for isolated Sign To Speak candidate; see modifications.patch. */\n' + new
    write_changed(output, new.encode())
    patches.append(''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                    fromfile='upstream/' + path, tofile='generated/' + path)))


modify('include/cst_error.h', [
    ('# define cst_error() (cst_errjmp ? longjmp(*cst_errjmp,1) : exit(-1))',
     '#include "private_runtime.h"\n# define cst_error() stts_fail(STTS_ENGINE)')
], generated / 'include/cst_error.h')
modify('src/cg/cst_mlsa.c', [
    ('#ifdef ANDROID\n#define SPEED_HACK\n#endif',
     '/* Candidate keeps the full desktop vocoder order on Android as well. */'),
    ('    wave = new_wave();\n    cst_wave_resize(wave,params->num_frames * framel,1);',
     '    if (asi || fs != 16000 || framel <= 0 || params->num_frames <= 0 ||\n'
     '        params->num_frames > STTS_MAX_PCM_SAMPLES / framel) stts_fail(STTS_FORMAT);\n'
     '    wave = new_wave();\n    cst_wave_resize(wave,params->num_frames * framel,1);'),
    ('        f0 = (double)params->frames[t][0];',
     '        stts_checkpoint();\n        f0 = (double)params->frames[t][0];'),
    ('    if (rand() > RAND_MAX/2.0)', '    if (stts_noise_bit())'),
    ('        wav->samples[*pos] = (short)x;', '        wav->samples[*pos] = stts_checked_sample(x);')
], generated / 'cst_mlsa.c')
modify('src/utils/cst_tokenstream.c', [
    ('#ifndef UNDER_CE\n\tif (ts->fd != stdin)\n#endif\n\t    cst_fclose(ts->fd);',
     '        stts_fail(STTS_ENGINE); /* string-only candidate: no file descriptor */')
], generated / 'cst_tokenstream.c')
modify('src/synth/cst_synth.c', [
    ('    while (meth->hookname)\n    {',
     '    while (meth->hookname)\n    {\n        stts_checkpoint();')
], generated / 'cst_synth.c')
modify('src/cg/cst_mlpg.c', [
    ('   for (m = 0; m <= pst->order; m++) {\n       calc_R_and_r(pst, m);\n       Choleski(pst);',
     '   for (m = 0; m <= pst->order; m++) {\n       stts_checkpoint();\n       calc_R_and_r(pst, m);\n       Choleski(pst);')
], generated / 'cst_mlpg.c')
modify('lang/usenglish/us_text.c', [
    ('atoi(val_string(val_car(val_cdr(ss))))', 'stts_checked_atoi(val_string(val_car(val_cdr(ss))))'),
    ('atoi(val_string(val_car(ss)))', 'stts_checked_atoi(val_string(val_car(ss)))'),
    ('else if (atoi(aaa) < (atoi(bbb)))', 'else if (stts_checked_atoi(aaa) < (stts_checked_atoi(bbb)))'),
    ('if (atoi(aaa) > 1)', 'if (stts_checked_atoi(aaa) > 1)')
], generated / 'us_text.c')
modify('src/utils/cst_val.c', [
    ('return atoi(CST_VAL_STRING(v));', 'return stts_checked_atoi(CST_VAL_STRING(v));'),
    ('return (int)CST_VAL_FLOAT(v);', 'return stts_checked_float_int((double)CST_VAL_FLOAT(v));')
], generated / 'cst_val.c')
modify('src/utils/cst_string.c', [
    ('double cst_atof(const char *str)\n{\n    return atof(str);\n}',
     'double cst_atof(const char *str)\n{\n    return stts_checked_atof(str);\n}')
], generated / 'cst_string.c')
modify('src/cg/cst_cg.c', [
    ('            mcep_frame = relation_append(mcep,NULL);',
     '            if ((num_frames + 1) * (double)cg_db->frame_advance > 30.0)\n'
     '                stts_fail(STTS_FORMAT);\n'
     '            mcep_frame = relation_append(mcep,NULL);'),
    ('    param_track = new_track();',
     '    /* Reject excessive predicted duration before large parameter matrices. */\n'
     '    if (cg_db->sample_rate != 16000 || cg_db->frame_advance <= 0 ||\n'
     '        utt_feat_int(utt,"param_track_num_frames") <= 0 ||\n'
     '        utt_feat_int(utt,"param_track_num_frames") * (double)cg_db->frame_advance > 30.0)\n'
     '        stts_fail(STTS_FORMAT);\n    param_track = new_track();')
], generated / 'cst_cg.c')
modify('src/speech/cst_track.c', [
    ('    memmove(n_times,t->times,',
     '    if (num_frames > 0 && t->num_frames > 0)\n        memmove(n_times,t->times,')
], generated / 'cst_track.c')
write_changed(ROOT / 'modifications.patch', ''.join(patches).encode())

# Keep only the conservative authorized translation units, excluding platform
# I/O and runtime functions replaced by local guarded implementations. Linker GC
# plus a separate retained-import/export check is still required after linking.
excluded = {
    'src/utils/cst_alloc.c', 'src/utils/cst_error.c', 'src/utils/cst_file_stdio.c',
    'src/utils/cst_mmap_posix.c', 'src/utils/cst_url.c', 'src/utils/cst_socket.c',
    'src/cg/cst_cg_load_voice.c', 'src/cg/cst_cg_dump_voice.c',
}
replaced = {'src/cg/cst_mlsa.c': 'generated/cst_mlsa.c',
            'src/utils/cst_tokenstream.c': 'generated/cst_tokenstream.c',
            'src/synth/cst_synth.c': 'generated/cst_synth.c',
            'src/cg/cst_mlpg.c': 'generated/cst_mlpg.c',
            'lang/usenglish/us_text.c': 'generated/us_text.c',
            'src/utils/cst_val.c': 'generated/cst_val.c',
            'src/utils/cst_string.c': 'generated/cst_string.c',
            'src/cg/cst_cg.c': 'generated/cst_cg.c',
            'src/speech/cst_track.c': 'generated/cst_track.c'}
units = [replaced.get(f['path'], 'upstream/' + f['path']) for f in manifest['files']
         if f['role'] == 'translation_unit' and f['path'] not in excluded]
write_changed(ROOT / 'sources.cmake', ('set(FLITE_SOURCES\n' + ''.join('  "${CMAKE_CURRENT_SOURCE_DIR}/' + p + '"\n' for p in units) + ')\n').encode())
if CHECK:
    for path in sorted(generated.rglob('*')) if generated.exists() else ():
        if path.is_file() and path not in expected_paths:
            drift.append('Unexpected: ' + path.relative_to(ROOT).as_posix())
    if drift:
        raise SystemExit('Prepared source drift:\n' + '\n'.join(drift))
    print('Verified', len(units), 'explicit translation units and', len(patches), 'attributed source repairs.')
else:
    print('Prepared', len(units), 'explicit translation units and', len(patches), 'attributed source repairs.')
