"""读取 JPEG / MP4 中 Sentrix XMP 的来源地址，不依赖 ffprobe 的标签投影。"""

from pathlib import Path
from xml.etree import ElementTree

from sentrix.domain.media.source_metadata import SourceMetadata

NAMESPACE = "urn:sentrix:media:1.0/"


def read_source_address(path: Path) -> SourceMetadata:
    # XMP 可位于 MP4 尾部；分段扫描，不把整段视频载入内存。
    packet = b""
    pending = b""
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            pending += chunk
            start = pending.find(b"<x:xmpmeta")
            if start < 0:
                pending = pending[-32:]
                continue
            pending = pending[start:]
            end = pending.find(b"</x:xmpmeta>")
            if end >= 0:
                packet = pending[: end + len(b"</x:xmpmeta>")]
                break
            if len(pending) > 2 * 1024 * 1024:
                raise ValueError("XMP packet exceeds 2 MiB")
    if not packet:
        return SourceMetadata()
    root = ElementTree.fromstring(packet)

    def value(name: str) -> str:
        key = f"{{{NAMESPACE}}}{name}"
        for node in root.iter():
            if key in node.attrib:
                return node.attrib[key]
            if node.tag == key:
                return node.text or ""
        return ""

    address = value("PreciseAddress")
    return SourceMetadata(
        precise_address=address if address.strip() else "",
        address_source=value("AddressSource"),
        address_source_sha256=value("AddressSourceSHA256"),
    )
