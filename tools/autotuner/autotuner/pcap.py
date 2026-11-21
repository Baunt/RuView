"""Strict classic-PCAP Ethernet/IPv4 UDP reader for passive ESP32 diagnostics.

Original PCAP timestamps are retained. No per-packet monotonic clock is invented,
and this is deliberately separate from the live UDP tee capture format.
"""
import ipaddress
import struct


def ethernet_udp(packet):
    """Decode a complete Ethernet frame; return None for other protocols."""
    packet = bytes(packet)
    if len(packet) < 14:
        raise ValueError("Incomplete Ethernet header")
    kind = struct.unpack_from("!H", packet, 12)[0]
    at = 14
    for _ in range(2):
        if kind not in (0x8100, 0x88A8):
            break
        if at + 4 > len(packet):
            raise ValueError("Incomplete VLAN header")
        kind = struct.unpack_from("!H", packet, at + 2)[0]
        at += 4
    if kind != 0x0800:
        return None
    if at + 20 > len(packet):
        raise ValueError("Incomplete IPv4 header")
    ihl = (packet[at] & 15) * 4
    total = struct.unpack_from("!H", packet, at + 2)[0]
    if packet[at] >> 4 != 4 or ihl < 20 or total < ihl or at + total > len(packet):
        raise ValueError("Invalid IPv4 length")
    if packet[at + 9] != 17:
        return None
    if struct.unpack_from("!H", packet, at + 6)[0] & 0x3FFF:
        raise ValueError("Fragmented UDP requires reassembly and is not supported")
    udp_at = at + ihl
    if udp_at + 8 > at + total:
        raise ValueError("Incomplete UDP header")
    source_port, destination_port, udp_length, _ = struct.unpack_from("!HHHH", packet, udp_at)
    if udp_length < 8 or udp_at + udp_length > at + total:
        raise ValueError("Invalid UDP length")
    return {"source_ip": str(ipaddress.IPv4Address(packet[at + 12:at + 16])),
            "destination_ip": str(ipaddress.IPv4Address(packet[at + 16:at + 20])),
            "source_port": source_port, "destination_port": destination_port,
            "payload": packet[udp_at + 8:udp_at + udp_length]}


def udp_datagrams(data):
    formats = {
        b"\xd4\xc3\xb2\xa1": ("<", 1_000_000),
        b"\xa1\xb2\xc3\xd4": (">", 1_000_000),
        b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000),
        b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000),
    }
    magic = bytes(data[:4])
    if len(data) < 24 or magic not in formats:
        raise ValueError("Unsupported or incomplete classic PCAP header")
    endian, scale = formats[magic]
    major, minor, _, _, snaplen, linktype = struct.unpack_from(endian + "HHIIII", data, 4)
    if (major, minor) != (2, 4) or linktype != 1 or not 1 <= snaplen <= 16_777_216:
        raise ValueError("Only bounded Ethernet PCAP 2.4 is supported")
    offset = 24
    while offset < len(data):
        if offset + 16 > len(data):
            raise ValueError("Incomplete PCAP packet header")
        seconds, fraction, length, original = struct.unpack_from(endian + "IIII", data, offset)
        offset += 16
        if fraction >= scale or length > snaplen or length != original or offset + length > len(data):
            raise ValueError("Truncated or invalid PCAP packet")
        packet = bytes(data[offset:offset + length])
        offset += length
        decoded = ethernet_udp(packet)
        if decoded is None:
            continue
        yield {
            "seconds": seconds, "fraction": fraction, "fraction_scale": scale,
            "received_at": seconds + fraction / scale,
            **decoded,
        }
