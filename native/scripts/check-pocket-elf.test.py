import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('pocket_elf', Path(__file__).with_name('check-pocket-elf.py'))
elf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(elf)

JNI = {'Java_com_xmilo_1at_1your_1side_sign_1to_1speak_PocketTtsRequest_native' + name
       for name in ('Create', 'Synthesize', 'Cancel', 'Close')}


def symbol(index, name, visibility='DEFAULT'):
    return f'{index}: 0000000000001000  16 FUNC    GLOBAL {visibility}   12 {name}'


class PocketElfExportsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prefix = Path(self.tmp.name) / 'jni'
        Path(str(self.prefix) + '-header.txt').write_text('Type: DYN\nMachine: AArch64\n')
        Path(str(self.prefix) + '-segments.txt').write_text(
            '  LOAD 0x000000 0x000000 0x000000 0x100 0x100 R E 0x4000\n')
        Path(str(self.prefix) + '-dynamic.txt').write_text(
            '(SONAME) Library soname: [libprivate_pocket_tts.so]\n'
            '(NEEDED) Shared library: [libsign_pocket_tts_runtime.so]\n')

    def write_symbols(self, dynamic, static=()):
        text = "Symbol table '.dynsym' contains 6 entries:\n"
        text += '   Num: Value Size Type Bind Vis Ndx Name\n'
        text += '\n'.join(symbol(i + 1, name, visibility) for i, (name, visibility) in enumerate(dynamic))
        text += "\nSymbol table '.symtab' contains 6 entries:\n"
        text += '   Num: Value Size Type Bind Vis Ndx Name\n'
        text += '\n'.join(symbol(i + 1, name) for i, name in enumerate(static))
        Path(str(self.prefix) + '-symbols.txt').write_text(text)

    def test_only_default_visible_dynamic_jni_exports_count(self):
        hidden = 'Java_com_xmilo_1at_1your_1side_sign_1to_1speak_PocketTtsRequest_nativeHelper'
        self.write_symbols([(name, 'DEFAULT') for name in sorted(JNI)] + [(hidden, 'HIDDEN')], [hidden])
        elf.check('arm64-v8a', self.prefix, 'jni')

    def test_missing_dynamic_jni_export_fails_even_if_present_in_symtab(self):
        missing = sorted(JNI)[-1]
        self.write_symbols([(name, 'DEFAULT') for name in sorted(JNI - {missing})], [missing])
        with self.assertRaisesRegex(ValueError, 'JNI exports differ: missing='):
            elf.check('arm64-v8a', self.prefix, 'jni')

    def test_extra_public_dynamic_jni_export_fails(self):
        self.write_symbols([(name, 'DEFAULT') for name in sorted(JNI)] + [('extra_api', 'DEFAULT')])
        with self.assertRaisesRegex(ValueError, 'extra='):
            elf.check('arm64-v8a', self.prefix, 'jni')


if __name__ == '__main__':
    unittest.main()
