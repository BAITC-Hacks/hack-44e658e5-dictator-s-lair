import unittest
import os
from unittest.mock import patch
import numpy as np

from speaker_embeddings import cluster, extractor, speaker_threads, DEFAULT_SPEAKER_THREADS


class SpeakerThreadTests(unittest.TestCase):
    def test_extractor_uses_configured_threads_and_is_cached(self):
        extractor.cache_clear()
        self.addCleanup(extractor.cache_clear)
        with patch.dict(os.environ, {'MEETING_SPEAKER_THREADS': '8'}), \
                patch('speaker_embeddings.model_path', return_value='local-model.onnx'), \
                patch('speaker_embeddings.sherpa_onnx.SpeakerEmbeddingExtractorConfig') as config, \
                patch('speaker_embeddings.sherpa_onnx.SpeakerEmbeddingExtractor') as engine:
            config.return_value.validate.return_value = True
            self.assertIs(extractor(), extractor())
            config.assert_called_once_with(model='local-model.onnx', num_threads=8, provider='cpu')
            engine.assert_called_once_with(config.return_value)

    def test_default_respects_cpu_limit(self):
        with patch.dict(os.environ, {}, clear=True):
            for available, expected in [(20, DEFAULT_SPEAKER_THREADS), (1, 1), (None, 1)]:
                with self.subTest(available=available), patch('speaker_embeddings.os.cpu_count', return_value=available):
                    self.assertEqual(speaker_threads(), expected)

    def test_explicit_thread_override(self):
        with patch.dict(os.environ, {'MEETING_SPEAKER_THREADS': '4'}):
            self.assertEqual(speaker_threads(), 4)

    def test_invalid_threads_fail_clearly(self):
        for value in ('0', '-2', '1.5', '', 'auto'):
            with self.subTest(value=value), patch.dict(os.environ, {'MEETING_SPEAKER_THREADS': value}):
                with self.assertRaisesRegex(ValueError, 'positive integer'):
                    speaker_threads()


class SpeakerClusteringTests(unittest.TestCase):
    def test_more_than_two_speakers_and_returning_voice(self):
        self.assertEqual(cluster(np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1],
                                          [1, .01, 0]])).tolist(), [0, 1, 2, 0])

    def test_single_voice_is_not_forced_into_two_clusters(self):
        self.assertEqual(cluster([[1, 0], [1, .01], [1, -.01]]).tolist(), [0, 0, 0])

    def test_empty_and_singleton(self):
        self.assertEqual(cluster([]).tolist(), [])
        self.assertEqual(cluster([[1, 0]]).tolist(), [0])
