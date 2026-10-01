import base64
import copy
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from homily_monitor.editorial import (build_analysis_prompt, catalog, default_profile,
                                     finalize_image_prompt, render, validate_profile)
from homily_monitor.editorial_generator import EditorialGenerator
from homily_monitor.editorial_settings import StudioClient, store_source_context
from homily_monitor.studio_worker import deliver_pending, process_one

ROOT = Path(__file__).resolve().parents[1]


def fake_api(text='{"title":"Hope","description":"A reflection.","liturgical_day":"Sunday","lit_year":"A","special":""}'):
    client = Mock()
    client.chat.completions.create.return_value = types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))], usage=None,
    )
    client.images.generate.return_value = types.SimpleNamespace(
        data=[types.SimpleNamespace(b64_json=base64.b64encode(b'preview-image').decode())], usage=None,
    )
    return client


def isolated_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProfileTests(unittest.TestCase):
    def test_catalogues_match_and_all_styles_preserve_their_treatment(self):
        plugin = json.loads((ROOT / 'wordpress-plugin/homily-studio/editorial-catalog.json').read_text(encoding='utf-8'))
        self.assertEqual(catalog(), plugin)
        for key, style in catalog()['styles'].items():
            profile = default_profile()
            profile['style'] = key
            prompt = finalize_image_prompt(profile, 'One scene', 'Hope', 'A reflection.', 'A concrete preached image.')
            self.assertIn(style['prompt'], prompt)
            self.assertIn('No visible text', prompt)
            if key != 'sacred-editorial':
                self.assertNotIn('painterly realism', prompt)
                self.assertNotIn('cinematic lighting', prompt)

    def test_creative_templates_replace_fixed_title_and_description_rules(self):
        profile = default_profile()
        profile['voice'] = 'Conversational Canadian English'
        profile['prompts']['title'] = 'Write a lowercase title with precisely four words.'
        profile['prompts']['description'] = 'Write two sentences for {{audience}}.'
        prompt = build_analysis_prompt(profile, 'Mass-2026-10-01_09-00.mp3', 'The source.')
        self.assertIn('lowercase', prompt)
        self.assertIn('two sentences', prompt)
        self.assertIn('Conversational Canadian English', prompt)
        self.assertNotIn('Title Case', prompt)
        self.assertNotIn('3–5', prompt)
        self.assertIn('Required factual and output contract', prompt)

    def test_legacy_additions_work_without_mutating_the_defaults(self):
        profile = default_profile({'gpt_title_addon': 'A local addition'})
        self.assertIn('A local addition', profile['prompts']['title'])
        self.assertNotIn('A local addition', default_profile()['prompts']['title'])

    def test_profile_rejects_unknown_configuration_and_bad_types(self):
        for field, value in [('openai_api_key', 'example'), ('schema_version', True),
                             ('images_enabled', 'false'), ('style', 'unknown'), ('image_quality', 'extreme')]:
            with self.subTest(field=field):
                profile = default_profile(); profile[field] = value
                with self.assertRaises(ValueError): validate_profile(profile)

    def test_template_parser_is_literal_and_single_pass(self):
        self.assertEqual(render('{{title}} / {{voice}}', {'title': '{{voice}}', 'voice': 'Warm'}), '{{voice}} / Warm')
        for template in ('{{password}}', '{{title', '{{title|execute}}', '}}'):
            profile = default_profile(); profile['prompts']['title'] = template
            with self.assertRaises(ValueError): validate_profile(profile)


class GeneratorTests(unittest.TestCase):
    def test_text_previews_only_call_text_api(self):
        client = fake_api()
        result = EditorialGenerator(client, {}).preview({'profile': default_profile(), 'kind': 'text',
            'input': {'filename': 'Mass-2026-10-01_09-00.mp3', 'transcript': 'A reflection.'}})
        self.assertEqual(result['metadata']['title'], 'Hope')
        client.images.generate.assert_not_called()
        self.assertNotIn('image_base64', result)

    def test_bad_metadata_is_rejected_before_storage(self):
        for text in ('{}', '[]', '{"title":42}', 'not JSON'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    EditorialGenerator(fake_api(text), {}).metadata(default_profile(), 'Mass-2026-10-01_09-00.mp3', 'Source')

    def test_concept_failure_keeps_style_and_source(self):
        client = fake_api(); client.chat.completions.create.side_effect = RuntimeError('Unavailable')
        profile = default_profile(); profile['style'] = 'woodcut'
        prompt, _ = EditorialGenerator(client, {}).image_prompt(profile, 'Hope', 'A reflection.', 'The seed grows.')
        self.assertIn('carved strokes', prompt)
        self.assertIn('The seed grows.', prompt)
        client.images.generate.assert_not_called()

    def test_reviewed_prompt_is_rendered_without_a_new_concept(self):
        client = fake_api()
        result = EditorialGenerator(client, {'image_model': 'configured-model'}).preview({
            'profile': default_profile(), 'kind': 'image', 'input': {'reviewed_image_prompt': 'The reviewed visual prompt'}})
        client.chat.completions.create.assert_not_called()
        self.assertEqual(client.images.generate.call_args.kwargs['prompt'], 'The reviewed visual prompt')
        self.assertEqual(client.images.generate.call_args.kwargs['model'], 'configured-model')
        self.assertEqual(result['image_prompt'], 'The reviewed visual prompt')

    def test_disabled_images_make_no_api_calls(self):
        client = fake_api(); profile = default_profile(); profile['images_enabled'] = False
        with self.assertRaises(ValueError):
            EditorialGenerator(client, {}).preview({'profile': profile, 'kind': 'full', 'input': {}})
        client.images.generate.assert_not_called(); client.chat.completions.create.assert_not_called()


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.config = {'wordpress': {'url': 'https://example.org/church'},
                       'homily_studio': {'enabled': True, 'user': 'test-reader', 'app_password': 'test-password'}}
        self.session = Mock()
        self.response = self.session.request.return_value
        self.response.status_code = 200
        self.response.json.return_value = default_profile()

    def client(self, config=None):
        with patch.dict('os.environ', {}, clear=True):
            return StudioClient(config or self.config, self.directory.name, self.session)

    def test_settings_read_uses_dedicated_auth_and_refuses_redirects(self):
        client = self.client(); client.active_profile(default_profile())
        args = self.session.request.call_args
        self.assertEqual(args.args, ('GET', 'https://example.org/church/wp-json/homily-studio/v1/settings'))
        self.assertEqual(args.kwargs['auth'], ('test-reader', 'test-password'))
        self.assertFalse(args.kwargs['allow_redirects'])
        self.response.status_code = 302
        with self.assertRaises(ValueError): client.request('GET', '/settings')

    def test_cache_is_validated_and_bound_to_site(self):
        first = self.client(); active = default_profile(); active['voice'] = 'An approved voice'
        self.response.json.return_value = active
        first.active_profile(default_profile())
        self.session.request.side_effect = requests.ConnectionError('Offline')
        self.assertEqual(first.active_profile(default_profile())['voice'], 'An approved voice')
        config = copy.deepcopy(self.config); config['wordpress']['url'] = 'https://another.example.org'
        self.assertNotEqual(self.client(config).active_profile(default_profile())['voice'], 'An approved voice')

    def test_invalid_remote_or_cached_settings_never_become_active(self):
        client = self.client()
        client.state_dir.mkdir(parents=True)
        (client.state_dir / 'active.json').write_text('{"openai_api_key":"example"}', encoding='utf-8')
        self.response.json.return_value = {'schema_version': 99}
        self.assertEqual(client.active_profile(default_profile()), default_profile())

    def test_unsafe_urls_are_rejected(self):
        for url in ('http://example.org', 'https://user:pass@example.org', 'https://example.org?token=example'):
            config = copy.deepcopy(self.config); config['wordpress']['url'] = url
            with self.assertRaises(ValueError): self.client(config)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.client = Mock(); self.client.state_dir = Path(self.directory.name)
        self.job = {'id': 7, 'claim': 'test-claim', 'kind': 'text', 'profile': default_profile(), 'input': {}}
        self.client.request.side_effect = [self.job, requests.ConnectionError('Delivery interrupted')]
        self.generator = Mock(); self.generator.preview.return_value = {'metadata': {'title': 'Hope'}}

    def test_delivery_failure_preserves_result_and_retry_makes_no_ai_call(self):
        with self.assertRaises(requests.ConnectionError): process_one(self.client, lambda: self.generator)
        self.assertTrue((self.client.state_dir / '7.json').exists())
        self.client.request.side_effect = None
        deliver_pending(self.client)
        self.generator.preview.assert_called_once()
        self.assertFalse((self.client.state_dir / '7.json').exists())
        self.assertEqual(self.client.request.call_args.args, ('POST', '/jobs/7/result'))

    def test_generation_failure_returns_error_without_automatic_retry(self):
        self.generator.preview.side_effect = RuntimeError('API failure')
        self.client.request.side_effect = [self.job, {}]
        process_one(self.client, lambda: self.generator)
        self.generator.preview.assert_called_once()
        self.assertIn('error', self.client.request.call_args.kwargs['json'])

    def test_local_transcript_is_resolved_without_uploading_it_at_publish_time(self):
        self.job['input'] = {'filename': 'Mass-2026-10-01_09-00.mp3', 'use_local_transcript': True}
        source = self.client.state_dir / 'sources'; source.mkdir()
        (source / 'Mass-2026-10-01_09-00.mp3.json').write_text('{"transcript":"The saved preached message."}', encoding='utf-8')
        self.client.request.side_effect = [self.job, {}]
        process_one(self.client, lambda: self.generator)
        self.assertEqual(self.generator.preview.call_args.args[0]['input']['transcript'], 'The saved preached message.')

    def test_local_source_path_traversal_is_rejected_before_generation(self):
        self.job['input'] = {'filename': '../config.json', 'use_local_transcript': True}
        self.client.request.side_effect = [self.job, {}]
        process_one(self.client, lambda: self.generator)
        self.generator.preview.assert_not_called()
        self.assertIn('error', self.client.request.call_args.kwargs['json'])


class PersistenceTests(unittest.TestCase):
    def test_uploader_uses_snapshot_and_configured_publishing_mapping(self):
        for rest_base, fields in [('podcast', {'audio': 'episode_audio', 'cover_url': 'art_url', 'cover_id': 'art_id'}), ('posts', {})]:
            with self.subTest(rest_base=rest_base), tempfile.TemporaryDirectory() as directory:
                cfg = types.ModuleType('homily_monitor.config_loader')
                cfg.CFG = {'wordpress': {'url': 'https://example.org', 'user': 'test-publisher', 'app_password': 'test-password',
                            'post_rest_base': rest_base, 'meta_fields': fields}, 'paths': {'local_dir': directory},
                            'church': {'timezone': 'America/Edmonton'}, 'homily_studio': {'enabled': True}}
                cfg.get_base_dir = lambda: directory
                profile = default_profile(); profile['revision'] = 'pinned-revision'
                db = types.ModuleType('homily_monitor.database')
                db.get_latest_homily_analysis = Mock(return_value=('Hope', 'Description', '', 'Sunday', 'A', '2026-10-01', '', None, ''))
                db.get_most_recent_homily_analysis = Mock()
                db.get_homily_editorial_profile = Mock(return_value=profile)
                email = types.ModuleType('homily_monitor.email_utils')
                email.create_inline_image = Mock(return_value={'content_id': 'preview'})
                email.send_email_alert = Mock(); email.send_success_email = Mock()
                speaker = types.ModuleType('homily_monitor.speaker_utils'); speaker.get_homilist_fallback_label = lambda: 'Homilist'
                gpt = types.ModuleType('homily_monitor.gpt_utils'); gpt.analyze_transcript_with_gpt = Mock()
                helpers = types.ModuleType('homily_monitor.helpers'); helpers.validate_and_get_transcript = Mock()
                with patch.dict(sys.modules, {'homily_monitor.config_loader': cfg, 'homily_monitor.database': db,
                        'homily_monitor.email_utils': email, 'homily_monitor.speaker_utils': speaker,
                        'homily_monitor.gpt_utils': gpt, 'homily_monitor.helpers': helpers}):
                    wp = isolated_module('homily_monitor.test_wordpress', 'homily_monitor/wordpress_utils.py')
                    wp.WP_SESSION = Mock()
                    responses = []
                    for data in ({'id': 23, 'source_url': 'https://example.org/cover.png'},
                                 {'id': 24, 'source_url': 'https://example.org/audio.mp3'},
                                 {'id': 25, 'link': 'https://example.org/example-post'}):
                        response = Mock(); response.status_code = 201; response.json.return_value = data; responses.append(response)
                    wp.WP_SESSION.post.side_effect = responses
                    wp._generate_podcast_image_if_available = Mock(return_value=BytesIO(b'fixture-png'))
                    wp._recover_homily_transcript_excerpt = Mock(return_value='The preached message.')
                    audio = Path(directory) / 'Homily-2026-10-01_09-00.mp3'; audio.write_bytes(b'fixture-audio')
                    self.assertTrue(wp.upload_to_wordpress(str(audio), 'Mass-2026-10-01_09-00.mp3'))
                    self.assertEqual(wp._generate_podcast_image_if_available.call_args.kwargs['profile'], profile)
                    post_call = wp.WP_SESSION.post.call_args
                    self.assertEqual(post_call.args[0], f'https://example.org/wp-json/wp/v2/{rest_base}')
                    payload = post_call.kwargs['json']
                    self.assertEqual(payload['status'], 'draft')
                    self.assertEqual(payload['homily_source_filename'], 'Mass-2026-10-01_09-00.mp3')
                    if fields:
                        self.assertEqual(payload['meta']['episode_audio'], 'https://example.org/audio.mp3')
                        self.assertEqual(payload['meta']['art_id'], '23')
                    else:
                        self.assertEqual(payload['meta'], {})
                        self.assertIn('[audio src=', payload['content'])
                    self.assertEqual(email.send_success_email.call_args.args[0], 'Homily draft ready')
                    email.send_email_alert.assert_not_called()

    def test_database_migration_preserves_existing_rows_and_snapshots_new_profiles(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory) / 'legacy.db')
            connection = sqlite3.connect(filename)
            connection.execute('CREATE TABLE homilies (id INTEGER PRIMARY KEY, group_key TEXT, filename TEXT, date TEXT, title TEXT, description TEXT, special TEXT, processed_at TEXT)')
            connection.execute("INSERT INTO homilies VALUES (1,'day','old.mp3','2026-10-01','Old','Existing description','','2026-10-01')")
            connection.commit(); connection.close()
            cfg = types.ModuleType('homily_monitor.config_loader'); cfg.CFG = {'paths': {'db_path': filename}}
            with patch.dict(sys.modules, {'homily_monitor.config_loader': cfg}):
                db = isolated_module('homily_monitor.test_database', 'homily_monitor/database.py')
                self.assertEqual(db.get_latest_homily_analysis('old.mp3')[0], 'Old')
                self.assertIsNone(db.get_homily_editorial_profile('old.mp3'))
                profile = default_profile(); profile['revision'] = 'approved-revision'
                db.insert_homily('day', 'new.mp3', '2026-10-01', 'New', 'Description', '', editorial_profile=profile)
                self.assertEqual(db.get_homily_editorial_profile('new.mp3')['revision'], 'approved-revision')
                db.CONN.close()

    def test_production_metadata_uses_shared_generator_and_stores_profile(self):
        profile = default_profile(); profile['revision'] = 'production-profile'
        cfg = types.ModuleType('homily_monitor.config_loader'); cfg.CFG = {'openai_api_key': 'test-only'}
        email = types.ModuleType('homily_monitor.email_utils'); email.send_email_alert = Mock()
        db = types.ModuleType('homily_monitor.database'); db.insert_homily = Mock()
        speaker = types.ModuleType('homily_monitor.speaker_utils'); speaker.resolve_homilist = Mock()
        api = types.ModuleType('openai'); api.OpenAI = Mock(return_value=fake_api()); api.OpenAIError = type('OpenAIError', (Exception,), {})
        with patch.dict(sys.modules, {'homily_monitor.config_loader': cfg, 'homily_monitor.email_utils': email,
                                     'homily_monitor.database': db, 'homily_monitor.speaker_utils': speaker, 'openai': api}), \
             patch('homily_monitor.editorial_settings.get_editorial_profile', return_value=profile):
            gpt = isolated_module('homily_monitor.test_gpt', 'homily_monitor/gpt_utils.py')
            gpt.analyze_transcript_with_gpt('Mass-2026-10-01_09-00.mp3', 'A homily about hope.', None)
            db.insert_homily.assert_called_once()
            self.assertEqual(db.insert_homily.call_args.kwargs['editorial_profile'], profile)
            email.send_email_alert.assert_not_called()


if __name__ == '__main__':
    unittest.main()
