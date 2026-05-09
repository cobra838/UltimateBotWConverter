import struct
from pathlib import Path
from bflan import FLAN
from common import readString as _readString, roundUp, Section


printTexList = False
printFntList = False
printMatInfo = False
printPanInfo = False


def readString(data, offset=0, charWidth=1, encoding='utf-8'):
    try:
        return _readString(data, offset, charWidth, encoding)
    except UnicodeDecodeError:
        end = data.find(b'\0' * charWidth, offset)
        while end != -1:
            if (end - offset) % charWidth == 0:
                break
            end = data.find(b'\0' * charWidth, end + 1)
        raw = data[offset:] if end == -1 else data[offset:end]
        return raw.decode(encoding, errors='replace')


def _bf_align(value, alignment=4):
    return (value + alignment - 1) & ~(alignment - 1)


def _bf_read_u16(data, offset, endian):
    return int.from_bytes(data[offset:offset + 2], endian)


def _bf_read_u32(data, offset, endian):
    return int.from_bytes(data[offset:offset + 4], endian)


def _bf_write_u16(value, endian):
    return int(value).to_bytes(2, endian)


def _bf_write_u32(value, endian):
    return int(value).to_bytes(4, endian)


def _bf_swap_u16(data, offset, src_endian, dst_endian):
    return _bf_write_u16(_bf_read_u16(data, offset, src_endian), dst_endian)


def _bf_swap_u32(data, offset, src_endian, dst_endian):
    return _bf_write_u32(_bf_read_u32(data, offset, src_endian), dst_endian)


def _bf_swap_words(data, start=0, src_endian='big', dst_endian='little'):
    out = bytearray(data[:start])
    pos = start
    while pos + 4 <= len(data):
        out += _bf_swap_u32(data, pos, src_endian, dst_endian)
        pos += 4
    out += data[pos:]
    return bytes(out)


def _bf_read_cstr(data, offset):
    end = data.find(b'\0', offset)
    return data[offset:] if end == -1 else data[offset:end]


class RawString(str):
    def __new__(cls, value, raw_bytes=None):
        obj = str.__new__(cls, value)
        obj.raw_bytes = bytes(raw_bytes if raw_bytes is not None else value.encode('utf-8'))
        return obj


def _bf_wrap_section(tag, body, endian):
    return tag + _bf_write_u32(len(body) + 8, endian) + body


def _bf_parse_sections(data):
    endian = 'big' if data[4:6] == b'\xFE\xFF' else 'little'
    pos = _bf_read_u16(data, 6, endian)
    count = _bf_read_u16(data, 16, endian)
    sections = []
    for _ in range(count):
        tag = data[pos:pos + 4]
        size = _bf_read_u32(data, pos + 4, endian)
        sections.append((tag, data[pos + 8:pos + size]))
        pos += size
    return sections, endian


def _bf_write_file(sections, version):
    body = b''.join(_bf_wrap_section(tag, payload, 'little') for tag, payload in sections)
    return (
        b'FLYT'
        + b'\xFF\xFE'
        + _bf_write_u16(0x14, 'little')
        + _bf_write_u32(version, 'little')
        + _bf_write_u32(0x14 + len(body), 'little')
        + _bf_write_u16(len(sections), 'little')
        + b'\0\0'
        + body
    )


def _bf_convert_named_table(body, strip_bflim=False):
    count = _bf_read_u16(body, 0, 'big')
    names = []
    for i in range(count):
        rel = _bf_read_u32(body, 4 + i * 4, 'big')
        name = _bf_read_cstr(body, 4 + rel)
        if strip_bflim and name.endswith(b'.bflim'):
            name = name[:-6]
        names.append(name)

    out = bytearray()
    out += _bf_write_u16(len(names), 'little')
    out += b'\0\0'
    table_size = 4 * len(names)
    strings = bytearray()
    offsets = []
    for name in names:
        offsets.append(table_size + len(strings))
        strings += name + b'\0'
    strings += b'\0' * ((_bf_align(len(out) + len(strings)) - (len(out) + len(strings))) % 4)
    for offset in offsets:
        out += _bf_write_u32(offset, 'little')
    out += strings
    return bytes(out)


def _bf_write_named_table(names):
    out = bytearray()
    out += _bf_write_u16(len(names), 'little')
    out += b'\0\0'
    strings = bytearray()
    offsets = []
    table_start = 4 + 4 * len(names)
    for name in names:
        raw = name if isinstance(name, bytes) else name.encode('utf-8')
        offsets.append(table_start + len(strings) - 4)
        strings += raw + b'\0'
    for offset in offsets:
        out += _bf_write_u32(offset, 'little')
    out += strings
    out += b'\0' * ((_bf_align(len(out)) - len(out)) % 4)
    return bytes(out)


def _bf_serialize_layout_model(layout):
    body = struct.pack(
        '<B3x4f',
        layout.originType,
        layout.layoutWidth,
        layout.layoutHeight,
        layout.partsWidth,
        layout.partsHeight,
    )
    name = layout.name.encode('utf-8') + b'\0'
    return body + name + b'\0' * ((_bf_align(len(body) + len(name)) - (len(body) + len(name))) % 4)


def _bf_serialize_texture_list_model(texture_list):
    names = []
    for texture, fmt in zip(texture_list.textures, texture_list.formats):
        names.append(f"{texture}{fmt}")
    return _bf_write_named_table(names)


def _bf_serialize_font_list_model(font_list):
    return _bf_write_named_table([f"{font}.bffnt" for font in font_list.fonts])


def _bf_convert_pane_base(body):
    out = bytearray(body[:36])
    out += body[36:40][::-1]
    out += body[40:44][::-1]
    out += body[44:52]
    for offset in (52, 56, 60, 64, 68, 72):
        out += body[offset:offset + 4][::-1]
    return bytes(out)


def _bf_convert_lyt(body):
    out = bytearray(body[:4])
    for offset in (4, 8, 12, 16):
        out += body[offset:offset + 4][::-1]
    out += body[20:]
    return bytes(out)


def _bf_material_counts(memory):
    return {
        'texture_maps': memory & 0x3,
        'texture_srt': (memory >> 2) & 0x3,
        'texture_coord_gen': (memory >> 4) & 0x3,
        'texture_extensions': (memory >> 21) & 0x1,
    }


def _bf_convert_tex_maps(data, count):
    out = bytearray()
    for i in range(count):
        entry = data[i * 4:(i + 1) * 4]
        out += entry[:2][::-1] + entry[2:4]
    return bytes(out)


def _bf_convert_tex_srt(data, count):
    out = bytearray()
    for i in range(count):
        entry = data[i * 20:(i + 1) * 20]
        for offset in range(0, 20, 4):
            out += entry[offset:offset + 4][::-1]
    return bytes(out)


def _bf_convert_tex_coord(data, count):
    out = bytearray()
    for i in range(count):
        out += data[i * 8:(i + 1) * 8] + b'\0' * 8
    return bytes(out)


def _bf_convert_material_remainder(remainder, memory, color_2, texture_maps, texture_srt, texture_coord):
    original_memory = memory
    rem = bytearray(remainder)

    if memory == 0x15 and texture_srt[:16] == b'\0' * 16 and texture_srt[16:20] != b'\0' * 4:
        memory |= 0x800

    for flag_offset in (0, 4):
        if len(rem) >= flag_offset + 4 and rem[flag_offset] == 0x09 and rem[flag_offset + 2:flag_offset + 4] == b'\0\0':
            rem[flag_offset] = 0x0B
        elif len(rem) >= flag_offset + 4 and rem[flag_offset] == 0x0B and rem[flag_offset + 2:flag_offset + 4] == b'\0\0':
            rem[flag_offset] = 0x0D

    def swap_ranges(prefix, ranges, suffix_start=None):
        out = bytearray(rem[:prefix])
        cursor = prefix
        for start, end in ranges:
            out += rem[cursor:start]
            for offset in range(start, end, 4):
                out.extend(rem[offset:offset + 4][::-1])
            cursor = end
        out += rem[cursor if suffix_start is None else suffix_start:]
        return out

    if memory & 0x8000 and len(rem) == 20:
        rem = bytearray(rem[:8] + rem[8:12][::-1] + rem[12:16][::-1] + rem[16:20])
    elif original_memory == 0x846A and len(rem) == 28:
        rem = bytearray(rem[:16] + rem[16:20][::-1] + rem[20:24][::-1] + rem[24:28])
    elif original_memory == 0x1006A and len(rem) == 44:
        rem = bytearray(rem[:12] + rem[12:16][::-1] + rem[16:20][::-1] + rem[20:32] + rem[32:36][::-1] + rem[36:40][::-1] + rem[40:44])
    elif original_memory == 0x1C4BF and len(rem) == 84:
        if rem[4] == 0x09 and rem[6:8] == b'\0\0':
            rem[4] = 0x0B
        rem = bytearray(
            rem[:16]
            + b''.join(rem[o:o + 4][::-1] for o in range(16, 40, 4))
            + rem[40:52]
            + rem[52:56][::-1] + rem[56:60][::-1]
            + rem[60:72]
            + rem[72:76][::-1] + rem[76:80][::-1]
            + rem[80:84]
        )
    elif original_memory == 0x184BF and len(rem) == 72:
        rem = bytearray(rem[:20] + rem[20:24][::-1] + rem[24:28][::-1] + rem[28:40] + rem[40:44][::-1] + rem[44:48][::-1] + rem[48:60] + rem[60:64][::-1] + rem[64:68][::-1] + rem[68:72])
    elif original_memory == 0x194BF and len(rem) == 76:
        rem = bytearray(rem[:24] + rem[24:28][::-1] + rem[28:32][::-1] + rem[32:44] + rem[44:48][::-1] + rem[48:52][::-1] + rem[52:64] + rem[64:68][::-1] + rem[68:72][::-1] + rem[72:76])
    elif original_memory == 0x806A and len(rem) == 24:
        start = 8 if rem[4:8] == b'\0' * 4 and rem[12:20] == b'\x3F\x80\0\0\x3F\x80\0\0' else 12
        rem = bytearray(rem[:start] + b''.join(rem[o:o + 4][::-1] for o in range(start, 20, 4)) + rem[20:24])
    elif original_memory in (0x80BF, 0x140BF, 0x104BF) and len(rem) >= 16:
        rem = swap_ranges(12, [(12, len(rem) - 4)], len(rem) - 4)
    elif original_memory in (0x1406A, 0xC06A) and len(rem) >= 8:
        rem = bytearray(rem[:8] + b''.join(rem[o:o + 4][::-1] for o in range(8, len(rem), 4)))
    elif original_memory in (0x406A, 0x4406A) and len(rem) == 16:
        rem = bytearray(rem[:8] + rem[8:12][::-1] + rem[12:16][::-1])
    elif original_memory in (0xC2BF, 0xC0BF) and len(rem) >= 16:
        rem = swap_ranges(12, [(12, len(rem) - 4)], len(rem) - 4)
    elif original_memory == 0x1146A and len(rem) == 52:
        rem = bytearray(rem[:20] + rem[20:24][::-1] + rem[24:28][::-1] + rem[28:40] + rem[40:44][::-1] + rem[44:48][::-1] + rem[48:52])
    elif original_memory == 0x100BF and len(rem) == 48:
        rem = bytearray(rem[:16] + rem[16:20][::-1] + rem[20:24][::-1] + rem[24:36] + rem[36:40][::-1] + rem[40:44][::-1] + rem[44:48])
    elif original_memory == 0x114BF and len(rem) == 56:
        rem = bytearray(rem[:24] + rem[24:28][::-1] + rem[28:32][::-1] + rem[32:44] + rem[44:48][::-1] + rem[48:52][::-1] + rem[52:56])
    elif original_memory == 0x215 and len(rem) == 8:
        rem = bytearray(rem[:4] + rem[4:8][::-1])
    elif len(rem) >= 20:
        rem = bytearray(rem[:12] + b''.join(rem[o:o + 4][::-1] for o in range(12, len(rem), 4)))

    projection_count = (memory >> 15) & 0x3
    if projection_count:
        tev_stage_count = (memory >> 6) & 0x7
        projection_offset = tev_stage_count * 4
        if (memory >> 9) & 1:
            projection_offset += 8
        if (memory >> 10) & 1:
            projection_offset += 4
        if (memory >> 12) & 1:
            projection_offset += 4
        if (memory >> 14) & 1:
            projection_offset += 12

        projection_sources = []
        coord_count = (memory >> 4) & 0x3
        for coord_index in range(coord_count):
            coord = texture_coord[coord_index * 8:(coord_index + 1) * 8]
            if len(coord) >= 2 and coord[1] in (3, 4, 5):
                projection_sources.append(coord[1])

        for index, source in enumerate(projection_sources[:projection_count]):
            flag_offset = projection_offset + index * 20 + 16
            if flag_offset + 4 <= len(rem):
                original_projection_flag = remainder[flag_offset:flag_offset + 4]
                switch_projection_flag = next((byte for byte in original_projection_flag if byte), 0)
                if (
                    not switch_projection_flag
                    and original_memory == 0x1C4BF
                    and projection_sources[:projection_count] == [3, 3, 4]
                    and index == 2
                    and source == 4
                ):
                    switch_projection_flag = 4
                if (
                    not switch_projection_flag
                    and original_memory == 0x8015
                    and texture_maps[2:4] == b'\x04\x04'
                    and source == 4
                    and texture_srt[:8] != b'\0\0\0\0\0\0\0\0'
                ):
                    switch_projection_flag = 4
                rem[flag_offset:flag_offset + 4] = bytes([switch_projection_flag]) + b'\0\0\0' if switch_projection_flag else b'\0\0\0\0'

    if original_memory == 0x806A and color_2 == b'\xFF' * 4:
        head0 = _bf_read_u32(texture_maps, 0, 'big') if len(texture_maps) >= 4 else None
        head1 = _bf_read_u32(texture_srt, 0, 'big') if len(texture_srt) >= 4 else None
        second_map_is_projection = (
            len(texture_maps) >= 8
            and _bf_read_u32(texture_maps, 4, 'big') == 0x00010404
            and len(texture_coord) >= 16
            and texture_coord[9] == 4
        )
        if (
            head0 in (0, 0x00020000)
            and (
                head1 is not None
                and ((head1 >> 16) & 0xFFFF) in (1, 7)
                and (head1 & 0xFFFF) == 0x0404
                or second_map_is_projection
            )
        ):
            memory = 0x1006A
            rem = bytearray(bytes(rem)[:-4] + b'\x01\0\0\0' + b'\0\0\0\0\0\0\0\0\0\0\x80\x3F\0\0\x80\x3F\0\0\0\0')

    return memory, bytes(rem)


def _bf_convert_mat1(body):
    count = _bf_read_u16(body, 0, 'big')
    offsets = [_bf_read_u32(body, 4 + i * 4, 'big') - 8 for i in range(count)]
    entries = []
    for i, start in enumerate(offsets):
        end = offsets[i + 1] if i + 1 < len(offsets) else len(body)
        chunk = body[start:end]
        name = chunk[:0x1C].split(b'\0', 1)[0]
        color_1 = chunk[0x1C:0x20]
        color_2 = chunk[0x20:0x24]
        memory = _bf_read_u32(chunk, 0x24, 'big')
        tail = chunk[0x28:]
        counts = _bf_material_counts(memory)
        cursor = 0
        tex_maps = tail[cursor:cursor + counts['texture_maps'] * 4]; cursor += counts['texture_maps'] * 4
        tex_srt = tail[cursor:cursor + counts['texture_srt'] * 20]; cursor += counts['texture_srt'] * 20
        tex_coord = tail[cursor:cursor + counts['texture_coord_gen'] * 8]; cursor += counts['texture_coord_gen'] * 8
        tex_ext = tail[cursor:cursor + counts['texture_extensions'] * 4]; cursor += counts['texture_extensions'] * 4
        original_memory = memory
        memory, rem = _bf_convert_material_remainder(tail[cursor:], memory, color_2, tex_maps, tex_srt, tex_coord)
        if original_memory == 0x806A and memory == 0x1006A and len(tex_coord) >= 16 and tex_coord[1] == 0 and tex_coord[9] == 4:
            tex_coord = bytearray(tex_coord)
            tex_coord[1] = 3
            tex_coord = bytes(tex_coord)
        counts = _bf_material_counts(memory)
        out = bytearray()
        out += name.ljust(0x1C, b'\0')
        out += _bf_write_u32(memory, 'little')
        out += b'\0\x02\x04\x08'
        out += color_1 + color_2
        out += _bf_convert_tex_maps(tex_maps, counts['texture_maps'])
        out += _bf_convert_tex_srt(tex_srt, counts['texture_srt'])
        out += _bf_convert_tex_coord(tex_coord, counts['texture_coord_gen'])
        out += tex_ext
        out += rem
        entries.append(bytes(out))

    out = bytearray()
    out += _bf_write_u16(count, 'little') + b'\0\0'
    running = 8 + 4 + 4 * count
    for entry in entries:
        out += _bf_write_u32(running, 'little')
        running += len(entry)
    out += b''.join(entries)
    return bytes(out)


def _bf_convert_txt1(body):
    if len(body) < 0x9C:
        return _bf_swap_words(body)
    text_offset = _bf_read_u32(body, 0x5C, 'big')
    label_offset = _bf_read_u32(body, 0x78, 'big')
    text_box_flag = body[0x56]
    has_per_character_transform = bool(text_box_flag & 0x10)
    a0_value = _bf_read_u32(body, 0x98, 'big') if has_per_character_transform else 0
    move_a0 = has_per_character_transform and label_offset == 0 and text_offset not in (0, 0xFFFFFFFF) and a0_value != 0
    if has_per_character_transform:
        raw = bytearray(_bf_write_u32(a0_value + 4 if move_a0 else a0_value, 'little') + body[0x9C:])
    else:
        raw = bytearray(body[0x98:])

    if text_offset not in (0, 0xFFFFFFFF):
        raw_text_start = text_offset - 0xA4 + 4
        text_end = raw_text_start
        while text_end + 1 < len(raw):
            if raw[text_end:text_end + 2] == b'\0\0':
                text_end += 2
                break
            text_end += 2
        text_blob = bytes(raw[raw_text_start:text_end])
        if not (text_blob[:1] == b'\x40' and all((48 <= b <= 57) or (65 <= b <= 90) or (97 <= b <= 122) or b in b'_.-/' for b in text_blob[1:].split(b'\0', 1)[0])):
            try:
                raw[raw_text_start:text_end] = text_blob.decode('utf-16-be').encode('utf-16-le')
            except UnicodeDecodeError:
                pass

    if has_per_character_transform and a0_value:
        pct_body_start = a0_value - 8
        pct_raw_start = pct_body_start - 0x9C + 4
        if 0 <= pct_raw_start and pct_raw_start + 12 <= len(raw):
            pct = raw[pct_raw_start:pct_raw_start + 12]
            converted_pct = bytearray(pct[:4][::-1] + pct[4:8][::-1] + pct[8:12])
            replace_size = 12
            if pct[10] and pct_body_start + 12 < len(body):
                try:
                    anim_info = FLAN.AnimationBlock.AnimationContent.AnimationInfo(body, pct_body_start + 12, '>')
                    original_anim_info = anim_info.save('>')
                    converted_pct += anim_info.save('<')
                    replace_size += len(original_anim_info)
                except Exception:
                    pass
            raw[pct_raw_start:pct_raw_start + replace_size] = converted_pct

    out = bytearray()
    out += body[:0x24]
    out += body[0x24:0x28][::-1] + body[0x28:0x2C][::-1]
    out += body[0x2C:0x38]
    for offset in (0x38, 0x3C, 0x40, 0x44, 0x48):
        out += body[offset:offset + 4][::-1]
    for offset in range(0x4C, 0x54, 2):
        out += body[offset:offset + 2][::-1]
    bytes_54_58 = bytearray(body[0x54:0x58])
    bytes_54_58[2] |= 0x20
    out += bytes_54_58
    out += body[0x58:0x5C][::-1]
    out += _bf_write_u32(text_offset + 4 if text_offset not in (0, 0xFFFFFFFF) else text_offset, 'little')
    out += body[0x60:0x68]
    for offset in (0x68, 0x6C, 0x70, 0x74):
        out += body[offset:offset + 4][::-1]
    out += _bf_write_u32(label_offset + 4 if label_offset not in (0, 0xFFFFFFFF) else label_offset, 'little')
    for offset in (0x7C, 0x80, 0x84, 0x88):
        out += body[offset:offset + 4][::-1]
    out += body[0x8C:0x98]
    out += _bf_write_u32(0, 'little')
    out += raw
    return bytes(out)


def _bf_convert_usd1(body):
    count = _bf_read_u16(body, 0, 'big')
    out = bytearray(_bf_write_u16(count, 'little') + body[2:4])
    cursor = 4
    for _ in range(count):
        out += body[cursor:cursor + 4][::-1]
        out += body[cursor + 4:cursor + 8][::-1]
        out += body[cursor + 8:cursor + 10][::-1]
        out += body[cursor + 10:cursor + 12]
        cursor += 12
    tail = body[cursor:]
    converted_tail = bytearray()
    pos = 0
    while pos < len(tail):
        if (
            pos + 4 <= len(tail)
            and 0x20 <= tail[pos] <= 0x7E
            and tail[pos + 1:pos + 4] == b'\0\0\0'
        ):
            converted_tail += tail[pos:pos + 4][::-1]
            pos += 4
            continue
        if (
            pos + 4 <= len(tail)
            and 0x20 <= tail[pos] <= 0x7E
            and 0x20 <= tail[pos + 1] <= 0x7E
            and tail[pos + 2:pos + 4] == b'\0\0'
        ):
            converted_tail += tail[pos:pos + 4][::-1]
            pos += 4
            continue
        end = tail.find(b'\0', pos)
        token = tail[pos:] if end == -1 else tail[pos:end]
        if token and all((48 <= b <= 57) or (65 <= b <= 90) or (97 <= b <= 122) or b in b'_.-/' for b in token):
            size = len(token) if end == -1 else len(token) + 1
            converted_tail += tail[pos:pos + size]
            pos += size
        elif pos + 4 <= len(tail):
            converted_tail += tail[pos:pos + 4][::-1]
            pos += 4
        else:
            converted_tail += tail[pos:]
            break
    out += converted_tail
    return bytes(out)


def _bf_convert_section_bytes(chunk):
    tag = chunk[:4]
    size = _bf_read_u32(chunk, 4, 'big')
    out_tag, out_body = _bf_convert_section(tag, chunk[8:size])
    return _bf_wrap_section(out_tag, out_body, 'little'), size


def _bf_convert_prt1_suffix(data):
    valid_tags = {b'txt1', b'pic1', b'wnd1', b'pan1', b'bnd1', b'prt1', b'grp1', b'cnt1', b'usd1', b'pas1', b'pae1', b'grs1', b'gre1'}
    payload = bytearray()
    cursor = 0
    while cursor < len(data):
        if cursor + 8 <= len(data):
            tag = data[cursor:cursor + 4]
            size = _bf_read_u32(data, cursor + 4, 'big')
            if tag in valid_tags and 8 <= size <= len(data) - cursor:
                converted, _ = _bf_convert_section_bytes(data[cursor:cursor + size])
                payload += converted
                cursor += size
                continue
        tail = data[cursor:]
        if cursor == 0:
            token_end = tail.find(b'\0')
            token = tail if token_end == -1 else tail[:token_end]
            if token and all(0x20 <= b <= 0x7E for b in token):
                token_size = len(tail) if token_end == -1 else _bf_align(token_end + 1)
                payload += tail[:token_size]
                cursor += token_size
                continue
        if cursor + 4 <= len(data):
            if cursor + 4 == len(data) and data[cursor + 1:cursor + 4] == b'\0\0\0':
                payload += data[cursor:cursor + 4]
            else:
                payload += data[cursor:cursor + 4][::-1]
            cursor += 4
        else:
            payload += data[cursor:]
            break
    return bytes(payload)


def _bf_convert_prt1_tail(tail, property_count):
    data = bytes(0x60) + tail
    payload = bytearray()
    valid_tags = {b'txt1', b'pic1', b'wnd1', b'pan1', b'bnd1', b'prt1', b'grp1', b'cnt1', b'usd1', b'pas1', b'pae1', b'grs1', b'gre1'}
    txt1_offset = _bf_read_u32(data, 0x7C, 'big') if len(data) >= 0x80 else 0

    if (
        property_count == 2
        and len(data) >= 0xD0
        and all(0x20 <= byte <= 0x7E or byte == 0 for byte in data[0x60:0x78])
        and any(byte != 0 for byte in data[0x60:0x78])
        and txt1_offset >= 0x80
        and txt1_offset + 8 <= len(data)
        and data[txt1_offset:txt1_offset + 4] == b'txt1'
    ):
        prefix = bytearray(data[0x80:txt1_offset])
        for offset in range(0x80, txt1_offset, 4):
            value = _bf_read_u32(data, offset, 'big')
            if txt1_offset <= value < len(data):
                start = offset - 0x80
                prefix[start:start + 4] = _bf_write_u32(value + 4, 'little')
        payload += data[0x60:0x78]
        payload += data[0x78:0x7C]
        payload += data[0x7C:0x80][::-1]
        payload += prefix
        cursor = txt1_offset
        while cursor + 8 <= len(data):
            tag = data[cursor:cursor + 4]
            size = _bf_read_u32(data, cursor + 4, 'big')
            if tag not in valid_tags or size < 8 or cursor + size > len(data):
                break
            converted, _ = _bf_convert_section_bytes(data[cursor:cursor + size])
            payload += converted
            cursor += size
        payload += data[cursor:]
        return bytes(payload)

    cursor = 0x60
    named_entry_mode = (
        property_count > 0
        and len(data) >= cursor + 0x28
        and all(0x20 <= byte <= 0x7E or byte == 0 for byte in data[cursor:cursor + 0x18])
        and any(byte != 0 for byte in data[cursor:cursor + 0x18])
        and len(data) >= cursor + property_count * 0x28
    )
    zero_entry_mode = (
        property_count > 0
        and len(data) >= cursor + property_count * 0x28
        and all(byte == 0 for byte in data[cursor:cursor + property_count * 0x28])
    )

    if named_entry_mode or zero_entry_mode:
        entries = []
        offsets = []
        for _ in range(property_count):
            entry = data[cursor:cursor + 0x28]
            entries.append(entry)
            offsets.append(_bf_read_u32(entry, 0x1C, 'big'))
            cursor += 0x28

        nonzero_offsets = [offset for offset in offsets if offset]
        tail_start = min(nonzero_offsets) if nonzero_offsets else cursor
        gap = data[cursor:tail_start]
        sorted_offsets = sorted(nonzero_offsets)
        converted_blocks = {}
        original_sizes = {}
        for index, start in enumerate(sorted_offsets):
            end = sorted_offsets[index + 1] if index + 1 < len(sorted_offsets) else len(data)
            chunk = data[start:end]
            original_sizes[start] = end - start
            tag = chunk[:4]
            if tag in {b'lyt1', b'txl1', b'fnl1', b'mat1', b'pan1', b'bnd1', b'pic1', b'txt1', b'wnd1', b'prt1', b'grp1', b'cnt1', b'usd1', b'pas1', b'pae1', b'grs1', b'gre1'}:
                section_size = _bf_read_u32(chunk, 4, 'big')
                if 8 <= section_size <= len(chunk):
                    converted, _ = _bf_convert_section_bytes(chunk[:section_size])
                    converted_blocks[start] = converted + _bf_convert_prt1_suffix(chunk[section_size:])
                else:
                    converted, _ = _bf_convert_section_bytes(chunk)
                    converted_blocks[start] = converted
            else:
                converted_blocks[start] = chunk

        new_offsets = {}
        running_offset = tail_start
        for start in sorted_offsets:
            new_offsets[start] = running_offset
            running_offset += len(converted_blocks[start])

        def translate_tail_offset(value):
            if value == 0 or value < tail_start:
                return value
            delta = 0
            for start in sorted_offsets:
                if start >= value:
                    break
                delta += len(converted_blocks[start]) - original_sizes[start]
            return value + delta

        for entry in entries:
            payload += entry[:0x18]
            payload += entry[0x18:0x1C]
            original_offset = _bf_read_u32(entry, 0x1C, 'big')
            payload += _bf_write_u32(new_offsets[original_offset] if original_offset else 0, 'little')
            payload += _bf_write_u32(translate_tail_offset(_bf_read_u32(entry, 0x20, 'big')), 'little')
            payload += _bf_write_u32(translate_tail_offset(_bf_read_u32(entry, 0x24, 'big')), 'little')
        payload += gap
        if sorted_offsets:
            for start in sorted_offsets:
                payload += converted_blocks[start]
        else:
            payload += _bf_convert_prt1_suffix(data[cursor:])
    else:
        for _ in range(property_count):
            entry = data[cursor:cursor + 0x18]
            payload += entry[:8]
            payload += entry[8:12][::-1]
            payload += entry[12:16][::-1]
            payload += entry[16:20][::-1]
            cursor += 0x18
        payload += data[cursor:]
    return bytes(payload)


def _bf_convert_prt1(body):
    if len(body) < 0x58:
        return _bf_swap_words(body)
    property_count = _bf_read_u32(body, 0x4C, 'big')
    out = bytearray()
    out += _bf_convert_pane_base(body[:76])
    out += _bf_write_u32(property_count, 'little')
    out += body[0x50:0x54][::-1]
    out += body[0x54:0x58][::-1]
    out += _bf_convert_prt1_tail(body[0x58:], property_count)
    return bytes(out)


def _bf_convert_cnt1(body):
    if len(body) < 20:
        return _bf_swap_words(body)
    return (
        body[0:4][::-1]
        + body[4:8][::-1]
        + body[8:10][::-1]
        + body[10:12][::-1]
        + body[12:16][::-1]
        + body[16:20][::-1]
        + body[20:]
    )


def _bf_convert_wnd1(body):
    if len(body) < 0x7C:
        return _bf_swap_words(body)
    frame_num = body[0x5C]
    frame_offset_table_offset = _bf_read_u32(body, 0x64, 'big')
    out = bytearray(_bf_convert_pane_base(body[:76]))
    out += body[0x4C:0x54]
    for offset in range(0x54, 0x5C, 2):
        out += body[offset:offset + 2][::-1]
    out += body[0x5C:0x60]
    out += body[0x60:0x64][::-1]
    out += body[0x64:0x68][::-1]
    out += body[0x68:0x78]
    out += body[0x78:0x7A][::-1]
    out += body[0x7A:0x7C]
    trailing = body[0x7C:]
    pos = 0
    while pos + 4 <= len(trailing):
        word = trailing[pos:pos + 4]
        if word[0] == 0 and word[1] != 0 and word[2:4] == b'\0\0':
            out += word[:2][::-1] + word[2:4]
        else:
            out += word[::-1]
        pos += 4
    out += trailing[pos:]
    # Window frame entries are not u32 values: they are materialIdx(u16),
    # textureFlip(u8), padding(u8). Fix them after the generic tail pass.
    for i in range(frame_num):
        table_pos = frame_offset_table_offset - 8 + i * 4
        if table_pos + 4 > len(body):
            break
        frame_pos = _bf_read_u32(body, table_pos, 'big') - 8
        if frame_pos + 4 > len(body):
            continue
        out[frame_pos:frame_pos + 4] = (
            body[frame_pos:frame_pos + 2][::-1]
            + body[frame_pos + 2:frame_pos + 4]
        )
    return bytes(out)


def _bf_convert_grp1(body):
    if len(body) < 0x24:
        return body
    return body[:0x22] + body[0x22:0x24][::-1] + body[0x24:]


def _bf_convert_section(tag, body):
    if tag == b'lyt1':
        return tag, _bf_convert_lyt(body)
    if tag in (b'pan1', b'bnd1'):
        return tag, _bf_convert_pane_base(body)
    if tag == b'pic1':
        return tag, _bf_convert_pane_base(body[:76]) + body[76:0x5C] + body[0x5C:0x5E][::-1] + body[0x5E:0x60] + _bf_swap_words(body[0x60:])
    if tag == b'wnd1':
        return tag, _bf_convert_wnd1(body)
    if tag == b'txt1':
        return tag, _bf_convert_txt1(body)
    if tag == b'prt1':
        return tag, _bf_convert_prt1(body)
    if tag == b'txl1':
        return tag, _bf_convert_named_table(body, True)
    if tag == b'fnl1':
        return tag, _bf_convert_named_table(body, False)
    if tag == b'mat1':
        return tag, _bf_convert_mat1(body)
    if tag == b'usd1':
        return tag, _bf_convert_usd1(body)
    if tag == b'cnt1':
        return tag, _bf_convert_cnt1(body)
    if tag == b'grp1':
        return tag, _bf_convert_grp1(body)
    if tag in (b'pas1', b'pae1', b'grs1', b'gre1'):
        return tag, b''
    raise ValueError(f"Unsupported BFLYT section {tag.decode('ascii', errors='replace')}")


def _bflyteu_to_switch(file, output, dVersion):
    layout = FLYT(file)
    layout.save_as_switch(output, dVersion)


def _printTex(*args, **kwargs):
    if printTexList:
        print(*args, **kwargs)


def _printFnt(*args, **kwargs):
    if printFntList:
        print(*args, **kwargs)


def _printMat(*args, **kwargs):
    if printMatInfo:
        print(*args, **kwargs)


def _printPan(*args, **kwargs):
    if printPanInfo:
        print(*args, **kwargs)


def readPane(file, pos, major, endian):
    if file[pos:pos + 4] == b'pan1':
        return FLYT.Pane(file, pos, endian)

    elif file[pos:pos + 4] == b'pic1':
        return FLYT.Picture(file, pos, endian)

    elif file[pos:pos + 4] == b'txt1':
        return FLYT.TextBox(file, pos, endian)

    elif file[pos:pos + 4] == b'wnd1':
        return FLYT.Window(file, pos, endian)

    elif file[pos:pos + 4] == b'bnd1':
        return FLYT.Bounding(file, pos, endian)

    elif file[pos:pos + 4] == b'prt1':
        return FLYT.Parts(file, pos, major, endian)

    else:
        raise NotImplementedError("Unknown pane type!")


class FLYT:
    class Layout(Section):
        def __init__(self, file, pos, endian):
            super().__init__(file, pos, endian)

            (self.originType,
             self.layoutWidth,
             self.layoutHeight,
             self.partsWidth,
             self.partsHeight) = struct.unpack_from(f'{endian}B3x4f', self.data)

            self.name = readString(self.data, 0x14)

    class Control(Section):
        def __init__(self, file, pos, major, endian):
            super().__init__(file, pos, endian)

            self.extUserDataList = None

            if major < 3:
                (self.controlFunctionalPaneNamesOffset,
                 self.controlFunctionalPaneNum,
                 self.controlFunctionalAnimNum) = struct.unpack_from(f'{endian}I2H', self.data); pos = 8

                self.controlUserNameOffset = 0
                self.controlFunctionalPaneParameterNameOffsetsOffset = 0
                self.controlFunctionalAnimParameterNameOffsetsOffset = 0

            else:
                (self.controlUserNameOffset,
                 self.controlFunctionalPaneNamesOffset,
                 self.controlFunctionalPaneNum,
                 self.controlFunctionalAnimNum,
                 self.controlFunctionalPaneParameterNameOffsetsOffset,
                 self.controlFunctionalAnimParameterNameOffsetsOffset) = struct.unpack_from(f'{endian}2I2H2I', self.data); pos = 20

            self.controlName = readString(self.data, pos)

            self.controlUserName = ''
            if self.controlUserNameOffset:
                pos = self.controlUserNameOffset - 8
                self.controlUserName = readString(self.data, pos)

            if not self.controlUserName:
                self.controlUserName = self.controlName

            self.controlFunctionalPaneNames = []
            if self.controlFunctionalPaneNamesOffset:
                pos = self.controlFunctionalPaneNamesOffset - 8
                for i in range(self.controlFunctionalPaneNum):
                    name = readString(struct.unpack_from(f'{endian}24s', self.data, pos)[0]); pos += 24
                    self.controlFunctionalPaneNames.append(name)

            else:
                pos -= 8

            self.controlFunctionalAnimNames = []
            for i in range(self.controlFunctionalAnimNum):
                pName = pos + struct.unpack_from(f'{endian}I', self.data, pos + 4*i)[0]
                self.controlFunctionalAnimNames.append(readString(self.data, pName))

            self.controlFunctionalPaneParameterNames = []
            if self.controlFunctionalPaneParameterNameOffsetsOffset:
                pos = self.controlFunctionalPaneParameterNameOffsetsOffset - 8
                for i in range(self.controlFunctionalPaneNum):
                    pName = pos + struct.unpack_from(f'{endian}I', self.data, pos + 4*i)[0]
                    self.controlFunctionalPaneParameterNames.append(readString(self.data, pName))

            self.controlFunctionalAnimParameterNames = []
            if self.controlFunctionalAnimParameterNameOffsetsOffset:
                pos = self.controlFunctionalAnimParameterNameOffsetsOffset - 8
                for i in range(self.controlFunctionalAnimNum):
                    pName = pos + struct.unpack_from(f'{endian}I', self.data, pos + 4*i)[0]
                    self.controlFunctionalAnimParameterNames.append(readString(self.data, pName))

            if major < 3:
                self.controlFunctionalPaneParameterNames = self.controlFunctionalPaneNames
                self.controlFunctionalAnimParameterNames = self.controlFunctionalAnimNames

        def save(self, major, endian='>'):
            controlFunctionalPaneNum = len(self.controlFunctionalPaneNames)
            controlFunctionalAnimNum = len(self.controlFunctionalAnimNames)
            buff1 = self.controlName.encode('utf-8') + b'\0'

            controlUserNameOffset = len(buff1)
            alignLen = roundUp(controlUserNameOffset, 4) - controlUserNameOffset

            controlUserNameOffset += alignLen
            buff1 += b'\0' * alignLen

            controlFunctionalPaneNamesOffset = len(buff1)
            if self.controlUserName and major >= 3:
                buff1 += self.controlUserName.encode('utf-8') + b'\0'

                controlFunctionalPaneNamesOffset = len(buff1)
                alignLen = roundUp(controlFunctionalPaneNamesOffset, 4) - controlFunctionalPaneNamesOffset

                controlFunctionalPaneNamesOffset += alignLen
                buff1 += b'\0' * alignLen

            buff2 = bytearray()
            for name in self.controlFunctionalPaneNames:
                buff2 += struct.pack(f'{endian}24s', name.encode('utf-8'))

            buff3 = bytearray()

            controlFunctionalAnimNamesOffsets = []
            controlFunctionalAnimNamesTableSize = 4 * controlFunctionalAnimNum
            for name in self.controlFunctionalAnimNames:
                controlFunctionalAnimNamesOffsets.append(controlFunctionalAnimNamesTableSize + len(buff3))
                buff3 += name.encode('utf-8')
                buff3.append(0)

            buff4 = struct.pack(f'{endian}{controlFunctionalAnimNum}I', *controlFunctionalAnimNamesOffsets)

            if major < 3:
                controlUserNameOffset += 16
                controlFunctionalPaneNamesOffset += 16

                buff5 = struct.pack(
                    f'{endian}I2H',
                    controlFunctionalPaneNamesOffset,
                    controlFunctionalPaneNum,
                    controlFunctionalAnimNum,
                )

                self.data = b''.join([buff5, buff1, buff2, buff4, buff3])
                self.data += b'\0' * ((_bf_align(len(self.data)) - len(self.data)) % 4)

                return super().save(endian)

            else:
                controlFunctionalPaneParameterNameOffsetsOffset = len(buff1) + len(buff2) + len(buff4) + len(buff3)
                alignLen = roundUp(controlFunctionalPaneParameterNameOffsetsOffset, 4) - controlFunctionalPaneParameterNameOffsetsOffset

                controlFunctionalPaneParameterNameOffsetsOffset += alignLen
                buff3 += b'\0' * alignLen

                buff6 = bytearray()

                controlFunctionalPaneParameterNameOffsets = []
                controlFunctionalPaneParameterNameOffsetsTableSize = 4 * controlFunctionalPaneNum
                for name in self.controlFunctionalPaneParameterNames:
                    controlFunctionalPaneParameterNameOffsets.append(controlFunctionalPaneParameterNameOffsetsTableSize + len(buff6))
                    buff6 += name.encode('utf-8')
                    buff6.append(0)

                buff7 = struct.pack(f'{endian}{controlFunctionalPaneNum}I', *controlFunctionalPaneParameterNameOffsets)

                controlFunctionalAnimParameterNameOffsetsOffset = controlFunctionalPaneParameterNameOffsetsOffset + len(buff7) + len(buff6)
                alignLen = roundUp(controlFunctionalAnimParameterNameOffsetsOffset, 4) - controlFunctionalAnimParameterNameOffsetsOffset

                controlFunctionalAnimParameterNameOffsetsOffset += alignLen
                buff6 += b'\0' * alignLen

                buff8 = bytearray()

                controlFunctionalAnimParameterNameOffsets = []
                controlFunctionalAnimParameterNameOffsetsTableSize = 4 * controlFunctionalAnimNum
                for name in self.controlFunctionalAnimParameterNames:
                    controlFunctionalAnimParameterNameOffsets.append(controlFunctionalAnimParameterNameOffsetsTableSize + len(buff8))
                    buff8 += name.encode('utf-8')
                    buff8.append(0)

                buff9 = struct.pack(f'{endian}{controlFunctionalAnimNum}I', *controlFunctionalAnimParameterNameOffsets)

                controlUserNameOffset += 28
                controlFunctionalPaneNamesOffset += 28
                controlFunctionalPaneParameterNameOffsetsOffset += 28
                controlFunctionalAnimParameterNameOffsetsOffset += 28

                buff5 = struct.pack(
                    f'{endian}2I2H2I',
                    controlUserNameOffset,
                    controlFunctionalPaneNamesOffset,
                    controlFunctionalPaneNum,
                    controlFunctionalAnimNum,
                    controlFunctionalPaneParameterNameOffsetsOffset,
                    controlFunctionalAnimParameterNameOffsetsOffset,
                )

                self.data = b''.join([buff5, buff1, buff2, buff4, buff3, buff7, buff6, buff9, buff8])
                self.data += b'\0' * ((_bf_align(len(self.data)) - len(self.data)) % 4)

                return super().save(endian)

    class TextureList(Section):
        def __init__(self, file, pos, endian):
            super().__init__(file, pos, endian)

            self.texNum = struct.unpack_from(f'{endian}H', self.data)[0]
            self.textures = []
            self.formats = []

            if self.texNum:
                _printTex("Textures:")

            for i in range(self.texNum):
                pTexture = struct.unpack_from(f'{endian}I', self.data, 4 * (i+1))[0] + 4
                texture = readString(self.data, pTexture)
                format = ""

                if texture.endswith(".bflim"):
                    texture = texture[:-6]

                if len(texture) > 2:
                    if texture[-1] in 'abcdefghijklmnopqrstu' and texture[-2] in '^+':
                        format = texture[-2:]
                        texture = texture[:-2]

                _printTex(texture)
                self.textures.append(texture)
                self.formats.append(format)

            if self.texNum:
                _printTex()

    class FontList(Section):
        def __init__(self, file, pos, endian):
            super().__init__(file, pos, endian)

            self.fontNum = struct.unpack_from(f'{endian}H', self.data)[0]
            self.fonts = []

            if self.fontNum:
                _printFnt("Fonts:")

            for i in range(self.fontNum):
                pFont = struct.unpack_from(f'{endian}I', self.data, 4 * (i+1))[0] + 4
                font = readString(self.data, pFont)

                assert font[-6:] == ".bffnt"
                font = font[:-6]

                _printFnt(font)
                self.fonts.append(font)

            if self.fontNum:
                _printFnt()

    class MaterialList(Section):
        class Material:
            class TexMap:
                def __init__(self, file, pos, endian):
                    (self.texIdx,
                     self.wrapSflt,
                     self.wrapTflt) = struct.unpack_from(f'{endian}H2B', file, pos)

                    _texWrap = ["Clamp", "Repeat", "Mirror"]
                    _texFilter = ["Near", "Linear"]

                    # wrapSflt -> 0000FFWW
                    self.wrapS = self.wrapSflt & 3
                    self.minFilter = (self.wrapSflt >> 2) & 3

                    # wrapTflt -> 0000FFWW
                    self.wrapT = self.wrapTflt & 3
                    self.magFilter = (self.wrapTflt >> 2) & 3

                    _printMat("Texture Index: %d" % self.texIdx)
                    _printMat("Wrap S: %s" % _texWrap[self.wrapS])
                    _printMat("Wrap T: %s" % _texWrap[self.wrapT])
                    _printMat("Min Filter: %s" % _texFilter[self.minFilter])
                    _printMat("Mag Filter: %s" % _texFilter[self.magFilter])

            class TexSRT:
                def __init__(self, file, pos, endian):
                    self.translate = struct.unpack_from(f'{endian}2f', file, pos); pos += 8
                    self.rotate = struct.unpack_from(f'{endian}f', file, pos); pos += 4
                    self.scale = struct.unpack_from(f'{endian}2f', file, pos); pos += 8

                    _printMat("Texture Translation:", self.translate)
                    _printMat("Texture Rotation:", self.rotate)
                    _printMat("Texture Scale:", self.scale)

            class TexCoordGen:
                def __init__(self, file, pos, endian):
                    _texGenTypes = ["Matrix2x4"]
                    _texGenSrcs = [
                        "Tex0", "Tex1", "Tex2", "Orthogonal Projection",
                        "Pane-Based Projection", "Perspective Projection",
                    ]

                    (self.texGenType,
                     self.texGenSrc) = struct.unpack_from(f'{endian}2B2x', file, pos); pos += 4

                    self.projectionTexGenParameter = None

                    _printMat("Texture Coordinate Generation Type: %s" % _texGenTypes[self.texGenType])
                    _printMat("Texture Coordinate Generation Source: %s" % _texGenSrcs[self.texGenSrc])

            class TevStage:
                def __init__(self, file, pos, endian):
                    _tevModes = [
                        "Replace", "Modulate", "Add", "Add Signed",
                        "Interpolate", "Subtract", "Add Multiplicate", "Multiplicate Add",
                        "Overlay", "Indirect", "Blend Indirect", "Each Indirect",
                    ]

                    (self.combineRgb,
                     self.combineAlpha) = struct.unpack_from(f'{endian}2B2x', file, pos); pos += 4

                    _printMat("Tev Combine RGB Mode: %s" % _tevModes[self.combineRgb])
                    _printMat("Tev Combine Alpha Mode: %s" % _tevModes[self.combineAlpha])

            class AlphaCompare:
                def __init__(self, file, pos, endian):
                    _funcs = [
                        "Never", "Less", "Less or Equal", "Equal",
                        "Not Equal", "Greater or Equal", "Greater", "Always",
                    ]

                    (self.func,
                     self.ref) = struct.unpack_from(f'{endian}Bf', file, pos); pos += 5

                    _printMat("Alpha Compare function: %s" % _funcs[self.func])
                    _printMat("Alpha Compare: %f" % self.ref)

            class BlendMode:
                def __init__(self, file, pos, endian, isAlpha=False):
                    _factors = [
                        "0", "1", "Destination Color", "Destination Inverse Color", "Source Alpha",
                        "Source Inverse Alpha", "Destination Alpha", "Destination Inverse Alpha",
                        "Source Color", "Source Inverse Color",
                    ]

                    _blendOp = [
                        "Disable", "Add", "Subtract", "Reverse Subtract",
                        "Select Min", "Select Max",
                    ]

                    _logicOp = [
                        "Disable", "No Op", "Clear", "Set", "Copy",
                        "InvCopy", "Inv", "And", "Nand", "Or",
                        "Nor", "Xor", "Equiv", "RevAnd",
                        "InvAnd", "RevOr", "InvOr",
                    ]

                    (self.blendOp,
                     self.srcFactor,
                     self.dstFactor,
                     self.logicOp) = struct.unpack_from(f'{endian}4B', file, pos); pos += 4

                    if isAlpha:
                        _printMat("Alpha:")

                    _printMat("Blend Op: %s" % _blendOp[self.blendOp])
                    _printMat("Source factor: %s" % _factors[self.srcFactor])
                    _printMat("Destination factor: %s" % _factors[self.dstFactor])
                    _printMat("Logic Op: %s" % _logicOp[self.logicOp])

            class IndirectParameter:
                def __init__(self, file, pos, endian):
                    self.rotate = struct.unpack_from(f'{endian}f', file, pos); pos += 4
                    self.scale = struct.unpack_from(f'{endian}2f', file, pos); pos += 8

                    _printMat("Indirect Rotation:", self.rotate)
                    _printMat("Indirect Scale:", self.scale)

            class ProjectionTexGenParamaters:
                def __init__(self, file, pos, endian):
                    self.translate = struct.unpack_from(f'{endian}2f', file, pos); pos += 8
                    self.scale = struct.unpack_from(f'{endian}2f', file, pos); pos += 8
                    flag = file[pos]; pos += 4

                    self.isFittingLayoutSize = bool(flag & 1)
                    self.isFittingPaneSizeEnabled = bool(flag & 2)
                    self.isAdjustProjectionSREnabled = bool(flag & 4)

                    _printMat("Projection Translation:", self.translate)
                    _printMat("Projection Scale:", self.scale)
                    _printMat("Projection isFittingLayoutSize:", self.isFittingLayoutSize)
                    _printMat("Projection isFittingPaneSizeEnabled:", self.isFittingPaneSizeEnabled)
                    _printMat("Projection isAdjustProjectionSREnabled:", self.isAdjustProjectionSREnabled)

            class FontShadowParameter:
                def __init__(self, file, pos, endian):
                    self.blackInterporateColor = struct.unpack_from(f'{endian}3B', file, pos); pos += 3
                    self.whiteInterporateColor = struct.unpack_from(f'{endian}4B', file, pos); pos += 5

                    _printMat("Font Shadow Black Interporate Color:", self.blackInterporateColor)
                    _printMat("Font Shadow White Interporate Color:", self.whiteInterporateColor)

            def __init__(self, file, pos, endian):
                try:
                    self.name = readString(file[pos:pos + 28])
                    pos += 28

                except:
                    _printMat(hex(pos))
                    raise Exception from None

                if not self.name:
                    _printMat(hex(pos - 28))
                    raise Exception from None

                if endian == '<':
                    self.resNum = struct.unpack_from(f'{endian}I', file, pos)[0]; pos += 4
                    self.switchHeader = file[pos:pos + 4]; pos += 4
                    self.color0 = struct.unpack_from(f'{endian}4B', file, pos); pos += 4
                    self.color1 = struct.unpack_from(f'{endian}4B', file, pos); pos += 4
                else:
                    self.color0 = struct.unpack_from(f'{endian}4B', file, pos); pos += 4
                    self.color1 = struct.unpack_from(f'{endian}4B', file, pos); pos += 4
                    self.resNum = struct.unpack_from(f'{endian}I', file, pos)[0]; pos += 4
                self.readResNum()

                _printMat('\n%s' % self.name)
                _printMat("Black Color:", self.color0)
                _printMat("White Color:", self.color1)
                _printMat("Resource Flags: %s" % bin(self.resNum))

                self.resTexMaps = []
                for i in range(self.texNum):
                    self.resTexMaps.append(self.TexMap(file, pos, endian))
                    pos += 4

                self.texSRTs = []
                for i in range(self.texSRTNum):
                    self.texSRTs.append(self.TexSRT(file, pos, endian))
                    pos += 20

                self.texCoordGen = []
                for i in range(self.texCoordGenNum):
                    self.texCoordGen.append(self.TexCoordGen(file, pos, endian))
                    pos += 16 if endian == '<' else 8

                if endian == '<':
                    pos += self.textureExtensionNum * 4

                self.tevStages = []
                for i in range(self.tevStageNum):
                    self.tevStages.append(self.TevStage(file, pos, endian))
                    pos += 4

                self.alphaCompare = None
                if self.hasAlphaCompare:
                    self.alphaCompare = self.AlphaCompare(file, pos, endian)
                    pos += 8

                self.blendType = "None"

                self.blendMode = None
                if self.hasBlendMode:
                    self.blendType = "Blend"
                    self.blendMode = self.BlendMode(file, pos, endian)
                    pos += 4

                self.blendModeAlpha = None
                if self.isSeparateBlendMode:
                    self.blendType = "Logic"
                    self.blendModeAlpha = self.BlendMode(file, pos, endian, True)
                    pos += 4

                self.indirectParameter = None
                if self.hasIndirectParameter:
                    self.indirectParameter = self.IndirectParameter(file, pos, endian)
                    pos += 12

                self.projectionTexGenParameters = []
                for i in range(self.projectionTexGenNum):
                    self.projectionTexGenParameters.append(self.ProjectionTexGenParamaters(file, pos, endian))
                    pos += 20

                if self.hasFontShadowParameter:
                    self.fontShadowParameter = self.FontShadowParameter(file, pos, endian)
                    pos += 8

                numProjectionTexGen = 0
                for i in range(self.texCoordGenNum):
                    if self.texCoordGen[i].texGenSrc in [3, 4, 5]:
                        self.texCoordGen[i].projectionTexGenParameter = self.projectionTexGenParameters[numProjectionTexGen]
                        numProjectionTexGen += 1

            def readResNum(self):
                # resNum -> 0000000000000HFPPI0bTBAVVVCCSSMM
                self.texNum = self.resNum & 3
                self.texSRTNum = (self.resNum >> 2) & 3
                self.texCoordGenNum = (self.resNum >> 4) & 3
                self.tevStageNum = (self.resNum >> 6) & 7
                self.hasAlphaCompare = bool((self.resNum >> 9) & 1)
                self.hasBlendMode = bool((self.resNum >> 10) & 1)
                self.isTextureOnly = bool((self.resNum >> 11) & 1)
                self.isSeparateBlendMode = bool((self.resNum >> 12) & 1)
                self.hasIndirectParameter = bool((self.resNum >> 14) & 1)
                self.projectionTexGenNum = (self.resNum >> 15) & 3
                self.hasFontShadowParameter = bool((self.resNum >> 17) & 1)
                self.isThresholdingAlphaInterpolation = bool((self.resNum >> 18) & 1)
                self.textureExtensionNum = (self.resNum >> 21) & 1

        def __init__(self, file, pos, endian):
            super().__init__(file, pos, endian); pos += 8

            self.materialNum = struct.unpack_from(f'{endian}H', file, pos)[0]
            self.materials = []

            for i in range(self.materialNum):
                pMaterial = struct.unpack_from(f'{endian}I', file, pos + 4 * (i+1))[0] + pos - 8
                self.materials.append(self.Material(file, pMaterial, endian))

    class Pane(Section):
        def __init__(self, file, pos, endian):
            super().__init__(file, pos, endian); pos += 8

            _xModes = {
                0: "Center",
                1: "Left",
                2: "Right",
            }

            _yModes = {
                0: "Center",
                1: "Top",
                2: "Bottom",
            }

            self.extUserDataList = None

            (self.flag,
             self.basePosition,
             self.alpha,
             self.flagEx,
             nameBytes,
             userDataBytes) = struct.unpack_from(f'{endian}4B24s8s', file, pos); pos += 36

            self.name = readString(nameBytes)
            self.userData = readString(userDataBytes)

            _printPan("\nPane name: %s" % self.name)
            _printPan("Pane visible:", bool(self.flag & 1))
            _printPan("Pane influenced alpha:", bool((self.flag >> 1) & 1))
            _printPan("Pane location adjust:", bool((self.flag >> 2) & 1))
            _printPan("Pane hidden (in editor?):", bool((self.flag >> 7) & 1))
            ignorePartsMagnify = bool(self.flagEx & 1)
            _printPan("Pane ignore parts magnify:", ignorePartsMagnify)
            if not ignorePartsMagnify:
                _printPan("Pane parts magnify influence:", bool((self.flagEx >> 1) & 1))

            parentRelativeY = (self.basePosition >> 6) & 3
            parentRelativeX = (self.basePosition >> 4) & 3
            baseY = (self.basePosition >> 2) & 3
            baseX = self.basePosition & 3

            _printPan("Pane base X position:", _xModes[baseX])
            _printPan("Pane base Y position:", _xModes[baseY])
            _printPan("Pane parent-relative X position:", _xModes[parentRelativeX])
            _printPan("Pane parent-relative Y position:", _xModes[parentRelativeY])

            self.translate = struct.unpack_from(f'{endian}3f', file, pos); pos += 12
            self.rotate = struct.unpack_from(f'{endian}3f', file, pos); pos += 12
            self.scale = struct.unpack_from(f'{endian}2f', file, pos); pos += 8
            self.size = struct.unpack_from(f'{endian}2f', file, pos); pos += 8

            _printPan("Pane translation:", self.translate)
            _printPan("Pane rotation:", self.rotate)
            _printPan("Pane scale:", self.scale)
            _printPan("Pane size:", self.size)

            self.parent = None
            self.childList = []

        def appendChild(self, pane):
            self.childList.append(pane)
            pane.parent = self

        def getChildren(self):
            childList = []

            for child in self.childList:
                childList.append(child)
                if child.childList:
                    childList.extend(child.getChildren())

            return childList

        def getAsTreeDict(self):
            childList = []

            for child in self.childList:
                if child.childList:
                    childList.append(child.getAsTreeDict())

                else:
                    childList.append(child.name)
                
            return {self.name: childList}

    class Picture(Pane):
        def __init__(self, file, pos, endian):
            super().__init__(file, pos, endian); pos += 84
            self.vtxCols = [struct.unpack_from(f'{endian}4B', file, pos + 4*i) for i in range(4)]; pos += 16

            (self.materialIdx,
             self.texCoordNum) = struct.unpack_from(f'{endian}HB', file, pos); pos += 4

            self.texCoords = []
            for _ in range(self.texCoordNum):
                self.texCoords.append([struct.unpack_from(f'{endian}2f', file, pos + 8*z) for z in range(4)]); pos += 32

    class TextBox(Pane):
        class PerCharacterTransform:
            def __init__(self, file, pos, endian):
                (self.evalTimeOffset,
                 self.evalTimeWidth,
                 self.loopType,
                 self.originV,
                 self.hasAnimationInfo) = struct.unpack_from(f'{endian}2f3Bx', file, pos)

        def __init__(self, file, pos, endian):
            initPos = pos
            super().__init__(file, pos, endian); pos += 84

            (self.textBufBytes,
             self.textStrBytes,
             self.materialIdx,
             self.fontIdx,
             self.textPosition,
             self.textAlignment,
             self.textBoxFlag,
             self.italicRatio,
             self.textStrOffset) = struct.unpack_from(f'{endian}4H3BxfI', file, pos); pos += 20

            self.readTextBoxFlag()

            self.textCols = [struct.unpack_from(f'{endian}4B', file, pos + 4*i) for i in range(2)]; pos += 8
            self.fontSize = struct.unpack_from(f'{endian}2f', file, pos); pos += 8

            (self.charSpace,
             self.lineSpace,
             self.textIDOffset) = struct.unpack_from(f'{endian}2fI', file, pos); pos += 12

            self.shadowOffset = struct.unpack_from(f'{endian}2f', file, pos); pos += 8
            self.shadowScale = struct.unpack_from(f'{endian}2f', file, pos); pos += 8
            self.shadowCols = [struct.unpack_from(f'{endian}4B', file, pos + 4*i) for i in range(2)]; pos += 8

            (self.shadowItalicRatio,
             self.perCharacterTransformOffset) = struct.unpack_from(f'{endian}fI', file, pos); pos += 8

            text_encoding = 'utf-16le' if endian == '<' else 'utf-16be'
            pos = initPos + self.textStrOffset
            if self.forceAssignTextLength:
                self.text = readString(file[pos:pos + self.textStrBytes], 0, 2, text_encoding)

            else:
                self.text = readString(file[pos:pos + self.textBufBytes], 0, 2, text_encoding)

            pos = initPos + self.textIDOffset
            self.textID = readString(file, pos)

            self.perCharacterTransform = None
            if self.perCharacterTransformEnabled:
                if self.perCharacterTransformOffset:
                    pos = initPos + self.perCharacterTransformOffset
                    self.perCharacterTransform = self.PerCharacterTransform(file, pos, endian); pos += 12
                    if self.perCharacterTransform.hasAnimationInfo:
                        self.perCharacterTransformAnimationInfo = FLAN.AnimationBlock.AnimationContent.AnimationInfo(file, pos, endian)

                else:
                    print("Whoopsie-daisy")
                    self.perCharacterTransformEnabled = False

        def readTextBoxFlag(self):
            self.shadowEnabled = bool(self.textBoxFlag & 1)
            self.forceAssignTextLength = bool((self.textBoxFlag >> 1) & 1)
            self.invisibleBorderEnabled = bool((self.textBoxFlag >> 2) & 1)
            self.doubleDrawnBorderEnabled = bool((self.textBoxFlag >> 3) & 1)
            self.perCharacterTransformEnabled = bool((self.textBoxFlag >> 4) & 1)

    class Window(Pane):
        class WindowContent:
            def __init__(self, file, pos, endian):
                self.vtxCols = [struct.unpack_from(f'{endian}4B', file, pos + 4*i) for i in range(4)]; pos += 16

                (self.materialIdx,
                 self.texCoordNum) = struct.unpack_from(f'{endian}HB', file, pos); pos += 4

                self.texCoords = []
                for _ in range(self.texCoordNum):
                    self.texCoords.append([struct.unpack_from(f'{endian}2f', file, pos + 8*z) for z in range(4)]); pos += 32

        class WindowFrame:
            def __init__(self, file, pos, endian):
                _modes = [
                    "None", "Flip Horizontal", "Flip Vertical", "Rotate 90",
                    "Rotate 180", "Rotate 270",
                ]

                (self.materialIdx,
                 self.textureFlip) = struct.unpack_from(f'{endian}HB', file, pos)

        def __init__(self, file, pos, endian):
            initPos = pos
            super().__init__(file, pos, endian); pos += 84

            self.inflation = struct.unpack_from(f'{endian}4h', file, pos); pos += 8
            self.frameSize = struct.unpack_from(f'{endian}4H', file, pos); pos += 8

            (self.frameNum,
             self.windowFlags,
             self.contentOffset,
             self.frameOffsetTableOffset) = struct.unpack_from(f'{endian}2B2x2I', file, pos); pos += 12

            self.readWindowFlags()

            pos = initPos + self.contentOffset
            self.content = self.WindowContent(file, pos, endian)

            self.frames = []
            for i in range(self.frameNum):
                pos = initPos + self.frameOffsetTableOffset + 4*i
                pos = initPos + struct.unpack_from(f'{endian}I', file, pos)[0]
                self.frames.append(self.WindowFrame(file, pos, endian))

        def readWindowFlags(self):
            self.useOneMaterialForAll = bool(self.windowFlags & 1)
            self.useVtxColAll = bool((self.windowFlags >> 1) & 1)
            self.windowKind = (self.windowFlags >> 2) & 3
            self.notDrawContent = bool((self.windowFlags >> 4) & 1)

    class Bounding(Pane):
        pass

    class Parts(Pane):
        class PartsProperty:
            class PartsPaneBasicInfo:
                def __init__(self, file, pos, major, endian):
                    self.userData = readString(struct.unpack_from(f'{endian}8s', file, pos)[0]); pos += 8
                    self.translate = struct.unpack_from(f'{endian}3f', file, pos); pos += 12
                    self.rotate = struct.unpack_from(f'{endian}3f', file, pos); pos += 12
                    self.scale = struct.unpack_from(f'{endian}2f', file, pos); pos += 8
                    self.size = struct.unpack_from(f'{endian}2f', file, pos); pos += 8
                    self.alpha = None

                    if major > 2:
                        self.alpha = file[pos]

            def __init__(self, file, initPos, pos, major, endian):
                (nameBytes,
                 self.usageFlag,
                 self.basicUsageFlag,
                 self.propertyOffset,
                 self.extUserDataOffset,
                 self.paneBasicInfoOffset) = struct.unpack_from(f'{endian}24s2B2x3I', file, pos)

                self.name = readString(nameBytes)

                self.property = None
                if self.propertyOffset:
                    self.property = readPane(file, initPos + self.propertyOffset, major, endian)

                self.extUserDataList = None
                if self.extUserDataOffset not in [0, 1]:
                    self.extUserDataList = FLYT.ExtUserDataList(file, initPos + self.extUserDataOffset, endian)

                self.basicInfo = None
                if self.paneBasicInfoOffset:
                    self.basicInfo = self.PartsPaneBasicInfo(file, initPos + self.paneBasicInfoOffset, major, endian)

        def __init__(self, file, pos, major, endian):
            initPos = pos
            super().__init__(file, pos, endian); pos += 84

            self.propertyNum, = struct.unpack_from(f'{endian}I', file, pos); pos += 4
            self.magnify = struct.unpack_from(f'{endian}2f', file, pos); pos += 8

            self.properties = []
            for i in range(self.propertyNum):
                property = self.PartsProperty(file, initPos, pos, major, endian); pos += 40
                if property.name:
                    self.properties.append(property)

            self.filename = readString(file, pos)
            assert self.filename

    class ExtUserDataList(Section):
        class ExtUserData:
            def __init__(self, file, pos, endian):
                (self.nameStrOffset,
                 self.dataOffset,
                 self.num,
                 self.type) = struct.unpack_from(f'{endian}2IHB', file, pos)

                self.name = readString(file, pos + self.nameStrOffset)
                self.data = []

                if self.dataOffset:
                    if self.type == 0:
                        tempPos = pos + self.dataOffset
                        for _ in range(self.num):
                            raw = _bf_read_cstr(file, tempPos)
                            tempPos += len(raw) + 1
                            self.data.append(RawString(raw.decode('utf-8', errors='replace'), raw))

                    elif self.type == 1:
                        self.data = struct.unpack_from(f'{endian}{self.num}i', file, pos + self.dataOffset)

                    elif self.type == 2:
                        self.data = struct.unpack_from(f'{endian}{self.num}f', file, pos + self.dataOffset)

        def __init__(self, file, pos, endian):
            super().__init__(file, pos, endian); pos += 8

            self.num, = struct.unpack_from(f'{endian}H', file, pos); pos += 4
            self.extUserData = []
            for i in range(self.num):
                self.extUserData.append(self.ExtUserData(file, pos + 12*i, endian))

    class Group(Section):
        def __init__(self, file, pos, major, endian):
            super().__init__(file, pos, endian); pos += 8

            if major < 5:
                fmt = f'{endian}24sH2x'
                size = 28

            else:
                fmt = f'{endian}33sxH'
                size = 36

            (nameBytes,
             self.paneNum) = struct.unpack_from(fmt, file, pos); pos += size

            self.name = readString(nameBytes)

            self.panes = []
            for i in range(self.paneNum):
                pane = readString(struct.unpack_from(f'{endian}24s', file, pos + 24*i)[0])
                self.panes.append(pane)

        def save(self, major, endian='>'):
            fmt = f'{endian}24sH2x' if major < 5 else f'{endian}33sxH'
            buff1 = struct.pack(
                fmt,
                self.name.encode('utf-8'),
                len(self.panes),
            )

            buff2 = bytearray()
            for pane in self.panes:
                buff2 += struct.pack(f'{endian}24s', pane.encode('utf-8'))

            self.data = b''.join([buff1, buff2])

            return super().save(endian)

    def __init__(self, file):
        # Determine endian from BOM
        if file[4:6] == b'\xFE\xFF':
            self.endian = '>'  # big-endian (Wii U)
        elif file[4:6] == b'\xFF\xFE':
            self.endian = '<'  # little-endian (Switch)
        else:
            raise NotImplementedError("Invalid BFLYT byte order mark")

        (self.magic,
         self.headSize,
         self.version,
         self.fileSize,
         self.numSections) = struct.unpack_from(f'{self.endian}4s2xH2IH', file)

        assert self.magic == b'FLYT'
        major = self.version >> 24
        if major not in [2, 3, 5]:
            print("Untested BFLYT version: %s\n" % hex(self.version))

        self.lyt = None
        self.cnt = None
        self.txl = None
        self.fnl = None
        self.mat = None

        rootPaneSet = False
        parent = None
        lastPane = None
        bReadRootGroup = False
        self.groupList = []
        groupNestLevel = 0

        self.rootPane = None
        self.sectionList = []

        pos = 0x14
        i = 0
        while i < self.numSections:
            if file[pos:pos + 4] == b'lyt1':
                self.lyt = self.Layout(file, pos, self.endian)
                self.sectionList.append(self.lyt)
                pos += self.lyt.blockHeader.size

            elif file[pos:pos + 4] == b'cnt1':
                self.cnt = self.Control(file, pos, major, self.endian)
                self.sectionList.append(self.cnt)
                pos += self.cnt.blockHeader.size

                if major > 2 and file[pos:pos + 4] == b'usd1':
                    self.cnt.extUserDataList = self.ExtUserDataList(file, pos, self.endian)
                    self.sectionList.append(self.cnt.extUserDataList)
                    pos += self.cnt.extUserDataList.blockHeader.size
                    i += 1

            elif file[pos:pos + 4] == b'txl1':
                self.txl = self.TextureList(file, pos, self.endian)
                self.sectionList.append(self.txl)
                pos += self.txl.blockHeader.size

            elif file[pos:pos + 4] == b'fnl1':
                self.fnl = self.FontList(file, pos, self.endian)
                self.sectionList.append(self.fnl)
                pos += self.fnl.blockHeader.size

            elif file[pos:pos + 4] == b'mat1':
                self.mat = self.MaterialList(file, pos, self.endian)
                self.sectionList.append(self.mat)
                pos += self.mat.blockHeader.size

            elif file[pos:pos + 4] in [b'pan1', b'pic1', b'txt1', b'wnd1', b'bnd1', b'prt1']:
                pane = readPane(file, pos, major, self.endian)
                self.sectionList.append(pane)

                if not rootPaneSet:
                    pane.isRootPane = rootPaneSet = True

                    # We don't need to add all panes to a list, since we can just get all panes from the root pane
                    self.rootPane = pane

                if parent:
                    parent.appendChild(pane)

                lastPane = pane
                pos += pane.blockHeader.size

                if file[pos:pos + 4] == b'usd1':
                    pane.extUserDataList = self.ExtUserDataList(file, pos, self.endian)
                    self.sectionList.append(pane.extUserDataList)
                    pos += pane.extUserDataList.blockHeader.size
                    i += 1

            elif file[pos:pos + 4] == b'pas1':
                assert lastPane is not None
                parent = lastPane

                section = Section(file, pos, self.endian)
                self.sectionList.append(section)
                pos += section.blockHeader.size

            elif file[pos:pos + 4] == b'pae1':
                lastPane = parent
                parent = lastPane.parent

                section = Section(file, pos, self.endian)
                self.sectionList.append(section)
                pos += section.blockHeader.size

            elif file[pos:pos + 4] == b'grp1':
                if not bReadRootGroup:
                    bReadRootGroup = True

                    section = Section(file, pos, self.endian)
                    self.sectionList.append(section)
                    pos += section.blockHeader.size

                elif groupNestLevel == 1:
                    group = self.Group(file, pos, major, self.endian)
                    self.sectionList.append(group)
                    pos += group.blockHeader.size

                    self.groupList.append(group)

            elif file[pos:pos + 4] == b'grs1':
                groupNestLevel += 1

                section = Section(file, pos, self.endian)
                self.sectionList.append(section)
                pos += section.blockHeader.size

            elif file[pos:pos + 4] == b'gre1':
                groupNestLevel -= 1

                section = Section(file, pos, self.endian)
                self.sectionList.append(section)
                pos += section.blockHeader.size

            else:
                section = Section(file, pos, self.endian)
                self.sectionList.append(section)
                pos += section.blockHeader.size

            i += 1

    def save_as_switch(self, output, dVersion):
        if self.endian != '>':
            raise NotImplementedError("Only Wii U big endian BFLYT conversion is supported")
        converted = []
        for section in self.sectionList:
            tag = section.blockHeader.magic
            if isinstance(section, self.Layout):
                converted.append((tag, _bf_serialize_layout_model(section)))
            elif isinstance(section, self.TextureList):
                converted.append((tag, _bf_serialize_texture_list_model(section)))
            elif isinstance(section, self.FontList):
                converted.append((tag, _bf_serialize_font_list_model(section)))
            elif isinstance(section, self.Control):
                converted.append((tag, section.save(dVersion >> 24, '<')[8:]))
            elif isinstance(section, self.Group):
                converted.append((tag, section.save(dVersion >> 24, '<')[8:]))
            else:
                converted.append(_bf_convert_section(tag, section.data))
        Path(output).write_bytes(_bf_write_file(converted, dVersion))


def toVersion(file, output, dVersion):
    if file[4:6] == b'\xFE\xFF' and dVersion == 0x08000000:
        _bflyteu_to_switch(file, output, dVersion)
        return
    if file[4:6] == b'\xFF\xFE' and struct.unpack_from('<I', file, 8)[0] == dVersion:
        Path(output).write_bytes(file)
        return

    # Determine endian from BOM
    if file[4:6] == b'\xFE\xFF':
        endian = '>'  # big-endian (Wii U)
    elif file[4:6] == b'\xFF\xFE':
        endian = '<'  # little-endian (Switch)
    else:
        raise NotImplementedError("Invalid BFLYT byte order mark")

    (magic,
     version,
     numSections) = struct.unpack_from(f'{endian}4s4xI4xH', file)

    assert magic == b'FLYT'

    file = bytearray(file)
    dMajor = dVersion >> 24
    major = version >> 24
    if major not in [2, 3, 5]:
        print("Untested BFLYT version: %s\n" % hex(version))

    pos = 0x14
    for _ in range(numSections):
        if file[pos:pos + 4] == b'cnt1':
            cnt = FLYT.Control(file, pos, major, endian)
            size = cnt.blockHeader.size

            file[pos:pos + size] = cnt.save(dMajor)
            pos += cnt.blockHeader.size

        elif file[pos:pos + 4] == b'grp1':
            group = FLYT.Group(file, pos, major, endian)
            size = group.blockHeader.size

            file[pos:pos + size] = group.save(dMajor)
            pos += group.blockHeader.size

        else:
            section = Section(file, pos, endian)
            pos += section.blockHeader.size

    file[:0x14] = struct.pack(
        '>4s2H2IH2x',
        b'FLYT',
        0xFEFF,
        20,
        dVersion,
        len(file),
        numSections,
    )

    with open(output, "wb") as out:
        out.write(file)


def main():
    file = input("Input (.bflyt):  ")
    output = input("Output (.bflyt):  ")
    version = int(input("Convert to version (e.g. 0x02020000):  "), 0)

    with open(file, "rb") as inf:
        inb = inf.read()

    toVersion(inb, output, version)


if __name__ == "__main__":
    main()
