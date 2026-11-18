import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

spec = importlib.util.spec_from_file_location('holistic', Path(__file__).with_name('prepare-holistic-assets.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class AdmissionTest(unittest.TestCase):
    def test_real_admission_and_source_built_core_are_intact(self):
        _, data = m.receipt()
        self.assertEqual(len(data['model']['members']), 7)
        self.assertEqual(len(m.notice_sources(data)), 6)
        m.verify_offline_core()

    def test_changed_pom_is_rejected_even_with_same_aar(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'core'
            Path(str(base) + '.aar').write_bytes(Path(str(m.CORE_BASE) + '.aar').read_bytes())
            Path(str(base) + '.pom').write_text(Path(str(m.CORE_BASE) + '.pom').read_text().replace('protobuf-javalite', 'datatransport'))
            with mock.patch.object(m, 'CORE_BASE', base), self.assertRaisesRegex(ValueError, 'dependency inventory'):
                m.verify_offline_core()

    def test_changed_receipt_identity_is_rejected(self):
        for key, value in [('url', 'https://example.com/model'), ('asset', '../escape'),
                           ('sha256', '0' * 64), ('size', 1), ('license', 'unknown')]:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temp:
                data = json.loads(m.RECEIPT.read_bytes())
                data['model'][key] = value
                path = Path(temp) / 'receipt.json'
                path.write_text(json.dumps(data))
                with mock.patch.object(m, 'RECEIPT', path), self.assertRaises(ValueError):
                    m.receipt()

    def test_missing_extra_or_changed_notice_is_rejected(self):
        _, original = m.receipt()
        for mutate in [lambda d: d['notices'].pop(),
                       lambda d: d['notices'].append(d['notices'][0]),
                       lambda d: d['notices'][0].update(sha256='0' * 64),
                       lambda d: d['notices'][0].update(source='../../package.json')]:
            data = copy.deepcopy(original)
            mutate(data)
            with self.assertRaises(ValueError):
                m.notice_sources(data)

    def test_redirect_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'must not redirect'):
            m.NoRedirect().redirect_request(None, None, 302, '', {}, 'http://bad.invalid/model')


class PreparationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = self.root / 'fixture.task'
        with zipfile.ZipFile(self.model, 'w') as archive:
            archive.writestr('fixture.tflite', b'public synthetic model bytes')
        model_bytes = self.model.read_bytes()
        raw, self.data = m.receipt()
        self.data = copy.deepcopy(self.data)
        self.data['model'].update(size=len(model_bytes), sha256=hashlib.sha256(model_bytes).hexdigest(), members=[
            {'name': 'fixture.tflite', 'size': 28, 'sha256': hashlib.sha256(b'public synthetic model bytes').hexdigest()}])
        self.data['model']['members'][0]['size'] = len(b'public synthetic model bytes')
        self.receipt = self.root / 'receipt.json'
        self.receipt.write_text(json.dumps(self.data))
        for key, value in [('MODEL_SIZE', len(model_bytes)), ('MODEL_SHA256', hashlib.sha256(model_bytes).hexdigest()),
                           ('RECEIPT', self.receipt)]:
            patcher = mock.patch.object(m, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.output = self.root / 'output'

    def test_offline_stage_and_cache_reuse_are_exact(self):
        with mock.patch.object(m, 'download') as download:
            m.prepare(self.output, self.model, False)
            m.prepare(self.output, allow_download=False)
            download.assert_not_called()
        self.assertEqual((self.output / m.MODEL_ASSET).read_bytes(), self.model.read_bytes())
        self.assertEqual((self.output / 'holistic-licenses/MODEL-RECEIPT.json').read_bytes(), self.receipt.read_bytes())
        self.assertEqual(len([p for p in self.output.rglob('*') if p.is_file()]), 8)

    def published_files(self):
        return {path.relative_to(self.output): m.file_digest(path)
                for path in self.output.rglob('*') if path.is_file()}

    def test_arbitrary_existing_directory_keeps_unmanaged_file(self):
        arbitrary = self.root / 'unrelated-directory'
        arbitrary.mkdir()
        sentinel = arbitrary / 'personal-data.txt'
        sentinel.write_bytes(b'unmanaged fixture')
        with self.assertRaises(ValueError):
            try:
                m.prepare(arbitrary, self.model, allow_download=False)
            finally:
                self.assertTrue(sentinel.is_file(), 'preparation deleted the unmanaged file')
        self.assertEqual(sentinel.read_bytes(), b'unmanaged fixture')
        self.assertEqual(list(arbitrary.iterdir()), [sentinel])

    def test_unmanaged_file_inside_staged_tree_blocks_replacement(self):
        m.prepare(self.output, self.model, allow_download=False)
        sentinel = self.output / m.NOTICE_PREFIX / 'unmanaged.txt'
        sentinel.write_bytes(b'unmanaged fixture')
        before = self.published_files()
        with self.assertRaises(ValueError):
            try:
                m.prepare(self.output, allow_download=False)
            finally:
                self.assertTrue(sentinel.is_file(), 'preparation deleted the unmanaged file')
        self.assertEqual(self.published_files(), before)

    def test_explicit_model_beneath_output_is_rejected_and_kept(self):
        model = self.output / 'inputs' / self.model.name
        model.parent.mkdir(parents=True)
        original = self.model.read_bytes()
        model.write_bytes(original)
        with self.assertRaises(ValueError):
            try:
                m.prepare(self.output, model, allow_download=False)
            finally:
                self.assertTrue(model.is_file(), 'preparation deleted the explicit model input')
        self.assertEqual(model.read_bytes(), original)
        self.assertFalse((self.output / m.MODEL_ASSET).exists())

    @unittest.skipUnless(os.name == 'nt', 'requires Windows directory junctions')
    def test_output_junction_and_junction_ancestor_are_rejected(self):
        for location in ('output', 'ancestor'):
            with self.subTest(location=location):
                target = self.root / f'{location}-target'
                target.mkdir()
                link = self.root / f'{location}-junction'
                subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(target)],
                               check=True, capture_output=True)
                self.addCleanup(link.rmdir)
                self.assertFalse(link.is_symlink(), 'fixture must be a junction')
                self.assertEqual(link.resolve(), target.resolve())
                output = link if location == 'output' else link / 'nested' / 'output'
                with self.assertRaises(ValueError):
                    try:
                        m.prepare(output, self.model, allow_download=False)
                    finally:
                        self.assertTrue(link.is_dir(), 'preparation removed the junction')
                        self.assertFalse(any(target.iterdir()), 'preparation wrote through the junction')

    def test_failed_publication_preserves_verified_output_and_allows_retry(self):
        m.prepare(self.output, self.model, allow_download=False)
        before = self.published_files()
        # A new receipt encoding is still admitted and must reach publication.
        receipt = json.dumps(self.data, indent=2).encode()
        self.receipt.write_bytes(receipt)
        receipt_asset = Path(m.NOTICE_PREFIX) / 'MODEL-RECEIPT.json'

        def fail_final_destination(operation):
            def perform(source, destination, *args, **kwargs):
                staged_receipt = Path(source) / receipt_asset
                if (Path(destination) == self.output and staged_receipt.is_file()
                        and staged_receipt.read_bytes() == receipt):
                    raise OSError('injected Holistic publication failure')
                return operation(source, destination, *args, **kwargs)
            return perform

        with mock.patch.object(m.shutil, 'copytree', side_effect=fail_final_destination(m.shutil.copytree)), \
             mock.patch.object(os, 'rename', side_effect=fail_final_destination(os.rename)), \
             mock.patch.object(os, 'replace', side_effect=fail_final_destination(os.replace)):
            with self.assertRaisesRegex(OSError, 'injected Holistic publication failure'):
                m.prepare(self.output, allow_download=False)

        self.assertEqual(self.published_files(), before, 'publication failure changed the previous output')
        m.prepare(self.output, allow_download=False)
        before[receipt_asset] = hashlib.sha256(receipt).hexdigest()
        self.assertEqual(self.published_files(), before)

    def test_missing_model_offline_and_bad_source_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'model missing'):
            m.prepare(self.output, allow_download=False)
        for content in (b'', b'bad bytes', self.model.read_bytes()[:-1] + b'?'):
            bad = self.root / 'bad.task'
            bad.write_bytes(content)
            with self.assertRaisesRegex(ValueError, 'size or SHA-256'):
                m.prepare(self.output, bad)
            self.assertFalse(self.output.exists())

    def test_corrupt_cached_model_does_not_silently_download(self):
        m.prepare(self.output, self.model)
        (self.output / m.MODEL_ASSET).write_bytes(b'corrupt')
        with mock.patch.object(m, 'download') as download, self.assertRaises(ValueError):
            m.prepare(self.output)
        download.assert_not_called()

    def test_linked_model_output_and_parent_are_rejected(self):
        model_link = self.root / 'linked.task'
        try:
            model_link.symlink_to(self.model)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314:
                self.skipTest('Windows symbolic-link privilege unavailable')
            raise
        with self.assertRaisesRegex(ValueError, 'Linked'):
            m.prepare(self.output, model_link)
        parent_link = self.root / 'linked-dir'
        parent_link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Linked'):
            m.prepare(parent_link / 'out', self.model)

    def test_bad_download_keeps_previous_outputs_unmodified(self):
        self.output.mkdir()
        sentinel = self.output / 'sentinel'
        sentinel.write_text('keep')
        def bad_download(path):
            path.write_bytes(b'corrupt')
        with mock.patch.object(m, 'download', side_effect=bad_download), self.assertRaises(ValueError):
            m.prepare(self.output)
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_download_pins_response_destination_length_and_bound(self):
        payload = self.model.read_bytes()
        def response(content=payload, url=m.MODEL_URL, size=None, status=200):
            stream = io.BytesIO(content)
            stream.geturl = lambda: url
            stream.status = status
            stream.headers = {} if size is None else {'Content-Length': size}
            return stream
        for content, url, size, status, fail in [
                (payload, m.MODEL_URL, str(len(payload)), 200, False),
                (payload, 'https://example.com', None, 200, True),
                (payload, m.MODEL_URL, '1', 200, True),
                (payload + b'x', m.MODEL_URL, None, 200, True),
                (payload, m.MODEL_URL, None, 201, True)]:
            target = self.root / 'download.task'
            target.unlink(missing_ok=True)
            opener = mock.Mock()
            opener.open.return_value = response(content, url, size, status)
            with mock.patch.object(m.urllib.request, 'build_opener', return_value=opener):
                if fail:
                    with self.assertRaises(ValueError): m.download(target)
                else:
                    m.download(target)
                    self.assertEqual(target.read_bytes(), payload)
                opener.open.assert_called_once_with(m.MODEL_URL, timeout=60)


if __name__ == '__main__': unittest.main()
