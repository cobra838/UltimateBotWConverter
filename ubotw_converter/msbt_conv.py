#!/usr/bin/env python
from __future__ import annotations

import struct
from dataclasses import dataclass, field

MSBT_MAGIC = b"MsgStdBn"
BOM_LE = b"\xFF\xFE"
BOM_BE = b"\xFE\xFF"
LBL1_MAGIC = b"LBL1"
ATR1_MAGIC = b"ATR1"
ATO1_MAGIC = b"ATO1"
TSY1_MAGIC = b"TSY1"
TXT2_MAGIC = b"TXT2"
TXTW_MAGIC = b"TXTW"
NLI1_MAGIC = b"NLI1"


def _calc_hash(label: str, buckets: int) -> int:
    h = 0
    for char in label:
        h = h * 0x492 + ord(char)
    return (h & 0xFFFFFFFF) % buckets


@dataclass
class _MsbtFile:
    big_endian: bool = False
    source_big_endian: bool = False
    version: int = 3
    encoding: str = "utf-16"
    label_groups: int = 0
    has_lbl1: bool = False
    has_atr1: bool = False
    has_ato1: bool = False
    has_tsy1: bool = False
    has_nli1: bool = False
    has_txtw: bool = False
    labels: dict[str, int] = field(default_factory=dict)
    texts: dict[int, str] = field(default_factory=dict)
    text_raw: dict[int, bytes] = field(default_factory=dict)
    attributes: dict[int, str] = field(default_factory=dict)
    attribute_has_text: bool = False
    text_order: list[int] = field(default_factory=list)
    raw_sections: dict[bytes, bytes] = field(default_factory=dict)

    @property
    def _enc_width(self) -> int:
        return {"utf-8": 1, "utf-16": 2, "utf-32": 4}.get(self.encoding, 2)

    @classmethod
    def from_bytes(cls, data: bytes) -> "_MsbtFile":
        if data[:8] != MSBT_MAGIC:
            raise ValueError("Not an MSBT file")
        msbt = cls()
        bom = data[8:10]
        if bom == BOM_LE:
            msbt.big_endian = False
            msbt.source_big_endian = False
            endian = "<"
        elif bom == BOM_BE:
            msbt.big_endian = True
            msbt.source_big_endian = True
            endian = ">"
        else:
            raise ValueError(f"Bad MSBT BOM: {bom.hex()}")

        msbt.encoding = {0: "utf-8", 1: "utf-16", 2: "utf-32"}.get(data[0x0C], "utf-16")
        msbt.version = data[0x0D]
        section_count = struct.unpack_from(f"{endian}H", data, 0x0E)[0]

        pos = 0x20
        for _ in range(section_count):
            if pos + 16 > len(data):
                break
            magic = data[pos : pos + 4]
            section_size = struct.unpack_from(f"{endian}I", data, pos + 4)[0]
            section_data = data[pos + 16 : pos + 16 + section_size]
            msbt.raw_sections[magic] = section_data

            if magic == LBL1_MAGIC:
                msbt.has_lbl1 = True
                msbt._parse_lbl1(section_data, endian)
            elif magic == ATR1_MAGIC:
                msbt.has_atr1 = True
                msbt._parse_atr1(section_data, endian)
            elif magic == ATO1_MAGIC:
                msbt.has_ato1 = True
            elif magic == TSY1_MAGIC:
                msbt.has_tsy1 = True
            elif magic == NLI1_MAGIC:
                msbt.has_nli1 = True
            elif magic == TXT2_MAGIC:
                msbt.has_txtw = False
                msbt._parse_txt(section_data, endian)
            elif magic == TXTW_MAGIC:
                msbt.has_txtw = True
                msbt._parse_txt(section_data, endian)

            pos += 16 + ((section_size + 15) & ~15)
        return msbt

    def _parse_lbl1(self, data: bytes, endian: str) -> None:
        if len(data) < 4:
            return
        self.label_groups = struct.unpack_from(f"{endian}I", data, 0)[0]
        groups = [
            (
                struct.unpack_from(f"{endian}I", data, 4 + i * 8)[0],
                struct.unpack_from(f"{endian}I", data, 8 + i * 8)[0],
            )
            for i in range(self.label_groups)
        ]
        labels: dict[str, int] = {}
        for count, offset in groups:
            pos = offset
            for _ in range(count):
                if pos >= len(data):
                    break
                label_len = data[pos]
                pos += 1
                label = data[pos : pos + label_len].decode("utf-8")
                pos += label_len
                msg_index = struct.unpack_from(f"{endian}I", data, pos)[0]
                pos += 4
                labels[label] = msg_index
        self.labels = labels

    def _parse_txt(self, data: bytes, endian: str) -> None:
        if len(data) < 4:
            return
        count = struct.unpack_from(f"{endian}I", data, 0)[0]
        offsets = [struct.unpack_from(f"{endian}I", data, 4 + i * 4)[0] for i in range(count)]
        if self.encoding == "utf-16":
            encoding = "utf-16-be" if self.big_endian else "utf-16-le"
        elif self.encoding == "utf-32":
            encoding = "utf-32-be" if self.big_endian else "utf-32-le"
        else:
            encoding = "utf-8"
        texts: dict[int, str] = {}
        raw_texts: dict[int, bytes] = {}
        for index in range(count):
            start = offsets[index]
            end = offsets[index + 1] if index + 1 < count else len(data)
            raw = data[start:end]
            raw_texts[index] = raw
            text = raw.decode(encoding)
            if text.endswith("\0"):
                text = text[:-1]
            texts[index] = text
        self.texts = texts
        self.text_raw = raw_texts
        self.text_order = list(range(count))

    def _parse_atr1(self, data: bytes, endian: str) -> None:
        if len(data) < 8:
            return
        count = struct.unpack_from(f"{endian}I", data, 0)[0]
        size = struct.unpack_from(f"{endian}I", data, 4)[0]
        encoding = "utf-16-be" if self.big_endian else "utf-16-le"
        attributes: dict[int, str] = {}
        attr_table_len = count * 4 + 8
        has_text = size == 4 and len(data) >= attr_table_len + count * self._enc_width
        if has_text:
            for index in range(count):
                offset = struct.unpack_from(f"{endian}I", data, 8 + index * 4)[0]
                if offset == 0 or offset < attr_table_len or offset > len(data):
                    attributes[index] = ""
                    continue
                end = offset
                while end + 1 < len(data) and not (data[end] == 0 and data[end + 1] == 0):
                    end += 2
                attributes[index] = data[offset:end].decode(encoding).rstrip("\0")
        else:
            for index in range(count):
                if size == 0:
                    attributes[index] = ""
                else:
                    start = 8 + index * size
                    end = start + size
                    if end <= len(data):
                        attributes[index] = data[start:end].hex()
        self.attribute_has_text = has_text
        self.attributes = attributes

    def to_bytes(self) -> bytes:
        endian = ">" if self.big_endian else "<"
        bom = BOM_BE if self.big_endian else BOM_LE

        header = bytearray(0x20)
        header[0:8] = MSBT_MAGIC
        header[8:10] = bom
        header[0x0C] = {"utf-8": 0, "utf-16": 1, "utf-32": 2}.get(self.encoding, 1)
        header[0x0D] = self.version

        sections: list[tuple[bytes, bytes]] = []
        if self.has_lbl1:
            sections.append((LBL1_MAGIC, self._build_lbl1(endian)))
        if self.has_atr1:
            atr1 = self._build_atr1(endian)
            if atr1 is not None:
                sections.append((ATR1_MAGIC, atr1))
        sections.append((TXTW_MAGIC if self.has_txtw else TXT2_MAGIC, self._build_txt(endian)))
        for magic, enabled in (
            (ATO1_MAGIC, self.has_ato1),
            (TSY1_MAGIC, self.has_tsy1),
            (NLI1_MAGIC, self.has_nli1),
        ):
            if enabled and magic in self.raw_sections:
                sections.append((magic, self.raw_sections[magic]))

        struct.pack_into(f"{endian}H", header, 0x0E, len(sections))

        out = bytearray(header)
        for magic, section_data in sections:
            aligned_size = (len(section_data) + 15) & ~15
            section_header = bytearray(16)
            section_header[0:4] = magic
            struct.pack_into(f"{endian}I", section_header, 4, len(section_data))
            out.extend(section_header)
            out.extend(section_data)
            out.extend(b"\xAB" * (aligned_size - len(section_data)))

        struct.pack_into(f"{endian}I", out, 0x12, len(out))
        return bytes(out)

    def _build_lbl1(self, endian: str) -> bytes:
        bucket_count = self.label_groups or max(len(self.labels), 1)
        buckets: list[list[tuple[str, int]]] = [[] for _ in range(bucket_count)]
        for label, msg_index in self.labels.items():
            buckets[_calc_hash(label, bucket_count)].append((label, msg_index))

        section = bytearray()
        section.extend(struct.pack(f"{endian}I", bucket_count))
        current_offset = 4 + bucket_count * 8
        offsets = []
        for bucket in buckets:
            offsets.append(current_offset)
            current_offset += sum(1 + len(label.encode("utf-8")) + 4 for label, _ in bucket)
        for index, bucket in enumerate(buckets):
            section.extend(struct.pack(f"{endian}I", len(bucket)))
            section.extend(struct.pack(f"{endian}I", offsets[index]))
        for bucket in buckets:
            for label, msg_index in bucket:
                encoded = label.encode("utf-8")
                section.append(len(encoded))
                section.extend(encoded)
                section.extend(struct.pack(f"{endian}I", msg_index))
        return bytes(section)

    def _build_atr1(self, endian: str) -> bytes | None:
        if not self.has_atr1:
            return None
        count = max(self.attributes.keys(), default=-1) + 1
        section = bytearray()
        if self.attribute_has_text:
            encoding = "utf-16-be" if self.big_endian else "utf-16-le"
            null = b"\x00\x00" if self.encoding == "utf-16" else b"\x00"
            section.extend(struct.pack(f"{endian}I", count))
            section.extend(struct.pack(f"{endian}I", 4))
            table_offset = len(section)
            section.extend(bytearray(count * 4))
            current_offset = table_offset + count * 4
            offsets: dict[int, int] = {}
            for index in range(count):
                attr = self.attributes.get(index, "")
                offsets[index] = current_offset
                encoded = attr.encode(encoding) + null
                section.extend(encoded)
                current_offset += len(encoded)
            for index in range(count):
                struct.pack_into(f"{endian}I", section, table_offset + index * 4, offsets[index])
        else:
            attr_size = 0
            for value in self.attributes.values():
                if value:
                    attr_size = max(attr_size, len(bytes.fromhex(value)))
            section.extend(struct.pack(f"{endian}I", count))
            section.extend(struct.pack(f"{endian}I", attr_size))
            for index in range(count):
                raw = (
                    bytes.fromhex(self.attributes[index])
                    if index in self.attributes and self.attributes[index]
                    else b""
                )
                section.extend(raw)
                section.extend(b"\x00" * (attr_size - len(raw)))
        return bytes(section)

    def _build_txt(self, endian: str) -> bytes:
        count = max(self.texts.keys(), default=-1) + 1
        if count == 0:
            return struct.pack(f"{endian}I", 0)
        if self.encoding == "utf-16":
            encoding = "utf-16-be" if self.big_endian else "utf-16-le"
            null = b"\x00\x00"
        elif self.encoding == "utf-32":
            encoding = "utf-32-be" if self.big_endian else "utf-32-le"
            null = b"\x00\x00\x00\x00"
        else:
            encoding = "utf-8"
            null = b"\x00"

        header_size = 4 + 4 * count
        section = bytearray(header_size)
        struct.pack_into(f"{endian}I", section, 0, count)
        body = bytearray()
        order = self.text_order or list(range(count))
        for index in range(count):
            struct.pack_into(f"{endian}I", section, 4 + index * 4, header_size + len(body))
            text_id = order[index] if index < len(order) else index
            raw = self.text_raw.get(text_id)
            if raw is None:
                text = self.texts.get(text_id, "")
                body.extend(text.encode(encoding))
                body.extend(null)
            else:
                body.extend(self._convert_text_blob(raw))
        return bytes(section + body)

    def _convert_text_blob(self, data: bytes) -> bytes:
        if self.encoding != "utf-16":
            if self.encoding == "utf-32":
                return data.decode(
                    "utf-32-be" if self.source_big_endian else "utf-32-le"
                ).encode("utf-32-be" if self.big_endian else "utf-32-le")
            return data

        src_endian = "big" if self.source_big_endian else "little"
        dst_endian = "big" if self.big_endian else "little"
        out = bytearray()
        pos = 0
        size = len(data)

        def read_u16(offset: int) -> int:
            return int.from_bytes(data[offset : offset + 2], src_endian)

        def write_u16(value: int) -> None:
            out.extend(value.to_bytes(2, dst_endian))

        while pos + 2 <= size:
            value = read_u16(pos)
            pos += 2
            write_u16(value)

            if value == 0x000E:
                if pos + 6 > size:
                    out.extend(data[pos:])
                    break
                group = read_u16(pos)
                write_u16(group)
                pos += 2
                type_id = read_u16(pos)
                write_u16(type_id)
                pos += 2
                arg_size = read_u16(pos)
                write_u16(arg_size)
                pos += 2
                if pos + arg_size > size:
                    out.extend(data[pos:])
                    break

                arg_data = data[pos : pos + arg_size]
                pos += arg_size

                if group == 1 and type_id in {0, 1, 3} and arg_size == 4:
                    # textSpeed, delay / autoAdvance stores one raw float32 payload.
                    out.extend(int.from_bytes(arg_data, src_endian).to_bytes(4, dst_endian))
                elif group == 1 and type_id in {4, 5, 6} and arg_size >= 2:
                    # choice dialogs:
                    # label indices are normal u16 fields, but the trailing
                    # selected/cancel byte pair is packed raw into one code unit.
                    for inner in range(0, arg_size - 2, 2):
                        chunk = arg_data[inner : inner + 2]
                        out.extend(int.from_bytes(chunk, src_endian).to_bytes(2, dst_endian))
                    out.extend(arg_data[-2:])
                elif group == 1 and type_id == 10 and arg_size >= 4:
                    # singleChoice:
                    # label is u16, trailing confirmed flag is packed as raw
                    # byte + padding.
                    out.extend(int.from_bytes(arg_data[:2], src_endian).to_bytes(2, dst_endian))
                    out.extend(arg_data[2:])
                elif group == 1 and type_id in {8, 9} and arg_size >= 2:
                    # choiceByFlags and fiveFlags:
                    # most payload fields behave like normal u16 / UTF-16
                    # pieces, but the final cancel field must keep its raw
                    # byte order to match the game files.
                    for inner in range(0, arg_size - 2, 2):
                        chunk = arg_data[inner : inner + 2]
                        if len(chunk) < 2:
                            out.extend(chunk)
                        else:
                            out.extend(int.from_bytes(chunk, src_endian).to_bytes(2, dst_endian))
                    out.extend(arg_data[-2:])
                elif group == 1 and type_id == 7 and arg_size == 2:
                    # BOTW icon tag stores a single byte plus 0xCD padding.
                    # The payload bytes stay in the same order between platforms.
                    out.extend(arg_data)
                elif group == 3 and type_id == 1 and arg_size == 2:
                    # setEmotion uses opaque byte data plus padding.
                    out.extend(arg_data)
                elif group == 4 and type_id == 1 and arg_size == 2:
                    # setEmotion2 uses opaque byte data plus padding.
                    out.extend(arg_data)
                elif group == 201 and type_id == 0 and arg_size == 4:
                    # wordInfo stores four packed bytes, not two independent
                    # u16 values.
                    out.extend(arg_data)
                else:
                    for inner in range(0, arg_size, 2):
                        chunk = arg_data[inner : inner + 2]
                        if len(chunk) < 2:
                            out.extend(chunk)
                        else:
                            out.extend(int.from_bytes(chunk, src_endian).to_bytes(2, dst_endian))
            elif value == 0x000F:
                if pos + 4 > size:
                    out.extend(data[pos:])
                    break
                group = read_u16(pos)
                write_u16(group)
                pos += 2
                type_id = read_u16(pos)
                write_u16(type_id)
                pos += 2

        if pos < size:
            out.extend(data[pos:])
        return bytes(out)


def convert_msbt(data: bytes, to_big_endian: bool) -> bytes:
    msbt = _MsbtFile.from_bytes(data)
    if msbt.big_endian == to_big_endian:
        return data
    msbt.big_endian = to_big_endian
    return msbt.to_bytes()


def convert_msbt_to_little_endian(data: bytes) -> bytes:
    return convert_msbt(data, to_big_endian=False)


def convert_msbt_to_big_endian(data: bytes) -> bytes:
    return convert_msbt(data, to_big_endian=True)
