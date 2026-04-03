"""Tests for camera API models, response parsing, and client methods."""

import base64
import json
from datetime import datetime, timedelta

import jwt as pyjwt
import pytest
from aiohttp import ClientSession
from aioresponses import aioresponses
from pydantic import ValidationError

from verisure_italy.client import API_URL, VerisureClient
from verisure_italy.models import (
    CAMERA_DEVICE_TYPES,
    CAMERA_IMAGE_DEVICE_TYPE,
    CameraDevice,
    Installation,
    PhotoDevice,
    PhotoImage,
    RawDevice,
    RequestImagesResult,
    RequestImagesStatusResult,
    Thumbnail,
)
from verisure_italy.responses import (
    DeviceListEnvelope,
    PhotoImagesEnvelope,
    RequestImagesEnvelope,
    RequestImagesStatusEnvelope,
    ThumbnailEnvelope,
)

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

# Minimal valid JPEG: starts with 0xFFD8, ends with 0xFFD9
_JPEG_BYTES = bytes([0xFF, 0xD8, 0xFF, 0xE0] + [0x00] * 100 + [0xFF, 0xD9])
_JPEG_BASE64 = base64.b64encode(_JPEG_BYTES).decode()


def _make_jwt(exp_minutes: int = 60) -> str:
    exp = (datetime.now() + timedelta(minutes=exp_minutes)).timestamp()
    return pyjwt.encode(
        {"exp": exp},
        "test-secret-key-that-is-long-enough-for-hs256",
        algorithm="HS256",
    )


INSTALLATION = Installation(
    number="1234567",
    alias="Casa",
    panel="SDVECU",
    type="VERISURE",
    name="Test",
    surname="User",
    address="Via Test 1",
    city="Roma",
    postcode="00100",
    province="RM",
    email="test@test.it",
    phone="+39000000000",
)


@pytest.fixture
def mock_api():
    with aioresponses() as m:
        yield m


@pytest.fixture
async def http_session():
    async with ClientSession() as session:
        yield session


@pytest.fixture
async def client(http_session: ClientSession) -> VerisureClient:
    c = VerisureClient(
        username="test@test.it",
        password="password",
        http_session=http_session,
        device_id="test-device-id",
        uuid="test-uuid",
        id_device_indigitall="",
        poll_delay=0.01,
        poll_timeout=5.0,
    )
    # Pre-set auth to skip login
    c._auth_token = _make_jwt()
    c._auth_token_exp = datetime.now() + timedelta(hours=1)
    c._capabilities[INSTALLATION.number] = _make_jwt()
    c._capabilities_exp[INSTALLATION.number] = datetime.now() + timedelta(hours=1)
    return c


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestCameraModels:
    """Pydantic model parsing for camera-related types."""

    def test_raw_device_from_api_json(self) -> None:
        data = {
            "id": "cam1",
            "code": "5",
            "zoneId": "QR05",
            "name": "Camera Sala",
            "type": "QR",
            "isActive": True,
            "serialNumber": "ABC123",
        }
        raw = RawDevice.model_validate(data)
        assert raw.id == "cam1"
        assert raw.code == "5"
        assert raw.zone_id == "QR05"
        assert raw.name == "Camera Sala"
        assert raw.device_type == "QR"
        assert raw.is_active is True
        assert raw.serial_number == "ABC123"

    def test_raw_device_null_zone_id(self) -> None:
        data = {
            "id": "cam2",
            "code": "3",
            "zoneId": None,
            "name": "PIR Camera",
            "type": "YR",
            "isActive": True,
            "serialNumber": None,
        }
        raw = RawDevice.model_validate(data)
        assert raw.zone_id is None

    def test_camera_device_frozen(self) -> None:
        cam = CameraDevice(
            id="cam1",
            code=5,
            zone_id="QR05",
            name="Camera Sala",
            device_type="QR",
        )
        with pytest.raises(ValidationError):
            cam.name = "changed"  # type: ignore[misc]

    def test_thumbnail_from_api_json(self) -> None:
        data = {
            "idSignal": "signal123",
            "deviceId": "dev1",
            "deviceCode": "5",
            "deviceAlias": "Camera Sala",
            "timestamp": "2026-04-03T12:00:00Z",
            "signalType": "ALARM",
            "image": _JPEG_BASE64,
            "type": "JPEG",
            "quality": "high",
        }
        thumb = Thumbnail.model_validate(data)
        assert thumb.id_signal == "signal123"
        assert thumb.image == _JPEG_BASE64
        assert thumb.timestamp == "2026-04-03T12:00:00Z"

    def test_thumbnail_all_null(self) -> None:
        data = {
            "idSignal": None,
            "deviceId": None,
            "deviceCode": None,
            "deviceAlias": None,
            "timestamp": None,
            "signalType": None,
            "image": None,
            "type": None,
            "quality": None,
        }
        thumb = Thumbnail.model_validate(data)
        assert thumb.id_signal is None
        assert thumb.image is None

    def test_photo_image_model(self) -> None:
        img = PhotoImage(id="img1", image=_JPEG_BASE64, type="BINARY")
        assert img.type == "BINARY"
        assert len(img.image) > 0

    def test_photo_device_model(self) -> None:
        dev = PhotoDevice.model_validate({
            "id": "dev1",
            "idSignal": "sig1",
            "code": "5",
            "name": "Camera",
            "quality": "high",
            "images": [
                {"id": "img1", "image": _JPEG_BASE64, "type": "BINARY"},
                {"id": "img2", "image": "header_data", "type": "HEADER"},
            ],
        })
        assert len(dev.images) == 2
        assert dev.images[0].type == "BINARY"
        assert dev.images[1].type == "HEADER"

    def test_request_images_result(self) -> None:
        r = RequestImagesResult.model_validate({
            "res": "OK",
            "msg": "success",
            "referenceId": "ref123",
        })
        assert r.reference_id == "ref123"

    def test_request_images_status_result(self) -> None:
        r = RequestImagesStatusResult.model_validate({
            "res": "WAIT",
            "msg": "processing image",
            "numinst": "1234567",
            "status": "pending",
        })
        assert r.res == "WAIT"
        assert r.msg is not None
        assert "processing" in r.msg


class TestCameraConstants:
    """Camera device type constants."""

    def test_camera_device_types(self) -> None:
        assert "QR" in CAMERA_DEVICE_TYPES
        assert "YR" in CAMERA_DEVICE_TYPES
        assert "YP" in CAMERA_DEVICE_TYPES
        assert "QP" in CAMERA_DEVICE_TYPES
        assert len(CAMERA_DEVICE_TYPES) == 4

    def test_image_device_type_map_covers_all(self) -> None:
        for dt in CAMERA_DEVICE_TYPES:
            assert dt in CAMERA_IMAGE_DEVICE_TYPE


# ---------------------------------------------------------------------------
# Response envelope tests
# ---------------------------------------------------------------------------


class TestCameraEnvelopes:
    """Response envelope parsing for camera operations."""

    def test_device_list_envelope(self) -> None:
        raw = json.dumps({
            "data": {
                "xSDeviceList": {
                    "res": "OK",
                    "devices": [
                        {
                            "id": "cam1",
                            "code": "5",
                            "zoneId": "QR05",
                            "name": "Camera Sala",
                            "type": "QR",
                            "isActive": True,
                            "serialNumber": "ABC",
                        },
                        {
                            "id": "pir1",
                            "code": "2",
                            "zoneId": None,
                            "name": "PIR Ingresso",
                            "type": "YR",
                            "isActive": True,
                            "serialNumber": None,
                        },
                        {
                            "id": "sensor1",
                            "code": "1",
                            "zoneId": "MC01",
                            "name": "Sensore Porta",
                            "type": "MC",
                            "isActive": True,
                            "serialNumber": None,
                        },
                    ],
                }
            }
        })
        envelope = DeviceListEnvelope.model_validate_json(raw)
        devices = envelope.data.xSDeviceList.devices
        assert len(devices) == 3
        # All three are raw — client method filters
        assert devices[0].device_type == "QR"
        assert devices[2].device_type == "MC"

    def test_request_images_envelope(self) -> None:
        raw = json.dumps({
            "data": {
                "xSRequestImages": {
                    "res": "OK",
                    "msg": None,
                    "referenceId": "ref-abc-123",
                }
            }
        })
        envelope = RequestImagesEnvelope.model_validate_json(raw)
        assert envelope.data.xSRequestImages.reference_id == "ref-abc-123"

    def test_request_images_status_envelope(self) -> None:
        raw = json.dumps({
            "data": {
                "xSRequestImagesStatus": {
                    "res": "OK",
                    "msg": "done",
                    "numinst": "1234567",
                    "status": "completed",
                }
            }
        })
        envelope = RequestImagesStatusEnvelope.model_validate_json(raw)
        assert envelope.data.xSRequestImagesStatus.res == "OK"

    def test_thumbnail_envelope(self) -> None:
        raw = json.dumps({
            "data": {
                "xSGetThumbnail": {
                    "idSignal": "sig1",
                    "deviceId": "dev1",
                    "deviceCode": "5",
                    "deviceAlias": "Camera",
                    "timestamp": "2026-04-03T12:00:00Z",
                    "signalType": "ALARM",
                    "image": _JPEG_BASE64,
                    "type": "JPEG",
                    "quality": "high",
                }
            }
        })
        envelope = ThumbnailEnvelope.model_validate_json(raw)
        assert envelope.data.xSGetThumbnail.id_signal == "sig1"
        assert envelope.data.xSGetThumbnail.image == _JPEG_BASE64

    def test_photo_images_envelope(self) -> None:
        raw = json.dumps({
            "data": {
                "xSGetPhotoImages": {
                    "devices": [
                        {
                            "id": "dev1",
                            "idSignal": "sig1",
                            "code": "5",
                            "name": "Camera",
                            "quality": "high",
                            "images": [
                                {"id": "img1", "image": _JPEG_BASE64, "type": "BINARY"},
                            ],
                        }
                    ]
                }
            }
        })
        envelope = PhotoImagesEnvelope.model_validate_json(raw)
        devices = envelope.data.xSGetPhotoImages.devices
        assert devices is not None
        assert len(devices) == 1
        assert len(devices[0].images) == 1

    def test_photo_images_envelope_null_devices(self) -> None:
        raw = json.dumps({
            "data": {"xSGetPhotoImages": {"devices": None}}
        })
        envelope = PhotoImagesEnvelope.model_validate_json(raw)
        assert envelope.data.xSGetPhotoImages.devices is None


# ---------------------------------------------------------------------------
# Client method tests
# ---------------------------------------------------------------------------


class TestListCameraDevices:
    """Client.list_camera_devices: filters and constructs CameraDevice."""

    @pytest.mark.asyncio
    async def test_filters_camera_types_only(
        self, client: VerisureClient, mock_api: aioresponses
    ) -> None:
        def _dev(
            dev_id: str, code: str, zone: str | None,
            name: str, typ: str, active: bool = True,
        ) -> dict[str, str | bool | None]:
            return {
                "id": dev_id, "code": code, "zoneId": zone,
                "name": name, "type": typ,
                "isActive": active, "serialNumber": None,
            }

        response = {
            "data": {
                "xSDeviceList": {
                    "res": "OK",
                    "devices": [
                        _dev("cam1", "5", "QR05", "Camera", "QR"),
                        _dev("sensor1", "1", "MC01", "Sensor", "MC"),
                        _dev("pir1", "3", "YR03", "PIR", "YR"),
                    ],
                }
            }
        }
        mock_api.post(API_URL, payload=response)
        cameras = await client.list_camera_devices(INSTALLATION)
        assert len(cameras) == 2
        assert cameras[0].device_type == "QR"
        assert cameras[1].device_type == "YR"

    @pytest.mark.asyncio
    async def test_filters_inactive_devices(
        self, client: VerisureClient, mock_api: aioresponses
    ) -> None:
        def _dev(
            dev_id: str, code: str, zone: str,
            name: str, active: bool,
        ) -> dict[str, str | bool | None]:
            return {
                "id": dev_id, "code": code, "zoneId": zone,
                "name": name, "type": "QR",
                "isActive": active, "serialNumber": None,
            }

        response = {
            "data": {
                "xSDeviceList": {
                    "res": "OK",
                    "devices": [
                        _dev("cam1", "5", "QR05", "Active", True),
                        _dev("cam2", "6", "QR06", "Inactive", False),
                    ],
                }
            }
        }
        mock_api.post(API_URL, payload=response)
        cameras = await client.list_camera_devices(INSTALLATION)
        assert len(cameras) == 1
        assert cameras[0].name == "Active"

    @pytest.mark.asyncio
    async def test_constructs_zone_id_when_null(
        self, client: VerisureClient, mock_api: aioresponses
    ) -> None:
        response = {
            "data": {
                "xSDeviceList": {
                    "res": "OK",
                    "devices": [{
                        "id": "pir1", "code": "3", "zoneId": None,
                        "name": "PIR", "type": "YR",
                        "isActive": True, "serialNumber": None,
                    }],
                }
            }
        }
        mock_api.post(API_URL, payload=response)
        cameras = await client.list_camera_devices(INSTALLATION)
        assert cameras[0].zone_id == "YR03"

    @pytest.mark.asyncio
    async def test_non_numeric_code_skipped(
        self, client: VerisureClient, mock_api: aioresponses
    ) -> None:
        response = {
            "data": {
                "xSDeviceList": {
                    "res": "OK",
                    "devices": [{
                        "id": "weird-id", "code": "abc",
                        "zoneId": None, "name": "Weird", "type": "QR",
                        "isActive": True, "serialNumber": None,
                    }],
                }
            }
        }
        mock_api.post(API_URL, payload=response)
        cameras = await client.list_camera_devices(INSTALLATION)
        assert cameras == []

    @pytest.mark.asyncio
    async def test_empty_device_list(
        self, client: VerisureClient, mock_api: aioresponses
    ) -> None:
        response = {
            "data": {"xSDeviceList": {"res": "OK", "devices": []}}
        }
        mock_api.post(API_URL, payload=response)
        cameras = await client.list_camera_devices(INSTALLATION)
        assert cameras == []


class TestGetThumbnail:
    """Client.get_thumbnail: fetches and parses thumbnail response."""

    @pytest.mark.asyncio
    async def test_returns_thumbnail(
        self, client: VerisureClient, mock_api: aioresponses
    ) -> None:
        camera = CameraDevice(
            id="cam1", code=5, zone_id="QR05",
            name="Camera", device_type="QR",
        )
        response = {
            "data": {
                "xSGetThumbnail": {
                    "idSignal": "sig1",
                    "deviceId": "dev1",
                    "deviceCode": "5",
                    "deviceAlias": "Camera",
                    "timestamp": "2026-04-03T12:00:00Z",
                    "signalType": "ALARM",
                    "image": _JPEG_BASE64,
                    "type": "JPEG",
                    "quality": "high",
                }
            }
        }
        mock_api.post(API_URL, payload=response)
        thumb = await client.get_thumbnail(INSTALLATION, camera)
        assert thumb.id_signal == "sig1"
        assert thumb.image == _JPEG_BASE64


class TestGetPhotoImages:
    """Client.get_photo_images: picks best BINARY image."""

    @pytest.mark.asyncio
    async def test_returns_largest_binary_image(
        self, client: VerisureClient, mock_api: aioresponses
    ) -> None:
        small_jpeg = base64.b64encode(bytes([0xFF, 0xD8] + [0x00] * 10 + [0xFF, 0xD9])).decode()
        large_jpeg = _JPEG_BASE64  # larger

        response = {
            "data": {
                "xSGetPhotoImages": {
                    "devices": [{
                        "id": "dev1",
                        "idSignal": "sig1",
                        "code": "5",
                        "name": "Camera",
                        "quality": "high",
                        "images": [
                            {"id": "img1", "image": small_jpeg, "type": "BINARY"},
                            {"id": "img2", "image": large_jpeg, "type": "BINARY"},
                            {"id": "img3", "image": "header_data", "type": "HEADER"},
                        ],
                    }]
                }
            }
        }
        mock_api.post(API_URL, payload=response)
        result = await client.get_photo_images(INSTALLATION, "sig1", "ALARM")
        assert result is not None
        assert result == _JPEG_BYTES

    @pytest.mark.asyncio
    async def test_returns_none_on_no_devices(
        self, client: VerisureClient, mock_api: aioresponses
    ) -> None:
        response = {
            "data": {"xSGetPhotoImages": {"devices": None}}
        }
        mock_api.post(API_URL, payload=response)
        result = await client.get_photo_images(INSTALLATION, "sig1", "ALARM")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_no_binary_images(
        self, client: VerisureClient, mock_api: aioresponses
    ) -> None:
        response = {
            "data": {
                "xSGetPhotoImages": {
                    "devices": [{
                        "id": "dev1",
                        "idSignal": "sig1",
                        "code": "5",
                        "name": "Camera",
                        "quality": None,
                        "images": [
                            {"id": "img1", "image": "header_only", "type": "HEADER"},
                        ],
                    }]
                }
            }
        }
        mock_api.post(API_URL, payload=response)
        result = await client.get_photo_images(INSTALLATION, "sig1", "ALARM")
        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_non_jpeg(
        self, client: VerisureClient, mock_api: aioresponses
    ) -> None:
        not_jpeg = base64.b64encode(b"not a jpeg image at all").decode()
        response = {
            "data": {
                "xSGetPhotoImages": {
                    "devices": [{
                        "id": "dev1",
                        "idSignal": "sig1",
                        "code": "5",
                        "name": "Camera",
                        "quality": None,
                        "images": [
                            {"id": "img1", "image": not_jpeg, "type": "BINARY"},
                        ],
                    }]
                }
            }
        }
        mock_api.post(API_URL, payload=response)
        result = await client.get_photo_images(INSTALLATION, "sig1", "ALARM")
        assert result is None


class TestRequestImages:
    """Client.request_images: sends capture command."""

    @pytest.mark.asyncio
    async def test_returns_reference_id(
        self, client: VerisureClient, mock_api: aioresponses
    ) -> None:
        camera = CameraDevice(
            id="cam1", code=5, zone_id="QR05",
            name="Camera", device_type="QR",
        )
        response = {
            "data": {
                "xSRequestImages": {
                    "res": "OK",
                    "msg": None,
                    "referenceId": "capture-ref-123",
                }
            }
        }
        mock_api.post(API_URL, payload=response)
        ref_id = await client.request_images(INSTALLATION, camera)
        assert ref_id == "capture-ref-123"

    @pytest.mark.asyncio
    async def test_raises_on_rejection(
        self, client: VerisureClient, mock_api: aioresponses
    ) -> None:
        from verisure_italy.exceptions import OperationFailedError

        camera = CameraDevice(
            id="cam1", code=5, zone_id="QR05",
            name="Camera", device_type="QR",
        )
        response = {
            "data": {
                "xSRequestImages": {
                    "res": "ERROR",
                    "msg": "camera unavailable",
                    "referenceId": "ref",
                }
            }
        }
        mock_api.post(API_URL, payload=response)
        with pytest.raises(OperationFailedError, match="camera unavailable"):
            await client.request_images(INSTALLATION, camera)


class TestCheckRequestImagesStatus:
    """Client.check_request_images_status: detects completion."""

    @pytest.mark.asyncio
    async def test_processing_returns_false(
        self, client: VerisureClient, mock_api: aioresponses
    ) -> None:
        camera = CameraDevice(
            id="cam1", code=5, zone_id="QR05",
            name="Camera", device_type="QR",
        )
        response = {
            "data": {
                "xSRequestImagesStatus": {
                    "res": "WAIT",
                    "msg": "processing image",
                    "numinst": "1234567",
                    "status": "pending",
                }
            }
        }
        mock_api.post(API_URL, payload=response)
        done = await client.check_request_images_status(
            INSTALLATION, camera, "ref123", 1
        )
        assert done is False

    @pytest.mark.asyncio
    async def test_completed_returns_true(
        self, client: VerisureClient, mock_api: aioresponses
    ) -> None:
        camera = CameraDevice(
            id="cam1", code=5, zone_id="QR05",
            name="Camera", device_type="QR",
        )
        response = {
            "data": {
                "xSRequestImagesStatus": {
                    "res": "OK",
                    "msg": "done",
                    "numinst": "1234567",
                    "status": "completed",
                }
            }
        }
        mock_api.post(API_URL, payload=response)
        done = await client.check_request_images_status(
            INSTALLATION, camera, "ref123", 2
        )
        assert done is True
