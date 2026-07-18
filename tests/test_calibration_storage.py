from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import inspector  # noqa: F401
from app.schemas.calibration import CalibrationSaveRequest, RoiModel
from app.services.calibration_storage_service import CalibrationStorageService


PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(b"fake-png").decode("ascii")


def request(target_name: str = "1 号增氧机") -> CalibrationSaveRequest:
    return CalibrationSaveRequest(
        deviceId="DEVICE_001",
        channelId="0",
        targetId="AERATOR_001",
        targetName=target_name,
        presetIndex=7,
        presetName="增氧机 1",
        roi=RoiModel(x=10, y=20, width=30, height=40),
        focusAnchorRoi=RoiModel(x=50, y=60, width=70, height=80),
        snapshotOriginalBase64=PNG_DATA_URL,
        snapshotAnnotatedBase64=PNG_DATA_URL,
    )


class CalibrationStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path.cwd())
        root = Path(self.temp_dir.name)
        self.fake_settings = SimpleNamespace(
            data_root=root,
            calibration_dir=root / "calibrations",
            calibration_history_dir=root / "calibration_history",
        )
        self.fake_settings.calibration_dir.mkdir()
        self.fake_settings.calibration_history_dir.mkdir()
        self.settings_patch = patch("app.services.calibration_storage_service.settings", self.fake_settings)
        self.settings_patch.start()
        self.service = CalibrationStorageService()

    def tearDown(self) -> None:
        self.settings_patch.stop()
        self.temp_dir.cleanup()

    def test_save_update_and_restore_create_immutable_versions(self) -> None:
        first = self.service.save(request())
        second = self.service.save(request("更新后的 1 号增氧机"))

        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 2)
        self.assertFalse(first.legacy)
        history = self.service.history("DEVICE_001", 7)
        self.assertEqual([item.version for item in history.items], [1, 2])
        self.assertTrue((self.fake_settings.calibration_history_dir / "DEVICE_001_7" / "v0001" / "snapshot-original.png").exists())
        self.assertTrue((self.fake_settings.calibration_history_dir / "DEVICE_001_7" / "v0002" / "snapshot-annotated.png").exists())

        restored = self.service.restore("DEVICE_001", 7, 1)
        self.assertEqual(restored.version, 3)
        self.assertEqual(restored.restoredFromVersion, 1)
        self.assertEqual(self.service.get("DEVICE_001", 7).version, 3)
        self.assertEqual([item.version for item in self.service.history("DEVICE_001", 7).items], [1, 2, 3])

    def test_first_update_archives_legacy_current_record_as_v0001(self) -> None:
        legacy_path = self.fake_settings.calibration_dir / "DEVICE_001_7.json"
        legacy_path.write_text(
            json.dumps(
                {
                    "deviceId": "DEVICE_001",
                    "channelId": "0",
                    "targetId": "AERATOR_001",
                    "targetName": "旧标定",
                    "presetIndex": 7,
                    "presetName": "增氧机 1",
                    "roi": {"x": 1, "y": 2, "width": 3, "height": 4},
                    "notes": "",
                    "updatedAt": "2026-07-17T00:00:00+00:00",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        saved = self.service.save(request())
        history = self.service.history("DEVICE_001", 7)

        self.assertEqual(saved.version, 2)
        self.assertEqual([(item.version, item.legacy) for item in history.items], [(1, True), (2, False)])

    def test_exports_keep_deployment_configs_separate_from_archive(self) -> None:
        self.service.save(request())
        current = zipfile.ZipFile(io.BytesIO(self.service.build_all_current_archive()))
        archive = zipfile.ZipFile(io.BytesIO(self.service.build_history_archive()))

        self.assertEqual(current.namelist(), ["calibrations/DEVICE_001_7.json"])
        self.assertIn("calibration_history/DEVICE_001_7/v0001/calibration.json", archive.namelist())
        self.assertIn("calibration_history/DEVICE_001_7/v0001/snapshot-original.png", archive.namelist())
        self.assertIn("manifest.json", archive.namelist())

    def test_deployment_exports_contain_only_recognition_runtime_fields(self) -> None:
        self.service.save(request())

        current_payload = json.loads(self.service.current_deployment_bytes("DEVICE_001", 7))
        all_current = zipfile.ZipFile(io.BytesIO(self.service.build_all_current_archive()))
        bundled_payload = json.loads(all_current.read("calibrations/DEVICE_001_7.json"))

        expected_fields = {
            "deviceId", "channelId", "targetId", "targetName", "presetIndex", "presetName", "roi", "focusAnchorRoi", "notes", "updatedAt"
        }
        self.assertEqual(set(current_payload), expected_fields)
        self.assertEqual(set(bundled_payload), expected_fields)
        deployment_path = self.fake_settings.data_root / "deployment.json"
        deployment_path.write_bytes(self.service.current_deployment_bytes("DEVICE_001", 7))
        self.assertEqual(self.service.load_path(deployment_path).presetIndex, 7)

    def test_archive_export_includes_unmigrated_legacy_current_config(self) -> None:
        legacy_path = self.fake_settings.calibration_dir / "DEVICE_001_7.json"
        legacy_snapshot = self.fake_settings.data_root / "snapshots" / "legacy.png"
        legacy_snapshot.parent.mkdir()
        legacy_snapshot.write_bytes(b"fake-png")
        legacy_path.write_text(
            json.dumps(
                {
                    "deviceId": "DEVICE_001",
                    "channelId": "0",
                    "targetId": "AERATOR_001",
                    "targetName": "旧标定",
                    "presetIndex": 7,
                    "presetName": "增氧机 1",
                    "roi": {"x": 1, "y": 2, "width": 3, "height": 4},
                    "focusAnchorRoi": {"x": 5, "y": 6, "width": 7, "height": 8},
                    "snapshotPath": "snapshots/legacy.png",
                    "notes": "",
                    "updatedAt": "2026-07-17T00:00:00+00:00",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        archive = zipfile.ZipFile(io.BytesIO(self.service.build_history_archive()))

        self.assertIn("current/DEVICE_001_7.json", archive.namelist())
        self.assertIn("snapshots/legacy.png", archive.namelist())

    def test_restore_rejects_history_without_annotated_snapshot(self) -> None:
        self.service.save(request())
        annotated = self.fake_settings.calibration_history_dir / "DEVICE_001_7" / "v0001" / "snapshot-annotated.png"
        annotated.unlink()

        with self.assertRaisesRegex(ValueError, "annotated snapshot"):
            self.service.restore("DEVICE_001", 7, 1)

    def test_posix_artifact_paths_resolve_using_platform_path_parts(self) -> None:
        expected = self.fake_settings.data_root / "calibration_history" / "DEVICE_001_7" / "v0001" / "snapshot-original.png"
        expected.parent.mkdir(parents=True)
        expected.write_bytes(b"fake-png")

        resolved = self.service._resolve_artifact_path("calibration_history/DEVICE_001_7/v0001/snapshot-original.png")

        self.assertEqual(resolved, expected)
        self.assertEqual(self.service._snapshot_as_data_url("calibration_history/DEVICE_001_7/v0001/snapshot-original.png"), PNG_DATA_URL)


if __name__ == "__main__":
    unittest.main()
