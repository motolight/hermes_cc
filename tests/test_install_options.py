import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import install


class InstallOptionsTest(unittest.TestCase):
    def test_managed_gpu_ssh_is_saved_and_can_be_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = install.Manager(Path(directory))
            prior = {"entry": None, "public": {}, "runtime": {}, "disabled": False}
            options = argparse.Namespace(
                systemd=True, hermes_home=None, vllm_origin="http://192.168.50.10:8000",
                gpu_ssh_user="hermes", local_gpu=False,
            )
            with patch.object(install.sys, "platform", "linux"):
                installed = manager.prepare(prior, options)
                text = manager.unit(installed).decode()
                self.assertIn("PrivateUsers=true", text)
                self.assertIn("ProtectSystem=strict", text)
                self.assertIn("HCC_GPU_SSH_USER=hermes", text)
                self.assertIn("HCC_VLLM_ORIGIN=http://192.168.50.10:8000", text)
                options.vllm_origin = options.gpu_ssh_user = None
                self.assertIn("HCC_GPU_SSH_USER=hermes", manager.unit(manager.prepare(installed, options)).decode())
                options.local_gpu = True
                self.assertNotIn("HCC_GPU_SSH_USER", manager.unit(manager.prepare(installed, options)).decode())

    def test_gpu_ssh_requires_a_private_inference_host_and_plain_username(self):
        for user, origin in (
            ("-oProxyCommand", "http://192.168.50.10:8000"),
            ("hermes", "https://service.example:8000"),
            ("hermes", "http://127.0.0.1:8000"),
            ("hermes", None),
        ):
            with self.subTest(user=user, origin=origin), self.assertRaises(install.InstallError):
                install.validate_gpu_ssh(user, origin)
