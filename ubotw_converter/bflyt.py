from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

WIIU_VERSION = 0x05020000
SWITCH_VERSION = 0x08000000

_ASCII_IDENTIFIER_EXTRA = frozenset(b"_.-/")
_ASCII_IDENTIFIER_INITIAL_EXTRA = frozenset(b"_/")


def _align(value: int, alignment: int = 4) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def _pad(data: bytes, size: int) -> bytes:
    return data.ljust(size, b"\x00")


def _read_u16(data: bytes, offset: int, endian: str) -> int:
    return int.from_bytes(data[offset : offset + 2], endian)


def _read_u32(data: bytes, offset: int, endian: str) -> int:
    return int.from_bytes(data[offset : offset + 4], endian)


def _read_f32(data: bytes, offset: int, endian: str) -> float:
    return struct.unpack(">f" if endian == "big" else "<f", data[offset : offset + 4])[0]


def _write_u16(value: int, endian: str) -> bytes:
    return value.to_bytes(2, endian)


def _write_u32(value: int, endian: str) -> bytes:
    return value.to_bytes(4, endian)


def _write_f32(value: float, endian: str) -> bytes:
    return struct.pack(">f" if endian == "big" else "<f", value)


def _swap_u16(data: bytes, offset: int, src_endian: str, dst_endian: str) -> bytes:
    return _write_u16(_read_u16(data, offset, src_endian), dst_endian)


def _swap_u32(data: bytes, offset: int, src_endian: str, dst_endian: str) -> bytes:
    return _write_u32(_read_u32(data, offset, src_endian), dst_endian)


def _swap_float32(data: bytes, offset: int, src_endian: str, dst_endian: str) -> bytes:
    value = data[offset : offset + 4]
    return value if src_endian == dst_endian else value[::-1]


def _read_cstring(data: bytes, offset: int) -> bytes:
    end = data.find(b"\x00", offset)
    return data[offset:] if end == -1 else data[offset:end]


def _read_fixed_cstring(data: bytes) -> bytes:
    return data.split(b"\x00", 1)[0]


def _convert_utf16_text(text: bytes, src_endian: str, dst_endian: str) -> bytes:
    codec_map = {"big": "utf-16-be", "little": "utf-16-le"}
    decoded = text.decode(codec_map[src_endian])
    return decoded.encode(codec_map[dst_endian])


def _is_ascii_alpha(byte: int) -> bool:
    return 0x41 <= byte <= 0x5A or 0x61 <= byte <= 0x7A


def _is_ascii_digit(byte: int) -> bool:
    return 0x30 <= byte <= 0x39


def _is_ascii_identifier_initial(byte: int) -> bool:
    return _is_ascii_alpha(byte) or _is_ascii_digit(byte) or byte in _ASCII_IDENTIFIER_INITIAL_EXTRA


def _is_ascii_identifier_body(byte: int) -> bool:
    return _is_ascii_alpha(byte) or _is_ascii_digit(byte) or byte in _ASCII_IDENTIFIER_EXTRA


def _looks_like_ascii_identifier_blob(data: bytes) -> bool:
    if not data:
        return False
    end = data.find(b"\x00")
    token = data if end == -1 else data[:end]
    if not token:
        return False
    first = token[0]
    if not _is_ascii_identifier_initial(first):
        return False
    for byte in token:
        if not _is_ascii_identifier_body(byte):
            return False
    return True


@dataclass
class Section:
    tag: str
    data: bytes


@dataclass
class BflytHeader:
    bom: bytes
    header_size: int
    version: int
    file_size: int
    section_count: int
    trailing_header_bytes: bytes


@dataclass
class BflytDocument:
    header: BflytHeader
    sections: list[Section]


@dataclass
class ResourceEntry:
    name: bytes


@dataclass
class ResourceTable:
    entries: list[ResourceEntry]


@dataclass
class MaterialEntry:
    name: bytes
    header_20: bytes
    color_1: bytes
    color_2: bytes
    memory: int
    original_memory: int
    texture_maps: bytes
    texture_srt: bytes
    texture_coord: bytes
    texture_extensions: bytes
    remainder: bytes


@dataclass
class MaterialTable:
    materials: list[MaterialEntry]


@dataclass
class Txt1Entry:
    prefix_8_2c: bytes
    float_2c: bytes
    float_30: bytes
    prefix_34_40: bytes
    float_40: bytes
    float_44: bytes
    float_48: bytes
    float_4c: bytes
    float_50: bytes
    u16_block_54_5c: bytes
    bytes_5c_60: bytes
    u32_60: int
    text_offset: int
    bytes_68_70: bytes
    float_70: bytes
    float_74: bytes
    float_78: bytes
    float_7c: bytes
    label_offset: int
    float_84: bytes
    float_88: bytes
    float_8c: bytes
    float_90: bytes
    bytes_94_a0: bytes
    a0_value: int
    raw_payload: bytes


@dataclass
class Wnd1Entry:
    bytes_54_5c: bytes
    u16_block_5c_64: bytes
    bytes_64_68: bytes
    u32_68: int
    u32_6c: int
    bytes_70_80: bytes
    u16_80: int
    bytes_82_84: bytes
    trailing_words: bytes


@dataclass
class Usd1ValueEntry:
    first_u32: int
    second_u32: int
    value_u16: int
    suffix: bytes


@dataclass
class Usd1Table:
    count_padding: bytes
    entries: list[Usd1ValueEntry]
    tail: bytes


@dataclass
class Prt1Entry:
    prefix_8_54: bytes
    property_count: int
    float_58: bytes
    float_5c: bytes
    tail: bytes


@dataclass
class Grp1Entry:
    name_block: bytes
    pane_count: int
    panes_blob: bytes


@dataclass
class Cnt1Entry:
    header_words: bytes
    tail: bytes


def convert_bflyt(file: Path) -> None:
    data = file.read_bytes()
    if data[:4] != b"FLYT":
        raise ValueError(f"{file.name} is not a BFLYT file")

    document, src_endian = _parse_bflyt(data)
    dst_endian = "little"
    if src_endian == dst_endian:
        return

    converted_sections = [_convert_section_via_model(section, src_endian, dst_endian) for section in document.sections]
    converted_document = BflytDocument(
        header=BflytHeader(
            bom=b"\xFF\xFE",
            header_size=document.header.header_size,
            version=SWITCH_VERSION,
            file_size=0,
            section_count=len(converted_sections),
            trailing_header_bytes=b"\x00\x00",
        ),
        sections=converted_sections,
    )
    file.write_bytes(_serialize_bflyt(converted_document, dst_endian))


def dump_bflyt_json(source: Path, destination: Path) -> None:
    data = source.read_bytes()
    document, endian = _parse_bflyt(data)
    payload = {
        "format": "bflyt-json-v1",
        "endian": endian,
        "header": {
            "bom": document.header.bom.hex(),
            "header_size": document.header.header_size,
            "version": document.header.version,
            "file_size": document.header.file_size,
            "section_count": document.header.section_count,
            "trailing_header_bytes_hex": document.header.trailing_header_bytes.hex(),
        },
        "sections": [_section_to_json_dict(section, endian) for section in document.sections],
    }
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_bflyt_from_json(source: Path, destination: Path) -> None:
    payload = json.loads(source.read_text(encoding="utf-8"))
    endian = payload["endian"]
    bom = b"\xFE\xFF" if endian == "big" else b"\xFF\xFE"
    try:
        sections = [_section_from_json_dict(section_payload, endian) for section_payload in payload["sections"]]
    except ValueError as exc:
        raise ValueError(f"{source}: {exc}") from exc
    document = BflytDocument(
        header=BflytHeader(
            bom=bom,
            header_size=payload["header"].get("header_size", 0x14),
            version=payload["header"]["version"],
            file_size=0,
            section_count=len(sections),
            trailing_header_bytes=_bytes_from_json(str(payload["header"].get("trailing_header_bytes_hex", "0000"))),
        ),
        sections=sections,
    )
    destination.write_bytes(_serialize_bflyt(document, endian))


def _parse_bflyt(data: bytes) -> tuple[BflytDocument, str]:
    endian = "big" if data[4:6] == b"\xFE\xFF" else "little"
    header = BflytHeader(
        bom=data[4:6],
        header_size=_read_u16(data, 6, endian),
        version=_read_u32(data, 8, endian),
        file_size=_read_u32(data, 12, endian),
        section_count=_read_u16(data, 16, endian),
        trailing_header_bytes=data[18:20],
    )
    return BflytDocument(header=header, sections=_parse_sections(data, endian)), endian


def _serialize_bflyt(document: BflytDocument, endian: str) -> bytes:
    body = b"".join(section.data for section in document.sections)
    header = bytearray()
    header += b"FLYT"
    header += document.header.bom
    header += _write_u16(document.header.header_size, endian)
    header += _write_u32(document.header.version, endian)
    header += _write_u32(document.header.header_size + len(body), endian)
    header += _write_u16(document.header.section_count, endian)
    header += document.header.trailing_header_bytes
    return bytes(header) + body


def _bytes_to_json(data: bytes) -> str:
    return data.hex()


def _bytes_from_json(data: str) -> bytes:
    return bytes.fromhex(data)


def _bytes_from_json_field(data: object, field_name: str, expected_len: int | None = None) -> bytes:
    if not isinstance(data, str):
        raise ValueError(f"{field_name} must be a hex string")
    if len(data) % 2 != 0:
        raise ValueError(f"{field_name} must have an even number of hex characters, got {len(data)}")
    try:
        value = bytes.fromhex(data)
    except ValueError as exc:
        raise ValueError(f"{field_name} is not valid hex: {data!r}") from exc
    if expected_len is not None and len(value) != expected_len:
        raise ValueError(
            f"{field_name} must be exactly {expected_len} bytes ({expected_len * 2} hex chars), got {len(value)}"
        )
    return value


def _int_from_json_field(data: object, field_name: str, min_value: int = 0, max_value: int | None = None) -> int:
    if not isinstance(data, int):
        raise ValueError(f"{field_name} must be an integer")
    if data < min_value:
        raise ValueError(f"{field_name} must be >= {min_value}, got {data}")
    if max_value is not None and data > max_value:
        raise ValueError(f"{field_name} must be <= {max_value}, got {data}")
    return data


def _name_bytes_to_json(data: bytes) -> str:
    return data.decode("utf-8", errors="surrogateescape")


def _name_bytes_from_json(data: str) -> bytes:
    return data.encode("utf-8", errors="surrogateescape")


def _pane_summary_to_json_dict(data: bytes, endian: str) -> dict[str, object]:
    flags = data[8:12]
    name = _read_fixed_cstring(data[12:36])
    user_info = _read_fixed_cstring(data[36:44])
    return {
        "pane_flags_hex": _bytes_to_json(flags),
        "visible": bool(flags[0] & 0x01),
        "influence_alpha_to_children": bool(flags[0] & 0x02),
        "alpha": flags[2],
        "name": _name_bytes_to_json(name),
        "user_info": _name_bytes_to_json(user_info),
        "translate_x": _read_f32(data, 44, endian),
        "translate_y": _read_f32(data, 48, endian),
        "packed_transform_34_3c_hex": _bytes_to_json(data[52:60]),
        "extra_float_3c": _read_f32(data, 60, endian),
        "extra_float_40": _read_f32(data, 64, endian),
        "scale_x": _read_f32(data, 68, endian),
        "scale_y": _read_f32(data, 72, endian),
        "size_x": _read_f32(data, 76, endian),
        "size_y": _read_f32(data, 80, endian),
    }


def _lyt1_summary_to_json_dict(data: bytes, endian: str) -> dict[str, object]:
    return {
        "layout_flags_hex": _bytes_to_json(data[8:12]),
        "canvas_width": _read_f32(data, 12, endian),
        "canvas_height": _read_f32(data, 16, endian),
        "extra_float_14": _read_f32(data, 20, endian),
        "extra_float_18": _read_f32(data, 24, endian),
        "name": _name_bytes_to_json(_read_fixed_cstring(data[28:])),
    }


def _parse_sections(data: bytes, endian: str) -> list[Section]:
    offset = _read_u16(data, 6, endian)
    count = _read_u16(data, 16, endian)
    sections: list[Section] = []
    for _ in range(count):
        tag = data[offset : offset + 4].decode("ascii")
        size = _read_u32(data, offset + 4, endian)
        sections.append(Section(tag, data[offset : offset + size]))
        offset += size
    return sections


def _section_to_json_dict(section: Section, endian: str) -> dict[str, object]:
    payload: dict[str, object] = {"tag": section.tag}
    if section.tag == "lyt1":
        payload["parsed"] = _lyt1_summary_to_json_dict(section.data, endian)
    elif section.tag in {"pan1", "bnd1", "pic1", "wnd1"}:
        parsed = _pane_summary_to_json_dict(section.data, endian)
        parsed["section_type"] = section.tag
        if section.tag == "pic1":
            parsed["bytes_54_64_hex"] = _bytes_to_json(section.data[0x54:0x64])
            parsed["u16_64"] = _read_u16(section.data, 0x64, endian)
            parsed["bytes_66_68_hex"] = _bytes_to_json(section.data[0x66:0x68])
            parsed["tail_hex"] = _bytes_to_json(section.data[0x68:])
        if section.tag == "wnd1":
            wnd = _parse_wnd1_entry(section.data, endian)
            parsed["bytes_54_5c_hex"] = _bytes_to_json(wnd.bytes_54_5c)
            parsed["u16_block_5c_64_hex"] = _bytes_to_json(wnd.u16_block_5c_64)
            parsed["bytes_64_68_hex"] = _bytes_to_json(wnd.bytes_64_68)
            parsed["u32_68"] = wnd.u32_68
            parsed["u32_6c"] = wnd.u32_6c
            parsed["bytes_70_80_hex"] = _bytes_to_json(wnd.bytes_70_80)
            parsed["u16_80"] = wnd.u16_80
            parsed["bytes_82_84_hex"] = _bytes_to_json(wnd.bytes_82_84)
            parsed["trailing_words_hex"] = _bytes_to_json(wnd.trailing_words)
        payload["parsed"] = parsed
    elif section.tag in {"txl1", "fnl1"}:
        table = _parse_resource_table(section.data, endian, False)
        payload["parsed"] = {
            "entries": [{"name": _name_bytes_to_json(entry.name)} for entry in table.entries]
        }
    elif section.tag == "mat1":
        table = _parse_material_table(section.data, endian)
        payload["parsed"] = {
            "materials": [
                ({
                    "name": _name_bytes_to_json(material.name),
                    "color_1_hex": _bytes_to_json(material.color_1),
                    "color_2_hex": _bytes_to_json(material.color_2),
                    "memory": material.memory,
                    "memory_decoded": _material_counts(material.memory),
                    "texture_maps_hex": _bytes_to_json(material.texture_maps),
                    "texture_srt_hex": _bytes_to_json(material.texture_srt),
                    "texture_coord_hex": _bytes_to_json(material.texture_coord),
                    "texture_extensions_hex": _bytes_to_json(material.texture_extensions),
                    "remainder_hex": _bytes_to_json(material.remainder),
                } | (
                    {"header_20_hex": _bytes_to_json(material.header_20)}
                    if material.header_20
                    else {}
                ))
                for material in table.materials
            ]
        }
    elif section.tag == "txt1":
        entry = _parse_txt1_entry(section.data, endian)
        payload["parsed"] = {
            "text_offset": entry.text_offset,
            "label_offset": entry.label_offset,
            "a0_value": entry.a0_value,
            "prefix_8_2c_hex": _bytes_to_json(entry.prefix_8_2c),
            "float_2c_hex": _bytes_to_json(entry.float_2c),
            "float_30_hex": _bytes_to_json(entry.float_30),
            "prefix_34_40_hex": _bytes_to_json(entry.prefix_34_40),
            "float_40_hex": _bytes_to_json(entry.float_40),
            "float_44_hex": _bytes_to_json(entry.float_44),
            "float_48_hex": _bytes_to_json(entry.float_48),
            "float_4c_hex": _bytes_to_json(entry.float_4c),
            "float_50_hex": _bytes_to_json(entry.float_50),
            "u16_block_54_5c_hex": _bytes_to_json(entry.u16_block_54_5c),
            "bytes_5c_60_hex": _bytes_to_json(entry.bytes_5c_60),
            "u32_60": entry.u32_60,
            "bytes_68_70_hex": _bytes_to_json(entry.bytes_68_70),
            "float_70_hex": _bytes_to_json(entry.float_70),
            "float_74_hex": _bytes_to_json(entry.float_74),
            "float_78_hex": _bytes_to_json(entry.float_78),
            "float_7c_hex": _bytes_to_json(entry.float_7c),
            "float_84_hex": _bytes_to_json(entry.float_84),
            "float_88_hex": _bytes_to_json(entry.float_88),
            "float_8c_hex": _bytes_to_json(entry.float_8c),
            "float_90_hex": _bytes_to_json(entry.float_90),
            "bytes_94_a0_hex": _bytes_to_json(entry.bytes_94_a0),
            "raw_payload_hex": _bytes_to_json(entry.raw_payload),
        }
    elif section.tag == "usd1":
        table = _parse_usd1_table(section.data, endian)
        payload["parsed"] = {
            "entry_count": len(table.entries),
            "count_padding_hex": _bytes_to_json(table.count_padding),
            "entries": [
                {
                    "first_u32": entry.first_u32,
                    "second_u32": entry.second_u32,
                    "value_u16": entry.value_u16,
                    "suffix_hex": _bytes_to_json(entry.suffix),
                }
                for entry in table.entries
            ],
            "tail_hex": _bytes_to_json(table.tail),
        }
    elif section.tag == "prt1":
        entry = _parse_prt1_entry(section.data, endian)
        parsed = _pane_summary_to_json_dict(section.data, endian)
        parsed.update({
            "property_count": entry.property_count,
            "prefix_8_54_hex": _bytes_to_json(entry.prefix_8_54),
            "float_58_hex": _bytes_to_json(entry.float_58),
            "float_5c_hex": _bytes_to_json(entry.float_5c),
            "tail_hex": _bytes_to_json(entry.tail),
        })
        tail_name = _read_fixed_cstring(entry.tail)
        if tail_name:
            parsed["part_name"] = _name_bytes_to_json(tail_name)
        payload["parsed"] = parsed
    elif section.tag == "grp1":
        entry = _parse_grp1_entry(section.data, endian)
        payload["parsed"] = {
            "group_name": _name_bytes_to_json(_read_fixed_cstring(entry.name_block)),
            "pane_count": entry.pane_count,
            "name_block_hex": _bytes_to_json(entry.name_block),
            "panes_blob_hex": _bytes_to_json(entry.panes_blob),
        }
    elif section.tag == "cnt1":
        entry = _parse_cnt1_entry(section.data)
        payload["parsed"] = {
            "header_words_hex": _bytes_to_json(entry.header_words),
            "tail_hex": _bytes_to_json(entry.tail),
        }
    elif section.tag in {"pas1", "pae1", "grs1", "gre1"}:
        payload["parsed"] = {"section_type": section.tag}
    else:
        payload["raw_hex"] = _bytes_to_json(section.data)
    return payload


def _section_from_json_dict(payload: dict[str, object], endian: str) -> Section:
    tag = str(payload["tag"])
    parsed = payload.get("parsed")
    raw_hex = payload.get("raw_hex")
    if tag == "lyt1" and isinstance(parsed, dict):
        body = bytearray()
        body += _bytes_from_json(str(parsed["layout_flags_hex"]))
        body += _write_f32(float(parsed["canvas_width"]), endian)
        body += _write_f32(float(parsed["canvas_height"]), endian)
        body += _write_f32(float(parsed["extra_float_14"]), endian)
        body += _write_f32(float(parsed["extra_float_18"]), endian)
        name_blob = _name_bytes_from_json(parsed["name"]) + b"\x00"
        body += name_blob + (b"\x00" * ((_align(len(name_blob), 4)) - len(name_blob)))
        return Section(tag, _wrap_section(tag, bytes(body), endian))
    if tag in {"pan1", "bnd1"} and isinstance(parsed, dict):
        body = bytearray()
        body += _bytes_from_json(str(parsed["pane_flags_hex"]))
        body += _pad(_name_bytes_from_json(parsed["name"]), 24)
        body += _pad(_name_bytes_from_json(parsed["user_info"]), 8)
        body += _write_f32(float(parsed["translate_x"]), endian)
        body += _write_f32(float(parsed["translate_y"]), endian)
        body += _bytes_from_json(str(parsed["packed_transform_34_3c_hex"]))
        body += _write_f32(float(parsed["extra_float_3c"]), endian)
        body += _write_f32(float(parsed["extra_float_40"]), endian)
        body += _write_f32(float(parsed["scale_x"]), endian)
        body += _write_f32(float(parsed["scale_y"]), endian)
        body += _write_f32(float(parsed["size_x"]), endian)
        body += _write_f32(float(parsed["size_y"]), endian)
        return Section(tag, _wrap_section(tag, bytes(body), endian))
    if tag == "pic1" and isinstance(parsed, dict):
        body = bytearray()
        body += _bytes_from_json(str(parsed["pane_flags_hex"]))
        body += _pad(_name_bytes_from_json(parsed["name"]), 24)
        body += _pad(_name_bytes_from_json(parsed["user_info"]), 8)
        body += _write_f32(float(parsed["translate_x"]), endian)
        body += _write_f32(float(parsed["translate_y"]), endian)
        body += _bytes_from_json(str(parsed["packed_transform_34_3c_hex"]))
        body += _write_f32(float(parsed["extra_float_3c"]), endian)
        body += _write_f32(float(parsed["extra_float_40"]), endian)
        body += _write_f32(float(parsed["scale_x"]), endian)
        body += _write_f32(float(parsed["scale_y"]), endian)
        body += _write_f32(float(parsed["size_x"]), endian)
        body += _write_f32(float(parsed["size_y"]), endian)
        body += _bytes_from_json(str(parsed["bytes_54_64_hex"]))
        body += _write_u16(int(parsed["u16_64"]), endian)
        body += _bytes_from_json(str(parsed["bytes_66_68_hex"]))
        body += _bytes_from_json(str(parsed["tail_hex"]))
        return Section(tag, _wrap_section(tag, bytes(body), endian))
    if tag == "wnd1" and isinstance(parsed, dict):
        body = bytearray()
        body += _bytes_from_json(str(parsed["pane_flags_hex"]))
        body += _pad(_name_bytes_from_json(parsed["name"]), 24)
        body += _pad(_name_bytes_from_json(parsed["user_info"]), 8)
        body += _write_f32(float(parsed["translate_x"]), endian)
        body += _write_f32(float(parsed["translate_y"]), endian)
        body += _bytes_from_json(str(parsed["packed_transform_34_3c_hex"]))
        body += _write_f32(float(parsed["extra_float_3c"]), endian)
        body += _write_f32(float(parsed["extra_float_40"]), endian)
        body += _write_f32(float(parsed["scale_x"]), endian)
        body += _write_f32(float(parsed["scale_y"]), endian)
        body += _write_f32(float(parsed["size_x"]), endian)
        body += _write_f32(float(parsed["size_y"]), endian)
        body += _bytes_from_json(str(parsed["bytes_54_5c_hex"]))
        body += _bytes_from_json(str(parsed["u16_block_5c_64_hex"]))
        body += _bytes_from_json(str(parsed["bytes_64_68_hex"]))
        body += _write_u32(int(parsed["u32_68"]), endian)
        body += _write_u32(int(parsed["u32_6c"]), endian)
        body += _bytes_from_json(str(parsed["bytes_70_80_hex"]))
        body += _write_u16(int(parsed["u16_80"]), endian)
        body += _bytes_from_json(str(parsed["bytes_82_84_hex"]))
        body += _bytes_from_json(str(parsed["trailing_words_hex"]))
        return Section(tag, _wrap_section(tag, bytes(body), endian))
    if tag == "prt1" and isinstance(parsed, dict):
        body = bytearray()
        body += _bytes_from_json(str(parsed["prefix_8_54_hex"]))
        body += _write_u32(int(parsed["property_count"]), endian)
        body += _bytes_from_json(str(parsed["float_58_hex"]))
        body += _bytes_from_json(str(parsed["float_5c_hex"]))
        body += _bytes_from_json(str(parsed["tail_hex"]))
        return Section(tag, _wrap_section(tag, bytes(body), endian))
    if tag == "txt1" and isinstance(parsed, dict):
        body = bytearray()
        body += _bytes_from_json(str(parsed["prefix_8_2c_hex"]))
        body += _bytes_from_json(str(parsed["float_2c_hex"]))
        body += _bytes_from_json(str(parsed["float_30_hex"]))
        body += _bytes_from_json(str(parsed["prefix_34_40_hex"]))
        body += _bytes_from_json(str(parsed["float_40_hex"]))
        body += _bytes_from_json(str(parsed["float_44_hex"]))
        body += _bytes_from_json(str(parsed["float_48_hex"]))
        body += _bytes_from_json(str(parsed["float_4c_hex"]))
        body += _bytes_from_json(str(parsed["float_50_hex"]))
        body += _bytes_from_json(str(parsed["u16_block_54_5c_hex"]))
        body += _bytes_from_json(str(parsed["bytes_5c_60_hex"]))
        body += _write_u32(int(parsed["u32_60"]), endian)
        body += _write_u32(int(parsed["text_offset"]), endian)
        body += _bytes_from_json(str(parsed["bytes_68_70_hex"]))
        body += _bytes_from_json(str(parsed["float_70_hex"]))
        body += _bytes_from_json(str(parsed["float_74_hex"]))
        body += _bytes_from_json(str(parsed["float_78_hex"]))
        body += _bytes_from_json(str(parsed["float_7c_hex"]))
        body += _write_u32(int(parsed["label_offset"]), endian)
        body += _bytes_from_json(str(parsed["float_84_hex"]))
        body += _bytes_from_json(str(parsed["float_88_hex"]))
        body += _bytes_from_json(str(parsed["float_8c_hex"]))
        body += _bytes_from_json(str(parsed["float_90_hex"]))
        body += _bytes_from_json(str(parsed["bytes_94_a0_hex"]))
        body += _write_u32(int(parsed["a0_value"]), endian)
        body += _bytes_from_json(str(parsed["raw_payload_hex"]))
        return Section(tag, _wrap_section(tag, bytes(body), endian))
    if tag == "grp1" and isinstance(parsed, dict):
        body = bytearray()
        body += _bytes_from_json(str(parsed["name_block_hex"]))
        body += _write_u16(int(parsed["pane_count"]), endian)
        body += _bytes_from_json(str(parsed["panes_blob_hex"]))
        return Section(tag, _wrap_section(tag, bytes(body), endian))
    if tag == "usd1" and isinstance(parsed, dict):
        body = bytearray()
        entries = parsed["entries"]
        entry_count = _int_from_json_field(parsed.get("entry_count", len(entries)), "usd1.entry_count", 0, 0xFFFF)
        if entry_count != len(entries):
            raise ValueError(f"usd1.entry_count must equal len(entries), got {entry_count} and {len(entries)}")
        body += _write_u16(entry_count, endian)
        body += _bytes_from_json_field(parsed.get("count_padding_hex", "0000"), "usd1.count_padding_hex", 2)
        for index, entry in enumerate(entries):
            body += _write_u32(
                _int_from_json_field(entry["first_u32"], f"usd1.entries[{index}].first_u32", 0, 0xFFFFFFFF),
                endian,
            )
            body += _write_u32(
                _int_from_json_field(entry["second_u32"], f"usd1.entries[{index}].second_u32", 0, 0xFFFFFFFF),
                endian,
            )
            body += _write_u16(
                _int_from_json_field(entry["value_u16"], f"usd1.entries[{index}].value_u16", 0, 0xFFFF),
                endian,
            )
            body += _bytes_from_json_field(entry["suffix_hex"], f"usd1.entries[{index}].suffix_hex", 2)
        body += _bytes_from_json_field(parsed["tail_hex"], "usd1.tail_hex")
        return Section(tag, _wrap_section(tag, bytes(body), endian))
    if tag == "cnt1" and isinstance(parsed, dict):
        body = bytearray()
        body += _bytes_from_json(str(parsed["header_words_hex"]))
        body += _bytes_from_json(str(parsed["tail_hex"]))
        return Section(tag, _wrap_section(tag, bytes(body), endian))
    if tag in {"pas1", "pae1", "grs1", "gre1"} and isinstance(parsed, dict):
        return Section(tag, _wrap_section(tag, b"", endian))
    if tag in {"txl1", "fnl1"} and isinstance(parsed, dict):
        table = ResourceTable(
            entries=[ResourceEntry(name=_name_bytes_from_json(entry["name"])) for entry in parsed["entries"]]
        )
        return Section(tag, _wrap_section(tag, _serialize_resource_table(table, endian), endian))
    if tag == "mat1" and isinstance(parsed, dict):
        table = MaterialTable(
            materials=[
                MaterialEntry(
                    name=_name_bytes_from_json(material["name"]),
                    header_20=(
                        _bytes_from_json_field(
                            material["header_20_hex"], f"mat1.materials[{index}].header_20_hex", 4
                        )
                        if "header_20_hex" in material
                        else b""
                    ),
                    color_1=_bytes_from_json_field(
                        material["color_1_hex"], f"mat1.materials[{index}].color_1_hex", 4
                    ),
                    color_2=_bytes_from_json_field(
                        material["color_2_hex"], f"mat1.materials[{index}].color_2_hex", 4
                    ),
                    memory=int(material["memory"]),
                    original_memory=int(material.get("original_memory", material["memory"])),
                    texture_maps=_bytes_from_json_field(
                        material["texture_maps_hex"], f"mat1.materials[{index}].texture_maps_hex"
                    ),
                    texture_srt=_bytes_from_json_field(
                        material["texture_srt_hex"], f"mat1.materials[{index}].texture_srt_hex"
                    ),
                    texture_coord=_bytes_from_json_field(
                        material["texture_coord_hex"], f"mat1.materials[{index}].texture_coord_hex"
                    ),
                    texture_extensions=_bytes_from_json_field(
                        material["texture_extensions_hex"], f"mat1.materials[{index}].texture_extensions_hex"
                    ),
                    remainder=_bytes_from_json_field(
                        material["remainder_hex"], f"mat1.materials[{index}].remainder_hex"
                    ),
                )
                for index, material in enumerate(parsed["materials"])
            ]
        )
        return Section(tag, _wrap_section(tag, _serialize_material_table_json(table, endian), endian))
    if raw_hex:
        return Section(tag, _bytes_from_json(str(raw_hex)))
    raise ValueError(f"JSON section {tag} is missing raw_hex and no parsed serializer is available")


def _convert_cnt1_parsed(parsed: dict[str, object], src_endian: str, dst_endian: str) -> dict[str, object]:
    header_words = _bytes_from_json_field(parsed["header_words_hex"], "cnt1.header_words_hex")
    tail = _bytes_from_json_field(parsed["tail_hex"], "cnt1.tail_hex")

    converted_header = bytearray()
    if len(header_words) >= 20:
        converted_header += _swap_u32(header_words, 0, src_endian, dst_endian)
        converted_header += _swap_u32(header_words, 4, src_endian, dst_endian)
        converted_header += _swap_u16(header_words, 8, src_endian, dst_endian)
        converted_header += _swap_u16(header_words, 10, src_endian, dst_endian)
        converted_header += _swap_u32(header_words, 12, src_endian, dst_endian)
        converted_header += _swap_u32(header_words, 16, src_endian, dst_endian)
        converted_header += header_words[20:]
    else:
        for offset in range(0, len(header_words), 4):
            if offset + 4 <= len(header_words):
                converted_header += _swap_u32(header_words, offset, src_endian, dst_endian)
            else:
                converted_header += header_words[offset:]

    converted_tail = bytearray()
    for offset in range(0, len(tail), 4):
        if offset + 4 <= len(tail):
            word = tail[offset : offset + 4]
            if word[:3] == b"\x00\x00\x00" and word != b"\x00\x00\x00\x00":
                converted_tail += _swap_u32(tail, offset, src_endian, dst_endian)
            else:
                converted_tail += word
        else:
            converted_tail += tail[offset:]

    return {
        "header_words_hex": _bytes_to_json(bytes(converted_header)),
        "tail_hex": _bytes_to_json(bytes(converted_tail)),
    }


def _convert_usd1_tail_via_model(tail: bytes, src_endian: str, dst_endian: str) -> bytes:
    payload = bytearray()
    cursor = 0
    while len(tail) - cursor >= 4:
        current_tail = tail[cursor:]
        current_end = current_tail.find(b"\x00")
        current_token = current_tail if current_end == -1 else current_tail[:current_end]
        next_is_identifier = len(tail) - cursor >= 8 and _looks_like_ascii_identifier_blob(tail[cursor + 4 :])
        current_word = tail[cursor : cursor + 4]
        next_tail = tail[cursor + 4 :] if len(tail) - cursor >= 8 else b""
        next_end = next_tail.find(b"\x00") if next_tail else -1
        next_token = next_tail if next_end == -1 else next_tail[:next_end]
        next_is_short_prefix = (
            len(next_token) <= 2
            and len(tail) - cursor >= 12
            and _looks_like_ascii_identifier_blob(tail[cursor + 8 :])
        )
        if (
            next_is_identifier
            and current_word[2:4] == b"\x00\x00"
            and 0x20 <= current_word[0] <= 0x7E
            and 0x20 <= current_word[1] <= 0x7E
        ):
            payload += _swap_u32(tail, cursor, src_endian, dst_endian)
            cursor += 4
            break
        if _looks_like_ascii_identifier_blob(current_tail):
            if not (len(current_token) <= 2 and current_word[2:4] == b"\x00\x00" and next_is_identifier):
                break
        if next_is_identifier:
            payload += _swap_u32(tail, cursor, src_endian, dst_endian)
            cursor += 4
            if next_is_short_prefix:
                continue
            break
        payload += _swap_u32(tail, cursor, src_endian, dst_endian)
        cursor += 4
    payload += tail[cursor:]
    return bytes(payload)


def _convert_usd1_parsed(parsed: dict[str, object], src_endian: str, dst_endian: str) -> dict[str, object]:
    tail = _bytes_from_json_field(parsed["tail_hex"], "usd1.tail_hex")
    return {
        "entry_count": parsed["entry_count"],
        "count_padding_hex": "0000",
        "entries": parsed["entries"],
        "tail_hex": _bytes_to_json(_convert_usd1_tail_via_model(tail, src_endian, dst_endian)),
    }


def _convert_pic1_parsed(parsed: dict[str, object], src_endian: str, dst_endian: str) -> dict[str, object]:
    tail = _bytes_from_json_field(parsed["tail_hex"], "pic1.tail_hex")
    converted_tail = bytearray()
    for offset in range(0, len(tail), 4):
        if offset + 4 <= len(tail):
            converted_tail += _swap_float32(tail, offset, src_endian, dst_endian)
        else:
            converted_tail += tail[offset:]
    return {
        **parsed,
        "tail_hex": _bytes_to_json(bytes(converted_tail)),
    }


def _convert_wnd1_parsed(parsed: dict[str, object], src_endian: str, dst_endian: str) -> dict[str, object]:
    u16_block = _bytes_from_json_field(parsed["u16_block_5c_64_hex"], "wnd1.u16_block_5c_64_hex")
    trailing_words = _bytes_from_json_field(parsed["trailing_words_hex"], "wnd1.trailing_words_hex")

    converted_u16 = bytearray()
    for offset in range(0, len(u16_block), 2):
        if offset + 2 <= len(u16_block):
            converted_u16 += _swap_u16(u16_block, offset, src_endian, dst_endian)
        else:
            converted_u16 += u16_block[offset:]

    converted_trailing = bytearray()
    cursor = 0
    while cursor + 4 <= len(trailing_words):
        word = trailing_words[cursor : cursor + 4]
        if word[0] == 0x00 and word[1] != 0x00 and word[2:4] == b"\x00\x00":
            converted_trailing += _swap_u16(trailing_words, cursor, src_endian, dst_endian)
            converted_trailing += word[2:4]
        else:
            converted_trailing += _swap_u32(trailing_words, cursor, src_endian, dst_endian)
        cursor += 4
    converted_trailing += trailing_words[cursor:]

    return {
        **parsed,
        "u16_block_5c_64_hex": _bytes_to_json(bytes(converted_u16)),
        "trailing_words_hex": _bytes_to_json(bytes(converted_trailing)),
    }


def _convert_mat1_parsed(parsed: dict[str, object], src_endian: str, dst_endian: str) -> dict[str, object]:
    converted: list[dict[str, object]] = []
    for mat in parsed["materials"]:
        color_2 = _bytes_from_json(str(mat["color_2_hex"]))
        memory = int(mat["memory"])
        texture_maps = _bytes_from_json(str(mat["texture_maps_hex"]))
        texture_srt = _bytes_from_json(str(mat["texture_srt_hex"]))
        texture_coord = _bytes_from_json(str(mat["texture_coord_hex"]))
        texture_extensions = _bytes_from_json(str(mat["texture_extensions_hex"]))
        remainder = bytearray(_bytes_from_json(str(mat["remainder_hex"])))
        original_memory = memory

        if (
            memory == 0x15
            and texture_srt[:16] == b"\x00" * 16
            and texture_srt[16:20] != b"\x00" * 4
        ):
            memory |= 0x800

        for flag_offset in (0, 4):
            if (
                len(remainder) >= flag_offset + 4
                and remainder[flag_offset] == 0x09
                and remainder[flag_offset + 2 : flag_offset + 4] == b"\x00\x00"
            ):
                remainder[flag_offset] = 0x0B

        if memory & 0x8000 and len(remainder) == 20:
            swapped = bytearray(remainder[:8])
            swapped += _swap_u32(remainder, 8, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 12, src_endian, dst_endian)
            swapped += remainder[16:20]
            remainder = swapped
        elif original_memory == 0x846A and len(remainder) == 28:
            swapped = bytearray(remainder[:16])
            swapped += _swap_u32(remainder, 16, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 20, src_endian, dst_endian)
            swapped += remainder[24:28]
            remainder = swapped
        elif original_memory == 0x1006A and len(remainder) == 44:
            swapped = bytearray(remainder[:12])
            swapped += _swap_u32(remainder, 12, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 16, src_endian, dst_endian)
            swapped += remainder[20:32]
            swapped += _swap_u32(remainder, 32, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 36, src_endian, dst_endian)
            swapped += remainder[40:44]
            remainder = swapped
        elif original_memory == 0x1C4BF and len(remainder) == 84:
            swapped = bytearray(remainder[:12])
            if swapped[4] == 0x09 and swapped[6:8] == b"\x00\x00":
                swapped[4] = 0x0B
            swapped += remainder[12:16]
            swapped += _swap_u32(remainder, 16, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 20, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 24, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 28, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 32, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 36, src_endian, dst_endian)
            swapped += remainder[40:52]
            swapped += _swap_u32(remainder, 52, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 56, src_endian, dst_endian)
            swapped += remainder[60:72]
            swapped += _swap_u32(remainder, 72, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 76, src_endian, dst_endian)
            swapped += remainder[80:84]
            words = [_read_u32(remainder, offset, src_endian) for offset in range(0, len(remainder), 4)]
            if (
                words[-1] == 0
                and words[18] == 0x00030000
                and words[20] == 0x00030000
                and words[22] == 0x00040000
                and words[24] == 0x09010000
                and words[25] == 0x00010000
            ):
                swapped[-4:] = _write_u32(4, dst_endian)
            remainder = swapped
        elif original_memory == 0x184BF and len(remainder) == 72:
            swapped = bytearray(remainder[:12])
            swapped += remainder[12:20]
            swapped += _swap_u32(remainder, 20, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 24, src_endian, dst_endian)
            swapped += remainder[28:40]
            swapped += _swap_u32(remainder, 40, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 44, src_endian, dst_endian)
            swapped += remainder[48:60]
            swapped += _swap_u32(remainder, 60, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 64, src_endian, dst_endian)
            swapped += remainder[68:72]
            remainder = swapped
        elif original_memory == 0x194BF and len(remainder) == 76:
            swapped = bytearray(remainder[:24])
            swapped += _swap_u32(remainder, 24, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 28, src_endian, dst_endian)
            swapped += remainder[32:44]
            swapped += _swap_u32(remainder, 44, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 48, src_endian, dst_endian)
            swapped += remainder[52:64]
            swapped += _swap_u32(remainder, 64, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 68, src_endian, dst_endian)
            swapped += remainder[72:76]
            remainder = swapped
        elif original_memory == 0x806A and len(remainder) == 24:
            swapped = bytearray(remainder[:8])
            if remainder[4:8] == b"\x00" * 4 and remainder[12:20] == b"\x3F\x80\x00\x00\x3F\x80\x00\x00":
                swapped += _swap_u32(remainder, 8, src_endian, dst_endian)
                swapped += _swap_u32(remainder, 12, src_endian, dst_endian)
                swapped += _swap_u32(remainder, 16, src_endian, dst_endian)
                swapped += remainder[20:24]
            else:
                swapped = bytearray(remainder[:12])
                swapped += _swap_u32(remainder, 12, src_endian, dst_endian)
                swapped += _swap_u32(remainder, 16, src_endian, dst_endian)
                swapped += remainder[20:24]
            remainder = swapped
        elif original_memory == 0x80BF and len(remainder) == 28:
            swapped = bytearray(remainder[:12])
            for offset in range(12, len(remainder) - 4, 4):
                swapped += _swap_u32(remainder, offset, src_endian, dst_endian)
            swapped += remainder[-4:]
            remainder = swapped
        elif original_memory == 0x140BF and len(remainder) == 60:
            swapped = bytearray(remainder[:12])
            for offset in range(12, len(remainder) - 4, 4):
                swapped += _swap_u32(remainder, offset, src_endian, dst_endian)
            swapped += remainder[-4:]
            remainder = swapped
        elif original_memory == 0x1406A and len(remainder) == 56:
            swapped = bytearray(remainder[:8])
            for offset in range(8, len(remainder), 4):
                swapped += _swap_u32(remainder, offset, src_endian, dst_endian)
            remainder = swapped
        elif original_memory == 0xC06A and len(remainder) == 36:
            swapped = bytearray(remainder[:8])
            for offset in range(8, len(remainder), 4):
                swapped += _swap_u32(remainder, offset, src_endian, dst_endian)
            remainder = swapped
        elif original_memory in (0x406A, 0x4406A) and len(remainder) == 16:
            swapped = bytearray(remainder[:8])
            swapped += _swap_u32(remainder, 8, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 12, src_endian, dst_endian)
            remainder = swapped
        elif original_memory in (0xC2BF, 0xC0BF) and len(remainder) >= 16:
            swapped = bytearray(remainder[:12])
            for offset in range(12, len(remainder) - 4, 4):
                swapped += _swap_u32(remainder, offset, src_endian, dst_endian)
            swapped += remainder[-4:]
            remainder = swapped
        elif original_memory == 0x1146A and len(remainder) == 52:
            swapped = bytearray(remainder[:20])
            swapped += _swap_u32(remainder, 20, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 24, src_endian, dst_endian)
            swapped += remainder[28:40]
            swapped += _swap_u32(remainder, 40, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 44, src_endian, dst_endian)
            swapped += remainder[48:52]
            remainder = swapped
        elif original_memory == 0x100BF and len(remainder) == 48:
            swapped = bytearray(remainder[:16])
            swapped += _swap_u32(remainder, 16, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 20, src_endian, dst_endian)
            swapped += remainder[24:36]
            swapped += _swap_u32(remainder, 36, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 40, src_endian, dst_endian)
            swapped += remainder[44:48]
            remainder = swapped
        elif original_memory == 0x104BF and len(remainder) == 52:
            swapped = bytearray(remainder[:12])
            for offset in range(12, len(remainder) - 4, 4):
                swapped += _swap_u32(remainder, offset, src_endian, dst_endian)
            swapped += remainder[-4:]
            remainder = swapped
        elif original_memory == 0x114BF and len(remainder) == 56:
            swapped = bytearray(remainder[:24])
            swapped += _swap_u32(remainder, 24, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 28, src_endian, dst_endian)
            swapped += remainder[32:44]
            swapped += _swap_u32(remainder, 44, src_endian, dst_endian)
            swapped += _swap_u32(remainder, 48, src_endian, dst_endian)
            swapped += remainder[52:56]
            remainder = swapped
        elif len(remainder) >= 20:
            swapped = bytearray(remainder[:12])
            for offset in range(12, len(remainder), 4):
                if offset + 4 <= len(remainder):
                    swapped += _swap_u32(remainder, offset, src_endian, dst_endian)
                else:
                    swapped += remainder[offset:]
            remainder = swapped

        if (
            original_memory == 0x806A
            and color_2 == b"\xFF\xFF\xFF\xFF"
            and bytes(remainder).endswith(b"\x00\x00\x00\x00")
        ):
            head_word_0 = _read_u32(texture_maps, 0, src_endian) if len(texture_maps) >= 4 else None
            head_word_1 = _read_u32(texture_srt, 0, src_endian) if len(texture_srt) >= 4 else None
            head_word_1_hi = (head_word_1 >> 16) & 0xFFFF if head_word_1 is not None else None
            head_word_1_lo = head_word_1 & 0xFFFF if head_word_1 is not None else None
        else:
            head_word_0 = head_word_1_hi = head_word_1_lo = None

        if (
            original_memory == 0x806A
            and color_2 == b"\xFF\xFF\xFF\xFF"
            and head_word_0 in (0x00000000, 0x00020000)
            and head_word_1_hi in (0x0001, 0x0007)
            and head_word_1_lo == 0x0404
            and bytes(remainder).endswith(b"\x00\x00\x00\x00")
        ):
            memory = 0x1006A
            texture_coord = b"\x00\x03\x00\x00" + texture_coord[4:]
            remainder = bytearray(
                bytes(remainder)[:-4]
                + b"\x01\x00\x00\x00"
                + b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x80\x3F\x00\x00\x80\x3F\x00\x00\x00\x00"
            )

        counts = _material_counts(memory)
        texture_maps_out = _convert_texture_maps(texture_maps, counts["texture_maps"], src_endian, dst_endian)
        texture_srt_out = _convert_texture_srt(texture_srt, counts["texture_srt"], src_endian, dst_endian)
        texture_coord_out = _convert_texture_coord_gen(texture_coord, counts["texture_coord_gen"])
        header_20_src = _bytes_from_json(str(mat["header_20_hex"])) if "header_20_hex" in mat else b""
        header_20_out = header_20_src if header_20_src else (b"\x00\x02\x04\x08" if dst_endian == "little" else b"")

        entry_dict: dict[str, object] = {
            "name": mat["name"],
            "color_1_hex": mat["color_1_hex"],
            "color_2_hex": mat["color_2_hex"],
            "memory": memory,
            "memory_decoded": _material_counts(memory),
            "texture_maps_hex": _bytes_to_json(texture_maps_out),
            "texture_srt_hex": _bytes_to_json(texture_srt_out),
            "texture_coord_hex": _bytes_to_json(texture_coord_out),
            "texture_extensions_hex": _bytes_to_json(texture_extensions),
            "remainder_hex": _bytes_to_json(bytes(remainder)),
        }
        if header_20_out:
            entry_dict["header_20_hex"] = _bytes_to_json(header_20_out)
        converted.append(entry_dict)

    return {"materials": converted}


def _convert_txt1_parsed(parsed: dict[str, object], src_endian: str, dst_endian: str) -> dict[str, object]:
    def _swap_float_hex(hex_str: str) -> str:
        b = _bytes_from_json(hex_str)
        return _bytes_to_json(_swap_float32(b, 0, src_endian, dst_endian))

    def _swap_u16_block_hex(hex_str: str) -> str:
        b = _bytes_from_json(hex_str)
        result = bytearray()
        for offset in range(0, len(b), 2):
            if offset + 2 <= len(b):
                result += _swap_u16(b, offset, src_endian, dst_endian)
            else:
                result += b[offset:]
        return _bytes_to_json(bytes(result))

    text_offset = int(parsed["text_offset"])
    label_offset = int(parsed["label_offset"])
    a0_value = int(parsed["a0_value"])

    text_offset_out = text_offset + 4 if text_offset not in (0, 0xFFFFFFFF) else text_offset
    label_offset_out = label_offset + 4 if label_offset not in (0, 0xFFFFFFFF) else label_offset

    bytes_5c_60 = bytearray(_bytes_from_json(str(parsed["bytes_5c_60_hex"])))
    bytes_5c_60[2] = bytes_5c_60[2] | 0x20

    move_a0_to_raw_head = label_offset == 0 and text_offset not in (0, 0xFFFFFFFF) and a0_value != 0

    raw_payload = bytearray(_bytes_from_json(str(parsed["raw_payload_hex"])))
    if move_a0_to_raw_head:
        raw_payload = bytearray(_write_u32(a0_value + 4, dst_endian)) + raw_payload
    else:
        raw_payload = bytearray(_write_u32(a0_value, dst_endian)) + raw_payload

    if text_offset not in (0, 0xFFFFFFFF):
        text_start = text_offset - 0xA4 + 4
        text_end = text_start
        while text_end + 1 < len(raw_payload):
            if raw_payload[text_end : text_end + 2] == b"\x00\x00":
                text_end += 2
                break
            text_end += 2
        text_blob = bytes(raw_payload[text_start:text_end])
        if not (text_blob[:1] == b"\x40" and _looks_like_ascii_identifier_blob(text_blob[1:])):
            raw_payload[text_start:text_end] = _convert_utf16_text(text_blob, src_endian, dst_endian)

    return {
        "text_offset": text_offset_out,
        "label_offset": label_offset_out,
        "a0_value": 0,
        "prefix_8_2c_hex": parsed["prefix_8_2c_hex"],
        "float_2c_hex": _swap_float_hex(str(parsed["float_2c_hex"])),
        "float_30_hex": _swap_float_hex(str(parsed["float_30_hex"])),
        "prefix_34_40_hex": parsed["prefix_34_40_hex"],
        "float_40_hex": _swap_float_hex(str(parsed["float_40_hex"])),
        "float_44_hex": _swap_float_hex(str(parsed["float_44_hex"])),
        "float_48_hex": _swap_float_hex(str(parsed["float_48_hex"])),
        "float_4c_hex": _swap_float_hex(str(parsed["float_4c_hex"])),
        "float_50_hex": _swap_float_hex(str(parsed["float_50_hex"])),
        "u16_block_54_5c_hex": _swap_u16_block_hex(str(parsed["u16_block_54_5c_hex"])),
        "bytes_5c_60_hex": _bytes_to_json(bytes(bytes_5c_60)),
        "u32_60": parsed["u32_60"],
        "bytes_68_70_hex": parsed["bytes_68_70_hex"],
        "float_70_hex": _swap_float_hex(str(parsed["float_70_hex"])),
        "float_74_hex": _swap_float_hex(str(parsed["float_74_hex"])),
        "float_78_hex": _swap_float_hex(str(parsed["float_78_hex"])),
        "float_7c_hex": _swap_float_hex(str(parsed["float_7c_hex"])),
        "float_84_hex": _swap_float_hex(str(parsed["float_84_hex"])),
        "float_88_hex": _swap_float_hex(str(parsed["float_88_hex"])),
        "float_8c_hex": _swap_float_hex(str(parsed["float_8c_hex"])),
        "float_90_hex": _swap_float_hex(str(parsed["float_90_hex"])),
        "bytes_94_a0_hex": parsed["bytes_94_a0_hex"],
        "raw_payload_hex": _bytes_to_json(bytes(raw_payload)),
    }


def _convert_section_via_model(section: Section, src_endian: str, dst_endian: str) -> Section:
    payload = _section_to_json_dict(section, src_endian)
    tag = section.tag
    parsed = payload.get("parsed")

    if tag in {"txl1", "fnl1"} and isinstance(parsed, dict):
        entries = parsed["entries"]
        if tag == "txl1" and src_endian == "big" and dst_endian == "little":
            for entry in entries:
                name = entry["name"]
                if isinstance(name, str) and name.endswith(".bflim"):
                    entry["name"] = name[:-6]
        return _section_from_json_dict(payload, dst_endian)

    if tag in {"lyt1", "pan1", "bnd1", "pas1", "pae1", "grs1", "gre1"} and isinstance(parsed, dict):
        return _section_from_json_dict(payload, dst_endian)
    if tag == "pic1" and isinstance(parsed, dict):
        payload["parsed"] = _convert_pic1_parsed(parsed, src_endian, dst_endian)
        return _section_from_json_dict(payload, dst_endian)
    if tag == "wnd1" and isinstance(parsed, dict):
        payload["parsed"] = _convert_wnd1_parsed(parsed, src_endian, dst_endian)
        return _section_from_json_dict(payload, dst_endian)
    if tag == "grp1" and isinstance(parsed, dict):
        return _section_from_json_dict(payload, dst_endian)
    if tag == "cnt1" and isinstance(parsed, dict):
        payload["parsed"] = _convert_cnt1_parsed(parsed, src_endian, dst_endian)
        return _section_from_json_dict(payload, dst_endian)
    if tag == "usd1" and isinstance(parsed, dict):
        payload["parsed"] = _convert_usd1_parsed(parsed, src_endian, dst_endian)
        return _section_from_json_dict(payload, dst_endian)

    if tag == "mat1" and isinstance(parsed, dict):
        payload["parsed"] = _convert_mat1_parsed(parsed, src_endian, dst_endian)
        return _section_from_json_dict(payload, dst_endian)
    if tag == "txt1" and isinstance(parsed, dict):
        payload["parsed"] = _convert_txt1_parsed(parsed, src_endian, dst_endian)
        return _section_from_json_dict(payload, dst_endian)
    if tag == "prt1" and isinstance(parsed, dict):
        payload["parsed"] = _convert_prt1_parsed(parsed, src_endian, dst_endian)
        return _section_from_json_dict(payload, dst_endian)
    raise ValueError(f"Unsupported BFLYT section {tag}")


def _wrap_section(tag: str, payload: bytes, dst_endian: str) -> bytes:
    return tag.encode("ascii") + _write_u32(len(payload) + 8, dst_endian) + payload


def _parse_resource_table(data: bytes, endian: str, strip_bflim: bool) -> ResourceTable:
    count = _read_u16(data, 8, endian)
    entries: list[ResourceEntry] = []
    for index in range(count):
        offset = _read_u32(data, 12 + index * 4, endian)
        name = _read_cstring(data, 12 + offset)
        if strip_bflim and name.endswith(b".bflim"):
            name = name[:-6]
        entries.append(ResourceEntry(name=name))
    return ResourceTable(entries=entries)


def _serialize_resource_table(table: ResourceTable, endian: str) -> bytes:
    payload = bytearray()
    payload += _write_u16(len(table.entries), endian)
    payload += b"\x00\x00"
    table_size = 4 * len(table.entries)
    string_offset = table_size
    strings = bytearray()
    offsets = []
    for entry in table.entries:
        offsets.append(string_offset + len(strings))
        strings += entry.name + b"\x00"
    strings += b"\x00" * ((_align(len(payload) + len(strings)) - (len(payload) + len(strings))) % 4)

    for offset in offsets:
        payload += _write_u32(offset, endian)
    payload += strings
    return payload


def _material_counts(memory: int) -> dict[str, int]:
    return {
        "texture_maps": memory & 0x3,
        "texture_srt": (memory >> 2) & 0x3,
        "texture_coord_gen": (memory >> 4) & 0x3,
        "texture_extensions": (memory >> 21) & 0x1,
    }


def _convert_texture_maps(data: bytes, count: int, src_endian: str, dst_endian: str) -> bytes:
    converted = bytearray()
    for index in range(count):
        entry = data[index * 4 : (index + 1) * 4]
        converted += _write_u16(int.from_bytes(entry[:2], src_endian), dst_endian)
        converted += entry[2:4]
    return bytes(converted)


def _convert_texture_srt(data: bytes, count: int, src_endian: str, dst_endian: str) -> bytes:
    converted = bytearray()
    size = 20
    for index in range(count):
        entry = data[index * size : (index + 1) * size]
        for offset in range(0, size, 4):
            converted += _swap_float32(entry, offset, src_endian, dst_endian)
    return bytes(converted)


def _convert_texture_coord_gen(data: bytes, count: int) -> bytes:
    converted = bytearray()
    size = 8
    for index in range(count):
        entry = data[index * size : (index + 1) * size]
        converted += entry + (b"\x00" * 8)
    return bytes(converted)


def _parse_material_table(data: bytes, endian: str) -> MaterialTable:
    count = _read_u16(data, 8, endian)
    offsets = [_read_u32(data, 12 + index * 4, endian) for index in range(count)]
    materials: list[MaterialEntry] = []
    for index, start in enumerate(offsets):
        end = offsets[index + 1] if index + 1 < len(offsets) else len(data)
        chunk = data[start:end]
        name = chunk[:0x1C].split(b"\x00")[0]
        if endian == "big":
            header_20 = b""
            color_1 = chunk[0x1C:0x20]
            color_2 = chunk[0x20:0x24]
            memory = _read_u32(chunk, 0x24, endian)
            tail = chunk[0x28:]
        else:
            memory = _read_u32(chunk, 0x1C, endian)
            header_20 = chunk[0x20:0x24]
            color_1 = chunk[0x24:0x28]
            color_2 = chunk[0x28:0x2C]
            tail = chunk[0x2C:]
        counts = _material_counts(memory)

        cursor = 0
        texture_maps_size = counts["texture_maps"] * 4
        texture_srt_size = counts["texture_srt"] * 20
        texture_coord_size = counts["texture_coord_gen"] * 8
        texture_extensions_size = counts["texture_extensions"] * 4

        texture_maps = tail[cursor : cursor + texture_maps_size]
        cursor += texture_maps_size
        texture_srt = tail[cursor : cursor + texture_srt_size]
        cursor += texture_srt_size
        texture_coord = tail[cursor : cursor + texture_coord_size]
        cursor += texture_coord_size
        texture_extensions = tail[cursor : cursor + texture_extensions_size]
        cursor += texture_extensions_size
        remainder = tail[cursor:]

        materials.append(
            MaterialEntry(
                name=name,
                header_20=header_20,
                color_1=color_1,
                color_2=color_2,
                memory=memory,
                original_memory=memory,
                texture_maps=texture_maps,
                texture_srt=texture_srt,
                texture_coord=texture_coord,
                texture_extensions=texture_extensions,
                remainder=remainder,
            )
        )
    return MaterialTable(materials=materials)


def _serialize_material_table_json(table: MaterialTable, endian: str) -> bytes:
    body = bytearray()
    offsets: list[int] = []
    running_offset = 8 + 4 + 4 * len(table.materials)
    for material in table.materials:
        offsets.append(running_offset + len(body))
        chunk = bytearray()
        chunk += _pad(material.name, 0x1C)
        if endian == "big":
            chunk += material.color_1
            chunk += material.color_2
            chunk += _write_u32(material.memory, endian)
        else:
            chunk += _write_u32(material.memory, endian)
            chunk += material.header_20
            chunk += material.color_1
            chunk += material.color_2
        chunk += material.texture_maps
        chunk += material.texture_srt
        chunk += material.texture_coord
        chunk += material.texture_extensions
        chunk += material.remainder
        body += chunk

    payload = bytearray()
    payload += _write_u16(len(table.materials), endian)
    payload += b"\x00\x00"
    for offset in offsets:
        payload += _write_u32(offset, endian)
    payload += body
    return bytes(payload)


def _parse_txt1_entry(data: bytes, endian: str) -> Txt1Entry:
    return Txt1Entry(
        prefix_8_2c=data[8:0x2C],
        float_2c=data[0x2C:0x30],
        float_30=data[0x30:0x34],
        prefix_34_40=data[0x34:0x40],
        float_40=data[0x40:0x44],
        float_44=data[0x44:0x48],
        float_48=data[0x48:0x4C],
        float_4c=data[0x4C:0x50],
        float_50=data[0x50:0x54],
        u16_block_54_5c=data[0x54:0x5C],
        bytes_5c_60=data[0x5C:0x60],
        u32_60=_read_u32(data, 0x60, endian),
        text_offset=_read_u32(data, 0x64, endian),
        bytes_68_70=data[0x68:0x70],
        float_70=data[0x70:0x74],
        float_74=data[0x74:0x78],
        float_78=data[0x78:0x7C],
        float_7c=data[0x7C:0x80],
        label_offset=_read_u32(data, 0x80, endian),
        float_84=data[0x84:0x88],
        float_88=data[0x88:0x8C],
        float_8c=data[0x8C:0x90],
        float_90=data[0x90:0x94],
        bytes_94_a0=data[0x94:0xA0],
        a0_value=_read_u32(data, 0xA0, endian),
        raw_payload=data[0xA4:],
    )


def _parse_wnd1_entry(data: bytes, endian: str) -> Wnd1Entry:
    return Wnd1Entry(
        bytes_54_5c=data[0x54:0x5C],
        u16_block_5c_64=data[0x5C:0x64],
        bytes_64_68=data[0x64:0x68],
        u32_68=_read_u32(data, 0x68, endian),
        u32_6c=_read_u32(data, 0x6C, endian),
        bytes_70_80=data[0x70:0x80],
        u16_80=_read_u16(data, 0x80, endian),
        bytes_82_84=data[0x82:0x84],
        trailing_words=data[0x84:],
    )


def _parse_usd1_table(data: bytes, endian: str) -> Usd1Table:
    entry_count = _read_u16(data, 8, endian)
    entries: list[Usd1ValueEntry] = []
    cursor = 0x0C
    for _ in range(entry_count):
        entries.append(
            Usd1ValueEntry(
                first_u32=_read_u32(data, cursor, endian),
                second_u32=_read_u32(data, cursor + 4, endian),
                value_u16=_read_u16(data, cursor + 8, endian),
                suffix=data[cursor + 10 : cursor + 12],
            )
        )
        cursor += 0x0C
    return Usd1Table(count_padding=data[0x0A:0x0C], entries=entries, tail=data[cursor:])


def _parse_prt1_entry(data: bytes, endian: str) -> Prt1Entry:
    return Prt1Entry(
        prefix_8_54=data[8:0x54],
        property_count=_read_u32(data, 0x54, endian),
        float_58=data[0x58:0x5C],
        float_5c=data[0x5C:0x60],
        tail=data[0x60:],
    )


def _parse_grp1_entry(data: bytes, endian: str) -> Grp1Entry:
    pane_count = _read_u16(data, 0x2A, endian)
    return Grp1Entry(
        name_block=data[8:0x2A],
        pane_count=pane_count,
        panes_blob=data[0x2C : 0x2C + pane_count * 0x18],
    )


def _parse_cnt1_entry(data: bytes) -> Cnt1Entry:
    header_end = min(len(data), 0x1C)
    return Cnt1Entry(
        header_words=data[8:header_end],
        tail=data[header_end:],
    )


def _convert_prt1_parsed(parsed: dict[str, object], src_endian: str, dst_endian: str) -> dict[str, object]:
    prefix = _bytes_from_json(str(parsed["prefix_8_54_hex"]))
    property_count = int(parsed["property_count"])
    float_58 = _bytes_from_json(str(parsed["float_58_hex"]))
    float_5c = _bytes_from_json(str(parsed["float_5c_hex"]))
    tail = _bytes_from_json(str(parsed["tail_hex"]))

    converted_prefix = bytearray(prefix[:0x24])
    converted_prefix += _swap_float32(prefix, 0x24, src_endian, dst_endian)
    converted_prefix += _swap_float32(prefix, 0x28, src_endian, dst_endian)
    converted_prefix += prefix[0x2C:0x38]
    converted_prefix += _swap_float32(prefix, 0x38, src_endian, dst_endian)
    converted_prefix += _swap_float32(prefix, 0x3C, src_endian, dst_endian)
    converted_prefix += _swap_float32(prefix, 0x40, src_endian, dst_endian)
    converted_prefix += _swap_float32(prefix, 0x44, src_endian, dst_endian)
    converted_prefix += _swap_float32(prefix, 0x48, src_endian, dst_endian)

    return {
        "prefix_8_54_hex": _bytes_to_json(bytes(converted_prefix)),
        "property_count": property_count,
        "float_58_hex": _bytes_to_json(_swap_float32(float_58, 0, src_endian, dst_endian)),
        "float_5c_hex": _bytes_to_json(_swap_float32(float_5c, 0, src_endian, dst_endian)),
        "tail_hex": _bytes_to_json(_convert_prt1_tail(tail, property_count, src_endian, dst_endian)),
    }


def _convert_prt1_suffix(data: bytes, src_endian: str, dst_endian: str) -> bytes:
    valid_tags = {"txt1", "pic1", "wnd1", "pan1", "bnd1", "prt1", "grp1", "cnt1", "usd1", "pas1", "pae1", "grs1", "gre1"}
    payload = bytearray()
    cursor = 0
    while cursor < len(data):
        if cursor + 8 <= len(data):
            tag = data[cursor : cursor + 4].decode("ascii", errors="ignore")
            size = _read_u32(data, cursor + 4, src_endian)
            if tag in valid_tags and 8 <= size <= len(data) - cursor:
                payload += _convert_section_via_model(Section(tag, data[cursor : cursor + size]), src_endian, dst_endian).data
                cursor += size
                continue
        tail = data[cursor:]
        if cursor == 0 and _looks_like_ascii_identifier_blob(tail):
            token_end = tail.find(b"\x00")
            token_size = len(tail) if token_end == -1 else _align(token_end + 1, 4)
            payload += tail[:token_size]
            cursor += token_size
            continue
        if cursor + 4 <= len(data):
            if cursor + 4 == len(data) and data[cursor + 1 : cursor + 4] == b"\x00\x00\x00":
                payload += data[cursor : cursor + 4]
                cursor += 4
                continue
            payload += _swap_u32(data, cursor, src_endian, dst_endian)
            cursor += 4
        else:
            payload += data[cursor:]
            break
    return bytes(payload)


def _convert_prt1_tail(tail: bytes, property_count: int, src_endian: str, dst_endian: str) -> bytes:
    # Prepend 0x60 zero bytes to restore absolute-offset arithmetic: in the original section,
    # the tail starts at offset 0x60 (8-byte header + 0x58-byte prefix block).
    data = bytes(0x60) + tail
    payload = bytearray()
    txt1_offset = _read_u32(data, 0x7C, src_endian) if len(data) >= 0x80 else 0
    if (
        property_count == 2
        and len(data) >= 0xD0
        and all(0x20 <= byte <= 0x7E or byte == 0 for byte in data[0x60:0x78])
        and any(byte != 0 for byte in data[0x60:0x78])
        and txt1_offset >= 0x80
        and txt1_offset + 8 <= len(data)
        and data[txt1_offset : txt1_offset + 4] == b"txt1"
    ):
        prefix = bytearray(data[0x80:txt1_offset])
        for offset in range(0x80, txt1_offset, 4):
            value = _read_u32(data, offset, src_endian)
            if txt1_offset <= value < len(data):
                start = offset - 0x80
                prefix[start : start + 4] = _write_u32(value + 4, dst_endian)
        payload += data[0x60:0x78]
        payload += data[0x78:0x7C]
        payload += _swap_u32(data, 0x7C, src_endian, dst_endian)
        payload += prefix
        cursor = txt1_offset
        while cursor + 8 <= len(data):
            tag = data[cursor : cursor + 4].decode("ascii", errors="ignore")
            size = _read_u32(data, cursor + 4, src_endian)
            if tag not in {"txt1", "pic1", "wnd1", "pan1", "bnd1", "prt1", "usd1", "cnt1", "pas1", "pae1", "grp1", "grs1", "gre1"}:
                break
            if size < 8 or cursor + size > len(data):
                break
            payload += _convert_section_via_model(Section(tag, data[cursor : cursor + size]), src_endian, dst_endian).data
            cursor += size
        payload += data[cursor:]
        return bytes(payload)
    cursor = 0x60
    named_entry_mode = (
        property_count > 0
        and len(data) >= cursor + 0x28
        and all(0x20 <= byte <= 0x7E or byte == 0 for byte in data[cursor : cursor + 0x18])
        and any(byte != 0 for byte in data[cursor : cursor + 0x18])
        and len(data) >= cursor + property_count * 0x28
    )
    zero_entry_mode = (
        property_count > 0
        and len(data) >= cursor + property_count * 0x28
        and all(byte == 0 for byte in data[cursor : cursor + property_count * 0x28])
    )
    if named_entry_mode or zero_entry_mode:
        entries = []
        offsets = []
        for _ in range(property_count):
            entry_bytes = data[cursor : cursor + 0x28]
            entries.append(entry_bytes)
            offsets.append(_read_u32(entry_bytes, 0x1C, src_endian))
            cursor += 0x28
        nonzero_offsets = [offset for offset in offsets if offset]
        tail_start = min(nonzero_offsets) if nonzero_offsets else cursor
        gap = data[cursor:tail_start]
        sorted_offsets = sorted(nonzero_offsets)
        converted_blocks: dict[int, bytes] = {}
        original_sizes: dict[int, int] = {}
        for index, start in enumerate(sorted_offsets):
            end = sorted_offsets[index + 1] if index + 1 < len(sorted_offsets) else len(data)
            chunk = data[start:end]
            original_sizes[start] = end - start
            chunk_tag = chunk[:4].decode("ascii")
            if chunk_tag in {"lyt1", "txl1", "fnl1", "mat1", "pan1", "bnd1", "pic1", "txt1", "wnd1", "prt1", "grp1", "cnt1", "usd1", "pas1", "pae1", "grs1", "gre1"}:
                section_size = _read_u32(chunk, 4, src_endian)
                if 8 <= section_size <= len(chunk):
                    converted_blocks[start] = _convert_section_via_model(Section(chunk_tag, chunk[:section_size]), src_endian, dst_endian).data + _convert_prt1_suffix(chunk[section_size:], src_endian, dst_endian)
                else:
                    converted_blocks[start] = _convert_section_via_model(Section(chunk_tag, chunk), src_endian, dst_endian).data
            else:
                converted_blocks[start] = chunk
        new_offsets: dict[int, int] = {}
        running_offset = tail_start
        for start in sorted_offsets:
            new_offsets[start] = running_offset
            running_offset += len(converted_blocks[start])

        def translate_tail_offset(value: int) -> int:
            if value == 0 or value < tail_start:
                return value
            delta = 0
            for start in sorted_offsets:
                if start >= value:
                    break
                delta += len(converted_blocks[start]) - original_sizes[start]
            return value + delta

        payload_entries = bytearray()
        for entry_bytes in entries:
            payload_entries += entry_bytes[:0x18]
            payload_entries += entry_bytes[0x18:0x1C]
            original_offset = _read_u32(entry_bytes, 0x1C, src_endian)
            payload_entries += _write_u32(new_offsets[original_offset] if original_offset else 0, dst_endian)
            payload_entries += _write_u32(translate_tail_offset(_read_u32(entry_bytes, 0x20, src_endian)), dst_endian)
            payload_entries += _write_u32(translate_tail_offset(_read_u32(entry_bytes, 0x24, src_endian)), dst_endian)
        payload += payload_entries
        payload += gap
        if sorted_offsets:
            for start in sorted_offsets:
                payload += converted_blocks[start]
        else:
            payload += _convert_prt1_suffix(data[cursor:], src_endian, dst_endian)
    else:
        for _ in range(property_count):
            entry_bytes = data[cursor : cursor + 0x18]
            payload += entry_bytes[:8]
            payload += _swap_u32(entry_bytes, 8, src_endian, dst_endian)
            payload += _swap_u32(entry_bytes, 12, src_endian, dst_endian)
            payload += _swap_u32(entry_bytes, 16, src_endian, dst_endian)
            cursor += 0x18
        payload += data[cursor:]
    return bytes(payload)


r"""
AppMap_00
AppPictureBook_00
Only in \UltimateBotWConverter\downloads\test_bflyt\2_Switch_json: ChangeControllerNN_00.json
MainHardMode_00
MainShortCut_00
PaAllControllerTipsNN_00 and PaAllControllerTips_00
PaMessageTipsDrcImageNN_00 and PaMessageTipsDrcImage_00
PaMessageTipsDrcImgAmiiboNN_00 and PaMessageTipsDrcImgAmiibo_00
PaSeekPadDecoText_00
PaSeekPadScanningLine_00
PaTempMeter_00
PauseMenuBG_00
ShopBtnList5_00
SystemWindow_00

\UltimateBotWConverter:
_____________________________________________________________________________
1. 1_WiiU → 1_WiiU_json
@'
from pathlib import Path
from ubotw_converter.bflyt import dump_bflyt_json
src = Path("downloads/test_bflyt/1_WiiU")
dst = Path("downloads/test_bflyt/1_WiiU_json")
count = 0
for f in sorted(src.rglob("*.bflyt")):
    out = dst / f.relative_to(src).with_suffix(".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    dump_bflyt_json(f, out)
    count += 1
print(f"{count} files -> 1_WiiU_json")
'@ | py -3.9 -

________________________________
2. 1_WiiU_json → 1_WiiU_json_bflyt
@'
from pathlib import Path
from ubotw_converter.bflyt import build_bflyt_from_json
src = Path("downloads/test_bflyt/1_WiiU_json")
dst = Path("downloads/test_bflyt/1_WiiU_json_bflyt")
ref = Path("downloads/test_bflyt/1_WiiU")
ok = fail = 0
for f in sorted(src.rglob("*.json")):
    rel = f.relative_to(src)
    out = dst / rel.with_suffix(".bflyt")
    out.parent.mkdir(parents=True, exist_ok=True)
    build_bflyt_from_json(f, out)
    orig = ref / rel.with_suffix(".bflyt")
    if orig.exists():
        if out.read_bytes() == orig.read_bytes(): ok += 1
        else: print(f"MISMATCH: {f.name}"); fail += 1
print(f"OK={ok} FAIL={fail}")
'@ | py -3.9 -

________________________________
3. 1_WiiU → 1_WiiU_json_bflyt
@'
import tempfile, shutil
from pathlib import Path
from ubotw_converter.bflyt import dump_bflyt_json, build_bflyt_from_json
src = Path("downloads/test_bflyt/1_WiiU")
dst = Path("downloads/test_bflyt/1_WiiU_json_bflyt")
ok = fail = 0
tmp = Path(tempfile.mkdtemp())
for f in sorted(src.rglob("*.bflyt")):
    rel = f.relative_to(src)
    jf = tmp / rel.with_suffix(".json")
    jf.parent.mkdir(parents=True, exist_ok=True)
    dump_bflyt_json(f, jf)
    out = dst / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    build_bflyt_from_json(jf, out)
    if out.read_bytes() == f.read_bytes(): ok += 1
    else: print(f"MISMATCH: {f.name}"); fail += 1
shutil.rmtree(tmp)
print(f"OK={ok} FAIL={fail}")
'@ | py -3.9 -





_____________________________________________________________________________
4. 2_Switch → 2_Switch_json
@'
from pathlib import Path
from ubotw_converter.bflyt import dump_bflyt_json
src = Path("downloads/test_bflyt/2_Switch")
dst = Path("downloads/test_bflyt/2_Switch_json")
count = 0
for f in sorted(src.rglob("*.bflyt")):
    out = dst / f.relative_to(src).with_suffix(".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    dump_bflyt_json(f, out)
    count += 1
print(f"{count} files -> 2_Switch_json")
'@ | py -3.9 -

________________________________
5. 2_Switch_json → 2_Switch_json_bflyt
@'
from pathlib import Path
from ubotw_converter.bflyt import build_bflyt_from_json
src = Path("downloads/test_bflyt/2_Switch_json")
dst = Path("downloads/test_bflyt/2_Switch_json_bflyt")
ref = Path("downloads/test_bflyt/2_Switch")
ok = fail = 0
for f in sorted(src.rglob("*.json")):
    rel = f.relative_to(src)
    out = dst / rel.with_suffix(".bflyt")
    out.parent.mkdir(parents=True, exist_ok=True)
    build_bflyt_from_json(f, out)
    orig = ref / rel.with_suffix(".bflyt")
    if orig.exists():
        if out.read_bytes() == orig.read_bytes(): ok += 1
        else: print(f"MISMATCH: {f.name}"); fail += 1
print(f"OK={ok} FAIL={fail}")
'@ | py -3.9 -

________________________________
6. 2_Switch → 2_Switch_json_bflyt
@'
import tempfile, shutil
from pathlib import Path
from ubotw_converter.bflyt import dump_bflyt_json, build_bflyt_from_json
src = Path("downloads/test_bflyt/2_Switch")
dst = Path("downloads/test_bflyt/2_Switch_json_bflyt")
ok = fail = 0
tmp = Path(tempfile.mkdtemp())
for f in sorted(src.rglob("*.bflyt")):
    rel = f.relative_to(src)
    jf = tmp / rel.with_suffix(".json")
    jf.parent.mkdir(parents=True, exist_ok=True)
    dump_bflyt_json(f, jf)
    out = dst / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    build_bflyt_from_json(jf, out)
    if out.read_bytes() == f.read_bytes(): ok += 1
    else: print(f"MISMATCH: {f.name}"); fail += 1
shutil.rmtree(tmp)
print(f"OK={ok} FAIL={fail}")
'@ | py -3.9 -




_____________________________________________________________________________
7. 1_WiiU → 3_Convert_Switch_json
@'
import tempfile, shutil
from pathlib import Path
from ubotw_converter.bflyt import convert_bflyt, dump_bflyt_json
src = Path("downloads/test_bflyt/1_WiiU")
dst = Path("downloads/test_bflyt/3_Convert_Switch_json")
count = 0
tmp = Path(tempfile.mkdtemp())
for f in sorted(src.rglob("*.bflyt")):
    rel = f.relative_to(src)
    cf = tmp / rel
    cf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(f, cf)
    convert_bflyt(cf)
    out = dst / rel.with_suffix(".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    dump_bflyt_json(cf, out)
    count += 1
shutil.rmtree(tmp)
print(f"{count} files -> 3_Convert_Switch_json")
'@ | py -3.9 -

________________________________
8. 3_Convert_Switch_json → 3_Convert_Switch_json_bflyt
@'
import tempfile, shutil
from pathlib import Path
from ubotw_converter.bflyt import build_bflyt_from_json, convert_bflyt
src = Path("downloads/test_bflyt/3_Convert_Switch_json")
dst = Path("downloads/test_bflyt/3_Convert_Switch_json_bflyt")
wiiu = Path("downloads/test_bflyt/1_WiiU")
ok = fail = 0
tmp = Path(tempfile.mkdtemp())
for f in sorted(src.rglob("*.json")):
    rel = f.relative_to(src)
    out = dst / rel.with_suffix(".bflyt")
    out.parent.mkdir(parents=True, exist_ok=True)
    build_bflyt_from_json(f, out)
    ref_src = wiiu / rel.with_suffix(".bflyt")
    if ref_src.exists():
        ref = tmp / rel
        ref.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ref_src, ref)
        convert_bflyt(ref)
        if out.read_bytes() == ref.read_bytes(): ok += 1
        else: print(f"MISMATCH: {f.name}"); fail += 1
shutil.rmtree(tmp)
print(f"OK={ok} FAIL={fail}")
'@ | py -3.9 -

________________________________
9. 1_WiiU → 3_Convert_Switch_json_bflyt
@'
import tempfile, shutil
from pathlib import Path
from ubotw_converter.bflyt import convert_bflyt, dump_bflyt_json, build_bflyt_from_json
src = Path("downloads/test_bflyt/1_WiiU")
dst = Path("downloads/test_bflyt/3_Convert_Switch_json_bflyt")
ok = fail = 0
tmp = Path(tempfile.mkdtemp())
for f in sorted(src.rglob("*.bflyt")):
    rel = f.relative_to(src)
    cf = tmp / "c" / rel
    cf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(f, cf)
    convert_bflyt(cf)
    jf = tmp / "j" / rel.with_suffix(".json")
    jf.parent.mkdir(parents=True, exist_ok=True)
    dump_bflyt_json(cf, jf)
    out = dst / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    build_bflyt_from_json(jf, out)
    if out.read_bytes() == cf.read_bytes(): ok += 1
    else: print(f"MISMATCH: {f.name}"); fail += 1
shutil.rmtree(tmp)
print(f"OK={ok} FAIL={fail}")
'@ | py -3.9 -


"""