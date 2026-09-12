"""源图版本化后仍按图序选德语图，并拒绝旧图的程序翻译。"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.store import Archive
from publish.compose import ComposeError, _choose_images


class SourceImageTests(unittest.TestCase):
    def test_numbered_translation_works_with_versioned_original_but_stale_program_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            arc = Archive(Path(directory), 'fa_brand')
            post = arc.base / 'posts' / '2026-09' / '2026-09-12_1200_campaign_123'
            (post / 'media_de').mkdir(parents=True)
            source = post / '01_abcdef12.jpg'
            output = post / 'media_de' / '01.jpg'
            Image.new('RGB', (100, 100), 'blue').save(source)
            Image.new('RGB', (100, 100), 'red').save(output)
            row = {'post_id': '123', 'media_complete': True,
                   'media': [{'kind': 'image', 'local_path': source.relative_to(arc.base).as_posix()}]}
            paths, kinds, _ = _choose_images(arc, row, post, [])
            self.assertEqual(paths, (output,))
            self.assertEqual(kinds, ('media_de',))
            record = {'post_id': '123', 'media_index': 0,
                      'out_path': output.relative_to(arc.base).as_posix(),
                      'output_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                      'source_sha256': '0' * 64}
            (arc.base / 'images_de.jsonl').write_text(json.dumps(record) + '\n', encoding='utf-8')
            with self.assertRaisesRegex(ComposeError, '原图.*变化|源图.*匹配'):
                _choose_images(arc, row, post, [])
            record['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
            (arc.base / 'images_de.jsonl').write_text(json.dumps(record) + '\n', encoding='utf-8')
            self.assertEqual(_choose_images(arc, row, post, [])[0], (output,))


if __name__ == '__main__':
    unittest.main()
