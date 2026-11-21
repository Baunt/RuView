import struct

import pytest

from autotuner.pcap import udp_datagrams


def capture(payload=b"CSI\x00\xff", *, endian="<", nano=True, vlan=False, options=False, padding=True):
    magic = ({("<", True): b"\x4d\x3c\xb2\xa1", (">", True): b"\xa1\xb2\x3c\x4d",
              ("<", False): b"\xd4\xc3\xb2\xa1", (">", False): b"\xa1\xb2\xc3\xd4"})[(endian, nano)]
    udp = struct.pack("!HHHH", 41000, 5005, 8 + len(payload), 0) + payload
    ihl = 24 if options else 20
    ip = struct.pack("!BBHHHBBH4s4s", 0x40 + ihl // 4, 0, ihl + len(udp), 1, 0, 64, 17, 0,
                     b"\xc0\xa8\x01\x75", b"\x0a\x0a\x0a\x65") + bytes(ihl - 20) + udp
    ethernet = bytes(12) + (b"\x81\x00\x00\x01\x08\x00" if vlan else b"\x08\x00")
    packet = ethernet + ip + (bytes(20) if padding else b"")
    header = magic + struct.pack(endian + "HHIIII", 2, 4, 0, 0, 65535, 1)
    return header + struct.pack(endian + "IIII", 1795190000, 123456, len(packet), len(packet)) + packet


@pytest.mark.parametrize("endian,nano", [("<", True), (">", True), ("<", False), (">", False)])
def test_timestamps_and_original_udp_payload_survive_pcap_variants(endian, nano):
    packet, = udp_datagrams(capture(endian=endian, nano=nano))
    assert packet["payload"] == b"CSI\x00\xff"
    assert packet["fraction"] == 123456
    assert packet["fraction_scale"] == (1_000_000_000 if nano else 1_000_000)
    assert packet["source_ip"] == "192.168.1.117"
    assert packet["destination_port"] == 5005


def test_vlan_ip_options_and_ethernet_padding_do_not_change_udp_bytes():
    packet, = udp_datagrams(capture(vlan=True, options=True))
    assert packet["payload"] == b"CSI\x00\xff"


@pytest.mark.parametrize("cut", [1, 23, 25, 39, 45, 60])
def test_truncated_captures_are_rejected(cut):
    with pytest.raises(ValueError):
        list(udp_datagrams(capture()[:cut]))


def test_udp_length_cannot_consume_ethernet_padding():
    packet = bytearray(capture())
    struct.pack_into("!H", packet, 24 + 16 + 14 + 20 + 4, 40)
    with pytest.raises(ValueError, match="UDP length"):
        list(udp_datagrams(packet))


def test_fragments_are_not_misread_as_complete_datagrams():
    packet = bytearray(capture())
    struct.pack_into("!H", packet, 24 + 16 + 14 + 6, 0x2000)
    with pytest.raises(ValueError, match="Fragmented"):
        list(udp_datagrams(packet))
