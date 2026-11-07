"""Judge attachments use the product v1 API and retain required-image failures."""

import base64
import io

import httpx
import pytest
from PIL import Image

from photobench.infrastructure.http.client import HttpxJsonClient
from photobench.infrastructure.http.judge_images import inline_judge_images


def test_gt_image_uses_v1_asset_endpoint_and_resizes_jpeg():
    image = io.BytesIO()
    Image.new("RGB", (1800, 900)).save(image, format="PNG")
    urls = []

    def handler(request):
        urls.append(str(request.url))
        if request.url.path == "/api/v1/assets/asset-a/file":
            return httpx.Response(200, content=image.getvalue())
        return httpx.Response(404)

    http = HttpxJsonClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = inline_judge_images(
        http, "http://product/", [{"asset_id": "asset-a", "require_attachment": True}], {}
    )
    assert urls == ["http://product/api/v1/assets/asset-a/file"]
    with Image.open(io.BytesIO(base64.b64decode(result[0]["image_url"]["url"].split(",")[1]))) as decoded:
        assert decoded.format == "JPEG" and decoded.size == (896, 448)


def test_required_gt_preserves_http_failure_optional_evidence_can_skip():
    http = HttpxJsonClient(client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))))
    with pytest.raises(httpx.HTTPStatusError, match="404"):
        inline_judge_images(http, "http://product", [{"asset_id": "a", "require_attachment": True}], {})
    assert inline_judge_images(http, "http://product", [{"asset_id": "a"}], {}) == []
