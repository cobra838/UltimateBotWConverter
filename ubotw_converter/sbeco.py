from struct import Struct


MAGIC = 0x00112233
HEADER_SIZE = 0x10


def _read_u32(data: bytes, offset: int, endian: str) -> int:
    return int.from_bytes(data[offset : offset + 4], endian)


def _read_u16(data: bytes, offset: int, endian: str) -> int:
    return int.from_bytes(data[offset : offset + 2], endian)


def _detect_endianness(data: bytes) -> str:
    if len(data) < HEADER_SIZE:
        raise ValueError("BECO file is too small to contain a header")

    if _read_u32(data, 0, "big") == MAGIC:
        return "big"
    if _read_u32(data, 0, "little") == MAGIC:
        return "little"

    raise ValueError("Invalid BECO magic")


def _validate_layout(data: bytes, endian: str) -> int:
    num_rows = _read_u32(data, 0x4, endian)
    offsets_start = HEADER_SIZE
    rows_start = offsets_start + num_rows * 4

    if num_rows == 0:
        raise ValueError("BECO file has no rows")
    if rows_start > len(data):
        raise ValueError("BECO row offset table extends past the end of the file")
    if (len(data) - rows_start) % 2:
        raise ValueError("BECO row data is not aligned to 16-bit values")

    rows_units = (len(data) - rows_start) // 2
    last_offset = 0
    for index in range(num_rows):
        offset = _read_u32(data, offsets_start + index * 4, endian)
        if offset > rows_units:
            raise ValueError(f"BECO row offset {index} points past the row data")
        if offset < last_offset:
            raise ValueError(f"BECO row offset {index} is smaller than the previous offset")
        last_offset = offset

    return num_rows


def convert_to_little_endian(data: bytes) -> bytes:
    source_endian = _detect_endianness(data)
    num_rows = _validate_layout(data, source_endian)
    if source_endian == "little":
        return data

    offsets_start = HEADER_SIZE
    rows_start = offsets_start + num_rows * 4
    output = bytearray(len(data))
    u32_le = Struct("<I")
    u16_le = Struct("<H")

    for offset in range(0, HEADER_SIZE, 4):
        u32_le.pack_into(output, offset, _read_u32(data, offset, source_endian))

    for index in range(num_rows):
        offset = offsets_start + index * 4
        u32_le.pack_into(output, offset, _read_u32(data, offset, source_endian))

    for offset in range(rows_start, len(data), 2):
        u16_le.pack_into(output, offset, _read_u16(data, offset, source_endian))

    return bytes(output)
