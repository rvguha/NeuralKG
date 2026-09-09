"""The checked-in launch pair are distinct deployments of one engine."""
import os
import pathlib
import subprocess
import unittest

import yaml

import instance
from registry import index


ROOT = pathlib.Path(__file__).resolve().parents[1]


class TwoInstanceTests(unittest.TestCase):
    def test_configs_name_distinct_ards_and_atlas_plugins(self):
        ours = instance.config(str(ROOT / 'instance.yaml'))
        atlas = instance.config(str(ROOT / 'instances' / 'atlas.yaml'))
        self.assertEqual(ours['identity']['name'], 'Neural KG')
        self.assertEqual(atlas['identity']['name'], 'Atlas')
        self.assertNotEqual(ours['ard']['finder_url'], atlas['ard']['finder_url'])
        self.assertEqual(atlas['query_understanding']['selection_model'], 'openai/gpt-oss-120b')
        self.assertIn('plugins.atlas_accessors', atlas['extensions'])
        self.assertIn('plugins.atlas_sec', atlas['extensions'])
        self.assertIn('plugins.atlas_datacommons', atlas['extensions'])

    def test_public_atlas_catalog_is_a_separate_indexable_corpus(self):
        roots = (ROOT / 'instances/atlas/catalog/attested-computations',
                 ROOT / 'instances/atlas/catalog/bigquery')
        old, old_custom = index.DESCRIPTOR_ROOTS, index.CUSTOM_DESCRIPTOR_ROOTS
        index.DESCRIPTOR_ROOTS = tuple(map(str, roots))
        index.CUSTOM_DESCRIPTOR_ROOTS = True
        self.addCleanup(setattr, index, 'DESCRIPTOR_ROOTS', old)
        self.addCleanup(setattr, index, 'CUSTOM_DESCRIPTOR_ROOTS', old_custom)
        docs, texts = index._collect_docs('test-embedding-model')
        self.assertEqual(len(docs), 10)
        self.assertEqual(len(texts), 10)
        self.assertTrue(any(d['identifier'].endswith('dc_indicator_for_place.md') for d in docs))

    def test_every_public_atlas_computation_has_an_installed_accessor(self):
        config = ROOT / 'instances' / 'atlas.yaml'
        code = ('import json, extensions; r=extensions.registry(); '
                'print(json.dumps(sorted(r.accessors)))')
        env = {**os.environ, 'INSTANCE_CONFIG': str(config)}
        loaded = subprocess.check_output([os.sys.executable, '-c', code], cwd=ROOT, env=env,
                                         text=True).strip()
        installed = set(__import__('json').loads(loaded))
        for path in (ROOT / 'instances/atlas/catalog/attested-computations').glob('*.md'):
            fm = yaml.safe_load(path.read_text().split('---', 2)[1])
            executor = ((fm.get('computation') or {}).get('runtime') or {}).get('executor')
            self.assertIn(executor, installed, path.name)

    def test_launch_scripts_are_valid_shell(self):
        for name in ('run_two_instances.sh', 'stop_two_instances.sh'):
            subprocess.run(['bash', '-n', str(ROOT / 'scripts' / name)], check=True)


if __name__ == '__main__':
    unittest.main()
